r"""
Amorçage (backfill) du RAG (modules 2/3) depuis des documents historiques :
exports Confluence (PDF), anciens tickets Jira (PDF/Word/texte exportés
individuellement), user stories archivées (TXT/MD/DOCX/XLSX/PPTX).

Usage :
    cd backend
    ..\..\venv\Scripts\activate
    python scripts/seed_rag_from_documents.py --dir C:\chemin\vers\mes\docs
    python scripts/seed_rag_from_documents.py --dir ... --stage requirement

Ce que fait le script, par fichier :
    1. Extraction de texte (réutilise app/services/attachment_service.py —
       même code que pour les pièces jointes de session, aucune logique
       d'extraction dupliquée).
    2. DLP : masque le texte extrait (app/agents/dlp_masking.py) — un
       Confluence/Jira réel peut contenir de vrais noms/emails, contrairement
       aux données [FICTIF] des tests.
    3. Fait tourner RequirementAnalyzerAgent.analyze() (module 2, LLM RÉEL —
       même provider que l'app, cf. settings.LLM_PROVIDER) sur le texte
       extrait pour le structurer en RequirementAnalysis.
    4. Si --stage full (défaut) : enchaîne avec TestDesignAgent.design()
       (module 3) sur le RequirementAnalysis obtenu.
    5. Stocke chaque résultat comme une GenerationModel avec
       decision="accepted" directement (pas de revue humaine étape par étape
       ici — un ticket/user story historique déjà livré vaut acceptation),
       embedding calculé via rag_service.store_embedding (même fonction que
       le pipeline live, aucune logique dupliquée).

Pourquoi pas de SessionModel réel par document importé : GenerationModel.session_id
n'est PAS une clé étrangère SQLAlchemy (choix de conception assumé, cf.
app/models/session.py — "pas de relationship SQLAlchemy" sur AttachmentModel/
GenerationModel) et rag_service ne fait jamais de jointure vers SessionModel.
Créer un vrai SessionModel par document polluerait silencieusement les
analytics (module 7, app/services/analytics_service.py::get_summary lit TOUTES
les sessions sans filtre) : total_sessions, acceptance_rate et surtout
average_pipeline_duration_seconds (quasi nul pour une acceptation immédiate)
seraient faussés. Un session_id synthétique ("seed-<uuid>") sert donc de simple
identifiant traçable, jamais lu ailleurs que dans les logs de ce script.

Limites connues (best-effort, cf. CLAUDE.md règle 3 — jamais d'échec silencieux
mais jamais bloquant non plus) :
    - Un fichier = un document. Pas de parseur pour un export CSV/JSON
      contenant plusieurs tickets Jira à la fois (à ajouter séparément si
      besoin réel — export un ticket/une page à la fois pour l'instant).
    - Un document très volumineux (gros PDF Confluence multi-pages) peut
      dépasser la fenêtre de contexte du LLM local ou provoquer un timeout
      (OLLAMA_TIMEOUT_SECONDS) — échoue proprement (logué, fichier suivant),
      jamais de crash du script entier.
    - Coût réel : 1 à 2 appels LLM locaux par fichier (module 2, +module 3 en
      --stage full) — pour un petit modèle local (~60s/appel mesuré), prévoir
      plusieurs minutes pour un lot de quelques dizaines de documents.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.dlp_masking import get_masking_provider  # noqa: E402
from app.agents.embedding_layer import get_embedding_provider  # noqa: E402
from app.agents.requirement_analyzer import (  # noqa: E402
    RequirementAnalysisError,
    RequirementAnalyzerAgent,
)
from app.agents.test_design_agent import TestDesignAgent, TestDesignError  # noqa: E402
from app.database import get_session_factory  # noqa: E402
from app.models.session import GenerationModel  # noqa: E402
from app.schemas.requirement import RequirementAnalysisRequest  # noqa: E402
from app.services import attachment_service, rag_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seed_rag")

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_one(
    db,
    analyzer: RequirementAnalyzerAgent,
    designer: TestDesignAgent,
    embedder,
    masker,
    path: Path,
    include_test_design: bool,
) -> str:
    """Traite un fichier ; retourne un code résultat pour le récapitulatif
    final. Ne lève jamais — chaque échec possible est catégorisé et loggé."""
    try:
        raw_text = attachment_service.extract_document_text(path.name, path.read_bytes())
    except attachment_service.AttachmentExtractionError as exc:
        logger.warning("%s: extraction échouée (%s)", path.name, exc)
        return "extraction_failed"

    if not raw_text.strip():
        logger.warning("%s: aucun texte exploitable extrait (fichier vide ?)", path.name)
        return "extraction_failed"

    seed_session_id = f"seed-{uuid.uuid4()}"

    # Même principe que RequirementAnalyzerAgent.analyze() dans le pipeline
    # live : le texte brut lui est passé tel quel (il masque lui-même
    # request.user_input en interne) ; masked_text ci-dessous ne sert QUE
    # pour l'embedding RAG, cf. session_service.run_requirement_analysis.
    try:
        analysis = await analyzer.analyze(RequirementAnalysisRequest(user_input=raw_text))
    except RequirementAnalysisError as exc:
        logger.warning("%s: analyse module 2 échouée (%s)", path.name, exc)
        return "analysis_failed"

    masked_text = masker.mask(raw_text)

    req_generation = GenerationModel(
        session_id=seed_session_id,
        stage="requirement",
        attempt_no=0,
        content=analysis.model_dump(mode="json"),
        decision="accepted",
        decided_at=_utcnow(),
    )
    db.add(req_generation)
    await rag_service.store_embedding(db, req_generation, masked_text, embedder)

    outcome = "requirement_seeded"

    if include_test_design:
        try:
            design_output = await designer.design(analysis)
        except TestDesignError as exc:
            logger.warning("%s: conception module 3 échouée (%s) — requirement quand même conservé", path.name, exc)
            await db.commit()
            return "test_design_failed"

        design_generation = GenerationModel(
            session_id=seed_session_id,
            stage="test_design",
            attempt_no=0,
            content=design_output.model_dump(mode="json"),
            decision="accepted",
            decided_at=_utcnow(),
        )
        db.add(design_generation)
        await rag_service.store_embedding(db, design_generation, masked_text, embedder)
        outcome = "requirement_and_test_design_seeded"

    await db.commit()
    return outcome


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Amorce le RAG (modules 2/3) depuis des documents historiques (Confluence/Jira/user stories)."
    )
    parser.add_argument("--dir", required=True, help="Dossier contenant les documents à importer.")
    parser.add_argument(
        "--stage",
        choices=["requirement", "full"],
        default="full",
        help="'requirement' : alimente uniquement le pool few-shot du module 2. "
        "'full' (défaut) : module 2 PUIS module 3 (2 appels LLM par fichier).",
    )
    args = parser.parse_args()

    folder = Path(args.dir)
    if not folder.is_dir():
        raise SystemExit(f"Dossier introuvable : {folder}")

    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS)
    if not files:
        raise SystemExit(
            f"Aucun fichier supporté dans {folder} (extensions attendues : {sorted(_SUPPORTED_EXTENSIONS)})"
        )

    print(f"{len(files)} fichier(s) trouvé(s) dans {folder} — stage={args.stage}")

    embedder = get_embedding_provider()
    masker = get_masking_provider()
    analyzer = RequirementAnalyzerAgent()
    designer = TestDesignAgent()
    factory = get_session_factory()
    include_test_design = args.stage == "full"

    tally: dict[str, int] = {}
    for path in files:
        async with factory() as db:
            outcome = await _seed_one(db, analyzer, designer, embedder, masker, path, include_test_design)
        tally[outcome] = tally.get(outcome, 0) + 1
        print(f"[{outcome}] {path.name}")

    print("---")
    for outcome, count in sorted(tally.items()):
        print(f"{outcome}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
