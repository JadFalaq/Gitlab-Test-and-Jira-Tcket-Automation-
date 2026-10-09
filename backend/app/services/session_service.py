"""
Module 4 — Interface de validation : orchestration

Fait vivre une session à travers le pipeline module 2 -> module 3 ->
validation humaine. Traitement asynchrone : chaque appel LLM (modules 2/3)
tourne en tâche de fond (FastAPI BackgroundTasks, voir run_*_task ci-dessous)
pour ne jamais bloquer la requête HTTP dessus — un appel réel (Ollama) peut
prendre de quelques dizaines de secondes à plusieurs minutes (mesuré : 60s
pour le module 2, jusqu'à 268s pour le module 3 sur un test réel, à 30s du
timeout par défaut OLLAMA_TIMEOUT_SECONDS=300 — cf.
docs/module_3_test_design_agent.md). Chaque route qui déclenche un appel LLM
répond donc immédiatement avec un statut "en cours" ; le client doit
re-interroger GET /api/sessions/{id} jusqu'à en sortir (polling, cf.
frontend/src/App.tsx).

Machine à états (SessionModel.status) :

    (create) --> chatting --[finalize_chat]--> analyzing --[analyse OK]--> pending_requirement_review
                        |--[analyse échoue]--> requirement_failed (retry via regenerate -> analyzing)

    pending_requirement_review --accept--> designing_tests --[design OK]--> pending_test_review
            |--reject--> requirement_rejected        |--[design échoue]--> test_design_failed (retry via regenerate -> designing_tests)

    pending_test_review --accept--> validated
            |--reject--> tests_rejected

    validated --create_tickets--> tickets_created
            |--create_tickets (échec total)--> ticket_creation_failed (retry via create_tickets)

"reject" est terminal : pas de résurrection, l'ingénieur recrée une session.
"tickets_created" n'est pas terminal au sens strict : create_tickets reste
appelable (ex: ajouter un provider configuré après coup) — module 5.

"chatting" (module 9, 22/07/2026) : phase de clarification conversationnelle
libre AVANT tout appel au module 2 — create_session y place directement la
session (plus aucun appel LLM synchrone caché dans le kickoff). send_chat_message
ajoute un tour de conversation (reste en 'chatting', appel LLM synchrone : une
réponse conversationnelle est rapide, contrairement aux modules 2/3, cf.
docstring de send_chat_message). finalize_chat est le seul moyen de sortir de
'chatting' vers 'analyzing' — kickoff classique, le transcript complet de la
conversation devient le `context` envoyé au module 2 (cf. run_requirement_analysis).

Chaque transition qui lance un appel LLM est scindée en deux fonctions :
- une fonction "kickoff" synchrone (create_session, accept_requirement,
  regenerate_requirement, regenerate_tests) qui valide l'état, positionne le
  statut "en cours" et committe immédiatement — retour rapide au client ;
- une fonction run_requirement_analysis / run_test_design qui exécute
  effectivement l'appel LLM, appelée par le router via BackgroundTasks (à
  travers un wrapper run_*_task qui ouvre sa propre AsyncSession, celle de
  la requête étant fermée à ce moment-là — cf. app/database.py
  get_session_factory). Ces fonctions ne lèvent jamais : toute exception,
  même inattendue (pas seulement RequirementAnalysisError/TestDesignError),
  est convertie en statut '_failed' + error_message, pour ne jamais laisser
  une session bloquée en 'analyzing'/'designing_tests' indéfiniment (règle 3
  CLAUDE.md — critique ici car il n'existe plus de réponse HTTP pour
  remonter l'erreur au client une fois la tâche de fond lancée).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.chat_agent import ChatAgent, ChatAgentError
from app.agents.dlp_masking import get_masking_provider
from app.agents.embedding_layer import BaseEmbeddingProvider, get_embedding_provider
from app.agents.requirement_analyzer import RequirementAnalysisError, RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent, TestDesignError
from app.agents.vision_layer import BaseVisionProvider, get_vision_provider
from app.config import settings
from app.models.session import GenerationModel, SessionModel
from app.schemas.attachment import UploadedFile
from app.schemas.requirement import RequirementAnalysis, RequirementAnalysisRequest
from app.schemas.session import ChatMessage, SessionCreateRequest
from app.schemas.test_design import TestDesignOutput
from app.services import attachment_service, rag_service, ticket_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SessionNotFoundError(Exception):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' introuvable")


class InvalidSessionTransitionError(Exception):
    """L'action demandée n'est pas valide dans l'état actuel de la session."""

    def __init__(self, message: str, current_status: str):
        self.current_status = current_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

