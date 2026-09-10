"""
Module 4 (extension) — RAG : récupération d'exemples few-shot pour les
modules 2 (analyse de besoin) ET 3 (conception de test, depuis le
23/07/2026), à partir des générations passées acceptées par un humain
(GenerationModel, app/models/session.py — voir sa docstring pour le cycle
de vie pending/accepted/rejected/failed). Générique sur `stage`
("requirement" | "test_design") : aucune logique spécifique à un module
dans ce fichier, toute la spécificité (quel texte de requête, quel prompt
d'injection) vit côté appelant (session_service.py / *_agent.py).

Approche : similarité cosinus calculée en Python sur des embeddings stockés
en JSON (generations.embedding) — recherche vectorielle "brute force",
volontairement sans sqlite-vec/pgvector/FAISS. Acceptable au volume d'un
prototype (dizaines à centaines de sessions, cf. absence de pagination déjà
assumée aux modules 4/7) ; à revoir avec un vrai index vectoriel si le
volume grandit significativement.

Best-effort partout : un échec (Ollama down, modèle d'embedding absent,
historique vide) ne casse jamais l'analyse/la conception elle-même — juste
moins/pas d'exemples few-shot. Appelé exclusivement depuis les tâches de
fond de session_service.py (run_requirement_analysis, run_test_design),
déjà hors du chemin critique de réponse HTTP.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.embedding_layer import BaseEmbeddingProvider, EmbeddingProviderError
from app.config import settings
from app.models.session import GenerationModel

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def store_embedding(
    db: AsyncSession,
    generation: GenerationModel,
    text: str,
    embedder: BaseEmbeddingProvider,
) -> None:
    """
    Calcule et attache un embedding à `generation` (mutation in-place, pas
    de commit ici — à la charge de l'appelant). Ne lève jamais : un échec
    d'embedding est une dégradation silencieuse (pas d'enrichissement futur
    pour cette génération), pas une raison de casser l'appelant.
    """
    try:
        generation.embedding = await embedder.embed(text)
    except EmbeddingProviderError as exc:
        logger.info("RAG: embedding non stocké pour generation %s (%s)", generation.id, exc)
    except Exception:
        logger.exception("RAG: erreur inattendue lors du calcul d'embedding (ignorée)")


async def get_few_shot_examples(
    db: AsyncSession,
    query_text: str,
    embedder: BaseEmbeddingProvider,
    stage: str,
    k: Optional[int] = None,
    min_similarity: Optional[float] = None,
) -> list[dict]:
    """
    Retourne jusqu'à `k` `content` de générations passées 'accepted' pour
    `stage`, sémantiquement proches de `query_text` (similarité cosinus >=
    `min_similarity`), triées de la plus proche à la moins proche.

    Liste vide (jamais d'exception) si : embedding indisponible (Ollama down,
    modèle absent), aucun historique 'accepted' avec embedding, ou aucun
    candidat au-dessus du seuil de similarité — cas attendu et fréquent en
    début de vie du projet (peu de sessions validées).
    """
    k = k if k is not None else settings.RAG_TOP_K
    min_similarity = min_similarity if min_similarity is not None else settings.RAG_MIN_SIMILARITY

    try:
        query_embedding = await embedder.embed(query_text)
    except EmbeddingProviderError as exc:
        logger.info("RAG: pas d'exemples few-shot (embedding indisponible: %s)", exc)
        return []
    except Exception:
        logger.exception("RAG: erreur inattendue lors de l'embedding de la requête (ignorée)")
        return []

    result = await db.execute(
        select(GenerationModel).where(
            GenerationModel.stage == stage,
            GenerationModel.decision == "accepted",
            GenerationModel.embedding.is_not(None),
        )
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return []

    scored = [(c, _cosine_similarity(query_embedding, c.embedding)) for c in candidates]
    scored = [(c, score) for c, score in scored if score >= min_similarity]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    return [c.content for c, _ in scored[:k]]


__all__ = ["store_embedding", "get_few_shot_examples"]