async def _get_or_404(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """
    Module 8 — charge une session en vérifiant qu'elle appartient bien à
    `user_id` : une session existante mais appartenant à un autre assigné
    lève SessionNotFoundError (pas un 403) pour ne pas révéler son existence
    — même logique que attachment_service.get_attachment pour les pièces
    jointes cross-session. Utilisée par toutes les fonctions déclenchées
    directement par une requête HTTP (kickoff synchrone).

    Les fonctions exécutées en tâche de fond (run_requirement_analysis,
    run_test_design) utilisent _get_by_id_or_404 ci-dessous à la place :
    l'ownership a déjà été vérifiée au kickoff, par la même requête HTTP qui
    a programmé la tâche de fond (cf. app/routers/sessions.py).
    """
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id, SessionModel.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise SessionNotFoundError(session_id)
    return session


async def _get_by_id_or_404(db: AsyncSession, session_id: str) -> SessionModel:
    """Chargement interne SANS vérification d'ownership — réservé aux
    fonctions de tâche de fond (cf. docstring de _get_or_404 ci-dessus)."""
    session = await db.get(SessionModel, session_id)
    if session is None:
        raise SessionNotFoundError(session_id)
    return session


async def _with_attachments(db: AsyncSession, session: SessionModel) -> SessionModel:
    """
    Attache la liste des pièces jointes de la session à l'objet ORM, en
    attribut Python ad-hoc — ce projet n'utilise aucune relationship
    SQLAlchemy nulle part (cf. GenerationModel, jamais accédé via
    `session.generations`), toujours des requêtes explicites. Nécessaire
    pour que SessionResponse.attachments (Pydantic, from_attributes=True)
    trouve quelque chose : `attachments` n'est pas une colonne de
    SessionModel, contrairement à requirement_analysis/tickets/etc.

    À appeler en tout dernier, juste avant de retourner une session vouée à
    devenir une réponse HTTP (chaque endpoint de app/routers/sessions.py et
    tickets.py) — jamais dans _get_or_404 (appelé aussi pour de simples
    vérifications d'état internes, sans avoir besoin de cette requête
    supplémentaire à chaque fois).
    """
    session.attachments = await attachment_service.get_session_attachments(db, session.id)
    return session


async def _record_generation(
    db: AsyncSession,
    session: SessionModel,
    stage: str,
    attempt_no: int,
    content: Optional[dict],
    error_message: Optional[str],
) -> GenerationModel:
    """Enregistre le résultat d'un run_requirement_analysis/run_test_design
    (succès ou échec) — voir GenerationModel pour la sémantique complète.
    Retourne l'objet créé (utilisé par run_requirement_analysis pour y
    attacher un embedding RAG, cf. store_embedding)."""
    generation = GenerationModel(
        session_id=session.id,
        stage=stage,
        attempt_no=attempt_no,
        content=content,
        decision="failed" if content is None else "pending",
        error_message=error_message,
    )
    db.add(generation)
    return generation


async def _mark_pending_generation(db: AsyncSession, session_id: str, stage: str, decision: str) -> None:
    """
    Marque la génération 'pending' la plus récente d'une session/étape avec
    la décision humaine ('accepted' ou 'rejected'). No-op silencieux si
    aucune génération 'pending' n'existe (ex: regenerate_* appelé depuis un
    état '_failed', où la dernière génération est déjà 'failed', pas
    'pending' — rien à marquer).
    """
    result = await db.execute(
        select(GenerationModel)
        .where(
            GenerationModel.session_id == session_id,
            GenerationModel.stage == stage,
            GenerationModel.decision == "pending",
        )
        .order_by(GenerationModel.attempt_no.desc())
        .limit(1)
    )
    generation = result.scalar_one_or_none()
    if generation is not None:
        generation.decision = decision
        generation.decided_at = datetime.now(timezone.utc)


async def _run_requirement_analysis(
    session: SessionModel,
    analyzer: RequirementAnalyzerAgent,
    examples: Optional[list[dict]] = None,
    context_override: Optional[str] = None,
) -> None:
    try:
        analysis = await analyzer.analyze(
            RequirementAnalysisRequest(
                user_input=session.raw_input,
                context=context_override if context_override is not None else session.context,
            ),
            examples=examples,
        )
        session.requirement_analysis = analysis.model_dump(mode="json")
        session.status = "pending_requirement_review"
        session.error_message = None
    except RequirementAnalysisError as exc:
        session.status = "requirement_failed"
        session.error_message = str(exc)
        logger.warning("Session %s: requirement analysis failed: %s", session.id, exc)


async def _run_test_design(
    session: SessionModel,
    designer: TestDesignAgent,
    guidance: Optional[str] = None,
    examples: Optional[list[dict]] = None,
) -> None:
    requirement = RequirementAnalysis(**session.requirement_analysis)
    try:
        output = await designer.design(requirement, guidance=guidance, examples=examples)
        session.test_design_output = output.model_dump(mode="json")
        session.status = "pending_test_review"
        session.error_message = None
    except TestDesignError as exc:
        session.status = "test_design_failed"
        session.error_message = str(exc)
        logger.warning("Session %s: test design failed: %s", session.id, exc)


# ---------------------------------------------------------------------------
# API du service
# ---------------------------------------------------------------------------

async def create_session(
    db: AsyncSession,
    payload: SessionCreateRequest,
    user_id: str,
    files: Optional[list[UploadedFile]] = None,
) -> SessionModel:
    """
    Crée une session en état 'chatting' et la persiste immédiatement, pour
    le compte de `user_id` (module 8 — chaque assigné ne voit que ses
    propres sessions, cf. _get_or_404/list_sessions).

    Module 9 (22/07/2026) : le module 2 n'est PLUS lancé automatiquement ici
    — la session s'ouvre sur une phase de clarification conversationnelle
    libre (cf. send_chat_message/finalize_chat ci-dessous), jamais de
    résultat structuré + bouton de validation présentés en une seule
    interaction. Le premier message de l'ingénieur (`payload.user_input`)
    est dupliqué comme premier tour de `chat_history`.

    `files` (pièces jointes, optionnel) : chaque fichier est écrit sur
    disque immédiatement (rapide, cf. app/services/attachment_service.py) —
    l'extraction de texte/description d'image, potentiellement lente, n'a
    lieu que dans run_requirement_analysis (tâche de fond, déclenchée par
    finalize_chat).
    """
    session = SessionModel(
        user_id=user_id,
        status="chatting",
        raw_input=payload.user_input,
        context=payload.context,
        chat_history=[
            ChatMessage(role="user", content=payload.user_input, created_at=datetime.now(timezone.utc)).model_dump(
                mode="json"
            )
        ],
    )
    db.add(session)
    await db.flush()  # attribue session.id (uuid par défaut) avant d'y rattacher des pièces jointes

    for uploaded in files or []:
        await attachment_service.save_attachment(
            db, session.id, uploaded.filename, uploaded.content_type, uploaded.content
        )

    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def send_chat_message(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    message: str,
    chat_agent: ChatAgent,
) -> SessionModel:
    """
    Module 9 — ajoute un tour de conversation (message de l'ingénieur +
    réponse de ChatAgent) à une session encore en 'chatting'.

    Appel LLM SYNCHRONE (contrairement aux modules 2/3, jamais de
    BackgroundTasks ici) : une réponse conversationnelle est rapide, et
    l'UX d'un chat attend naturellement une réponse avant de continuer —
    cf. docstring de tête du module.

    Rien n'est persisté si l'appel LLM échoue (ni le message de l'ingénieur,
    ni de réponse partielle) : l'ingénieur peut renvoyer son message sans
    risque de doublon. ChatAgentError remonte telle quelle, convertie en
    réponse HTTP par le gestionnaire enregistré dans app/main.py (même
    principe que SessionNotFoundError/InvalidSessionTransitionError).
    """
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "chatting":
        raise InvalidSessionTransitionError(
            f"Impossible d'envoyer un message depuis l'état '{session.status}'", session.status
        )

    history = [ChatMessage(**m) for m in session.chat_history]
    history.append(ChatMessage(role="user", content=message, created_at=datetime.now(timezone.utc)))

    reply = await chat_agent.reply(history)  # ChatAgentError se propage si échec, rien n'est persisté

    history.append(ChatMessage(role="assistant", content=reply, created_at=datetime.now(timezone.utc)))
    session.chat_history = [m.model_dump(mode="json") for m in history]

    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def finalize_chat(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """
    Module 9 — sort de 'chatting' vers 'analyzing' (kickoff) : le module 2
    est lancé en tâche de fond par le router (run_requirement_analysis_task),
    avec le transcript complet de la conversation comme `context` (cf.
    run_requirement_analysis, qui le concatène comme il le fait déjà pour
    les pièces jointes/guidance — même principe, un seul point d'entrée
    masqué par le DLP).
    """
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "chatting":
        raise InvalidSessionTransitionError(
            f"Impossible de générer l'analyse depuis l'état '{session.status}'", session.status
        )
    session.status = "analyzing"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


def _render_chat_transcript(chat_history: list[dict]) -> Optional[str]:
    """Rend l'historique de conversation en texte lisible, à concaténer au
    `context` envoyé au module 2 (cf. run_requirement_analysis). None si
    l'historique ne contient que le tout premier message (déjà couvert par
    `raw_input`, pas la peine de le dupliquer dans le contexte)."""
    if len(chat_history) <= 1:
        return None
    speaker = {"user": "Ingénieur", "assistant": "Assistant"}
    lines = [f"[{speaker.get(m['role'], m['role'])}] {m['content']}" for m in chat_history]
    return "Conversation de clarification :\n" + "\n".join(lines)


async def run_requirement_analysis(
    db: AsyncSession,
    session_id: str,
    analyzer: RequirementAnalyzerAgent,
    embedder: Optional[BaseEmbeddingProvider] = None,
    vision_provider: Optional[BaseVisionProvider] = None,
    guidance: Optional[str] = None,
) -> SessionModel:
    """
    Exécute effectivement le module 2 pour une session déjà en 'analyzing',
    persiste le résultat (pending_requirement_review / requirement_failed).

    `guidance` (optionnel) : instruction libre de l'ingénieur pour CET appel
    précis (cf. ReviewActions.tsx côté frontend), ex: "précise le critère de
    performance". Jamais stockée sur `session.context` (transitoire, propre
    à cette régénération) — simplement concaténée au contexte envoyé au LLM
    pour cet appel, donc masquée par le DLP au même titre que le reste (même
    principe que les pièces jointes ci-dessous).

    Module 9 — transcript de conversation : si `session.chat_history`
    contient plus que le tout premier message (l'ingénieur a discuté avant
    de cliquer "Générer l'analyse", cf. finalize_chat), le transcript
    complet est concaténé au contexte, avant les pièces jointes — c'est
    souvent la source la plus riche de clarifications.

    Pièces jointes : chaque pièce jointe pas encore traitée (extracted_text
    et extraction_error tous deux NULL — donc jamais retraitée à un
    regenerate_requirement suivant) est extraite (document) ou décrite
    (image, cf. app/agents/vision_layer.py). Le texte obtenu est concaténé
    au `context` de la session pour CET appel — session.context lui-même
    n'est jamais modifié, seul ce qui part au LLM l'est. Ce contexte élargi
    passe par le DLP exactement comme le `context` saisi par l'ingénieur
    (même champ, même masquage dans RequirementAnalyzerAgent.analyze) — pas
    de logique de masquage dupliquée pour les pièces jointes.

    RAG (few-shot) : avant l'analyse, retrouve jusqu'à settings.RAG_TOP_K
    générations 'accepted' passées sémantiquement proches (cf.
    app/services/rag_service.py) et les injecte comme exemples dans le
    prompt du module 2. Après l'analyse (succès ou échec), calcule et
    stocke l'embedding de CETTE génération pour de futures recherches — sur
    le texte masqué (DLP), pas le texte brut, par cohérence avec ce qui est
    réellement envoyé au LLM (aucun envoi externe supplémentaire : Ollama
    est déjà le provider d'embedding, en local). Entièrement best-effort :
    un Ollama sans le modèle d'embedding tiré (`ollama pull
    nomic-embed-text`) fait juste tourner l'analyse sans exemples few-shot,
    jamais d'échec de session pour cette raison.

    Ne lève jamais (cf. docstring de tête du module).
    """
    session = await _get_by_id_or_404(db, session_id)
    embedder = embedder or get_embedding_provider()
    vision_provider = vision_provider or get_vision_provider()

    attachments = await attachment_service.get_session_attachments(db, session_id)
    for attachment in attachments:
        if attachment.extracted_text is None and attachment.extraction_error is None:
            await attachment_service.process_attachment(attachment, vision_provider)
    attachments_context = attachment_service.build_attachments_context(attachments)
    combined_context = session.context or None
    transcript = _render_chat_transcript(session.chat_history)
    if transcript:
        combined_context = f"{combined_context}\n\n{transcript}" if combined_context else transcript
    if attachments_context:
        combined_context = (
            f"{combined_context}\n\n{attachments_context}" if combined_context else attachments_context
        )
    if guidance:
        guidance_block = f"Instruction spécifique de l'ingénieur pour cette régénération : {guidance}"
        combined_context = f"{combined_context}\n\n{guidance_block}" if combined_context else guidance_block

    examples: list[dict] = []
    masked_query = get_masking_provider().mask(session.raw_input) if settings.RAG_ENABLED else session.raw_input
    if settings.RAG_ENABLED:
        examples = await rag_service.get_few_shot_examples(
            db, masked_query, embedder, stage="requirement"
        )

    try:
        await _run_requirement_analysis(
            session, analyzer, examples=examples, context_override=combined_context
        )
    except Exception:
        logger.exception("Session %s: échec inattendu de l'analyse", session.id)
        session.status = "requirement_failed"
        session.error_message = "Erreur interne inattendue pendant l'analyse (voir logs serveur)."

    generation = await _record_generation(
        db,
        session,
        stage="requirement",
        attempt_no=session.requirement_regenerations,
        content=session.requirement_analysis,
        error_message=session.error_message,
    )
    if settings.RAG_ENABLED and generation.content is not None:
        await rag_service.store_embedding(db, generation, masked_query, embedder)

    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def run_requirement_analysis_task(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    analyzer: RequirementAnalyzerAgent,
    guidance: Optional[str] = None,
) -> None:
    """
    Wrapper enregistré via FastAPI BackgroundTasks (app/routers/sessions.py) :
    ouvre sa propre AsyncSession puis délègue à run_requirement_analysis.
    """
    async with session_factory() as db:
        await run_requirement_analysis(db, session_id, analyzer, guidance=guidance)


async def get_session(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    session = await _get_or_404(db, session_id, user_id)
    return await _with_attachments(db, session)


async def list_sessions(db: AsyncSession, user_id: str) -> list[SessionModel]:
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.created_at.desc())
    )
    return list(result.scalars().all())


async def regenerate_requirement(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """Repasse en 'analyzing' (kickoff) — le module 2 est relancé en tâche de
    fond par le router, voir run_requirement_analysis_task."""
    session = await _get_or_404(db, session_id, user_id)
    if session.status not in ("pending_requirement_review", "requirement_failed"):
        raise InvalidSessionTransitionError(
            f"Impossible de régénérer l'analyse depuis l'état '{session.status}'", session.status
        )
    # Rejet implicite : régénérer, c'est renoncer à la génération actuelle
    # (si elle était encore 'pending') au profit d'une nouvelle tentative.
    await _mark_pending_generation(db, session_id, stage="requirement", decision="rejected")
    session.requirement_regenerations += 1
    session.status = "analyzing"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def reject_requirement(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "pending_requirement_review":
        raise InvalidSessionTransitionError(
            f"Impossible de rejeter l'analyse depuis l'état '{session.status}'", session.status
        )
    await _mark_pending_generation(db, session_id, stage="requirement", decision="rejected")
    session.status = "requirement_rejected"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def accept_requirement(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """
    Valide l'analyse et passe en 'designing_tests' (kickoff) — le module 3
    est lancé en tâche de fond par le router, voir run_test_design_task.
    """
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "pending_requirement_review":
        raise InvalidSessionTransitionError(
            f"Impossible de valider l'analyse depuis l'état '{session.status}'", session.status
        )
    await _mark_pending_generation(db, session_id, stage="requirement", decision="accepted")
    session.status = "designing_tests"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def run_test_design(
    db: AsyncSession,
    session_id: str,
    designer: TestDesignAgent,
    embedder: Optional[BaseEmbeddingProvider] = None,
    guidance: Optional[str] = None,
) -> SessionModel:
    """
    Exécute effectivement le module 3 pour une session déjà en
    'designing_tests', persiste le résultat (pending_test_review /
    test_design_failed). Ne lève jamais (cf. docstring de tête du module).

    `guidance` (optionnel) : cf. docstring de run_requirement_analysis —
    même principe, transmis tel quel à TestDesignAgent.design().

    RAG (few-shot) : même principe que run_requirement_analysis (cf. sa
    docstring pour le détail complet du mécanisme best-effort) — récupère
    jusqu'à settings.RAG_TOP_K conceptions de test 'accepted' passées,
    sémantiquement proches, et les injecte comme exemples de format dans le
    prompt de TestDesignAgent. Après génération, stocke l'embedding de
    CETTE génération pour de futures recherches (uniquement si
    settings.RAG_ENABLED et si la génération a produit un résultat).

    Texte de requête (identique à celui du module 2, volontairement) : le
    `raw_input` de LA SESSION COURANTE, masqué DLP — pas le
    RequirementAnalysis ni ses champs. Deux raisons : (1) cohérence avec le
    pipeline de masquage déjà exercé au module 2, pas de logique de masquage
    dupliquée pour un nouveau type de texte ; (2) une conception de test
    'accepted' est ainsi indexée sur la DEMANDE D'ORIGINE qui y a mené —
    une future session dont la demande brute est similaire retrouve
    naturellement cette conception, sans dépendre de la formulation exacte
    que le LLM a donnée à l'analyse structurée intermédiaire.
    """
    session = await _get_by_id_or_404(db, session_id)
    embedder = embedder or get_embedding_provider()

    examples: list[dict] = []
    masked_query = get_masking_provider().mask(session.raw_input) if settings.RAG_ENABLED else session.raw_input
    if settings.RAG_ENABLED:
        examples = await rag_service.get_few_shot_examples(
            db, masked_query, embedder, stage="test_design"
        )

    try:
        await _run_test_design(session, designer, guidance=guidance, examples=examples)
    except Exception:
        logger.exception("Session %s: échec inattendu de la conception de tests", session.id)
        session.status = "test_design_failed"
        session.error_message = "Erreur interne inattendue pendant la conception de tests (voir logs serveur)."

    generation = await _record_generation(
        db,
        session,
        stage="test_design",
        attempt_no=session.test_regenerations,
        content=session.test_design_output,
        error_message=session.error_message,
    )
    if settings.RAG_ENABLED and generation.content is not None:
        await rag_service.store_embedding(db, generation, masked_query, embedder)

    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def run_test_design_task(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    designer: TestDesignAgent,
    guidance: Optional[str] = None,
) -> None:
    """Wrapper BackgroundTasks — même principe que run_requirement_analysis_task."""
    async with session_factory() as db:
        await run_test_design(db, session_id, designer, guidance=guidance)


async def regenerate_tests(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """Repasse en 'designing_tests' (kickoff) — le module 3 est relancé en
    tâche de fond par le router, voir run_test_design_task."""
    session = await _get_or_404(db, session_id, user_id)
    if session.status not in ("pending_test_review", "test_design_failed"):
        raise InvalidSessionTransitionError(
            f"Impossible de régénérer les tests depuis l'état '{session.status}'", session.status
        )
    await _mark_pending_generation(db, session_id, stage="test_design", decision="rejected")
    session.test_regenerations += 1
    session.status = "designing_tests"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def reject_tests(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "pending_test_review":
        raise InvalidSessionTransitionError(
            f"Impossible de rejeter les tests depuis l'état '{session.status}'", session.status
        )
    await _mark_pending_generation(db, session_id, stage="test_design", decision="rejected")
    session.status = "tests_rejected"
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def accept_tests(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    session = await _get_or_404(db, session_id, user_id)
    if session.status != "pending_test_review":
        raise InvalidSessionTransitionError(
            f"Impossible de valider les tests depuis l'état '{session.status}'", session.status
        )
    await _mark_pending_generation(db, session_id, stage="test_design", decision="accepted")
    session.status = "validated"
    session.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


# ---------------------------------------------------------------------------
# Module 5 — Intégration Jira
# ---------------------------------------------------------------------------


async def create_tickets(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """
    Crée les tickets Jira (un par user story).

    Appelable depuis 'validated' ou 'ticket_creation_failed' (retry). Passe en
    'ticket_creation_failed' seulement si AUCUN ticket n'a pu être créé —
    sinon 'tickets_created', même avec des erreurs partielles (visibles dans
    error_message).

    Tous les comptes utilisateur (module 8) partagent le même compte de
    service Jira (settings.JIRA_*) — `user_id` ne sert ici qu'à vérifier
    l'ownership de la session.
    """
    session = await _get_or_404(db, session_id, user_id)
    if session.status not in ("validated", "ticket_creation_failed"):
        raise InvalidSessionTransitionError(
            f"Impossible de créer les tickets depuis l'état '{session.status}'", session.status
        )

    test_design = TestDesignOutput(**session.test_design_output)
    result = await ticket_service.create_tickets(session_id=session.id, test_design=test_design)

    existing = list(session.tickets or [])
    session.tickets = existing + [t.model_dump(mode="json") for t in result.tickets]

    if result.tickets:
        session.status = "tickets_created"
        session.error_message = "; ".join(result.errors) if result.errors else None
    else:
        session.status = "ticket_creation_failed"
        session.error_message = "; ".join(result.errors) or "Aucun ticket créé (raison inconnue)"

    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)


async def refresh_tickets(db: AsyncSession, session_id: str, user_id: str) -> SessionModel:
    """Rafraîchit le statut de tous les tickets déjà créés pour cette session (module 6)."""
    session = await _get_or_404(db, session_id, user_id)
    if not session.tickets:
        return await _with_attachments(db, session)

    session.tickets = await ticket_service.refresh_ticket_statuses(session.tickets)
    await db.commit()
    await db.refresh(session)
    return await _with_attachments(db, session)
