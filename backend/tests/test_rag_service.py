"""
Tests unitaires — Module 4 (extension) : RAG (app/services/rag_service.py)

Utilise un embedder factice à vecteurs 2D contrôlés (pas de dépendance à un
vrai Ollama) pour tester le ranking par similarité cosinus de façon
déterministe. La sémantique réelle (Ollama + nomic-embed-text) est vérifiée
manuellement (cf. docs/module_2_requirement_analyzer.md).

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.agents.embedding_layer import BaseEmbeddingProvider, EmbeddingProviderError
from app.models.session import GenerationModel
from app.services import rag_service


class _FakeEmbedder(BaseEmbeddingProvider):
    """Vecteurs pré-définis par texte exact — permet de contrôler
    précisément quel candidat doit ressortir en tête du ranking."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    @property
    def provider_name(self) -> str:
        return "fake"

    async def embed(self, text: str) -> list[float]:
        if text not in self._vectors:
            raise EmbeddingProviderError(f"Aucun vecteur factice pour {text!r}", provider="fake")
        return self._vectors[text]


def _generation(
    session_id: str = "s1",
    stage: str = "requirement",
    decision: str = "accepted",
    embedding: list[float] | None = None,
    content: dict | None = None,
) -> GenerationModel:
    return GenerationModel(
        session_id=session_id,
        stage=stage,
        attempt_no=0,
        content=content or {"title": "[FICTIF] contenu"},
        decision=decision,
        embedding=embedding,
    )


class TestGetFewShotExamples:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_history(self, db_session):
        embedder = _FakeEmbedder({"query": [1.0, 0.0]})

        examples = await rag_service.get_few_shot_examples(db_session, "query", embedder, stage="requirement")

        assert examples == []

    @pytest.mark.asyncio
    async def test_returns_closest_accepted_generation_first(self, db_session):
        close = _generation(embedding=[1.0, 0.0], content={"title": "[FICTIF] proche"})
        far = _generation(embedding=[0.0, 1.0], content={"title": "[FICTIF] loin"})
        db_session.add_all([close, far])
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [0.9, 0.1]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", min_similarity=0.0
        )

        assert examples[0]["title"] == "[FICTIF] proche"

    @pytest.mark.asyncio
    async def test_ignores_non_accepted_generations(self, db_session):
        for decision in ("pending", "rejected", "failed"):
            db_session.add(_generation(decision=decision, embedding=[1.0, 0.0]))
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [1.0, 0.0]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", min_similarity=0.0
        )

        assert examples == []

    @pytest.mark.asyncio
    async def test_ignores_generations_without_embedding(self, db_session):
        db_session.add(_generation(embedding=None))
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [1.0, 0.0]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", min_similarity=0.0
        )

        assert examples == []

    @pytest.mark.asyncio
    async def test_ignores_other_stage(self, db_session):
        db_session.add(_generation(stage="test_design", embedding=[1.0, 0.0]))
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [1.0, 0.0]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", min_similarity=0.0
        )

        assert examples == []

    @pytest.mark.asyncio
    async def test_respects_min_similarity_threshold(self, db_session):
        # Orthogonal (similarité cosinus = 0) : sous le seuil par défaut (0.5)
        db_session.add(_generation(embedding=[0.0, 1.0]))
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [1.0, 0.0]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", min_similarity=0.5
        )

        assert examples == []

    @pytest.mark.asyncio
    async def test_respects_k_limit(self, db_session):
        for i in range(5):
            db_session.add(_generation(embedding=[1.0, 0.0], content={"title": f"[FICTIF] {i}"}))
        await db_session.flush()

        embedder = _FakeEmbedder({"query": [1.0, 0.0]})
        examples = await rag_service.get_few_shot_examples(
            db_session, "query", embedder, stage="requirement", k=2, min_similarity=0.0
        )

        assert len(examples) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_when_query_embedding_fails(self, db_session):
        db_session.add(_generation(embedding=[1.0, 0.0]))
        await db_session.flush()

        embedder = _FakeEmbedder({})  # ne connaît aucun texte -> lève systématiquement

        examples = await rag_service.get_few_shot_examples(db_session, "query", embedder, stage="requirement")

        assert examples == []


class TestStoreEmbedding:
    @pytest.mark.asyncio
    async def test_sets_embedding_field(self, db_session):
        generation = _generation(embedding=None)
        db_session.add(generation)
        await db_session.flush()
        embedder = _FakeEmbedder({"texte source": [0.5, 0.5]})

        await rag_service.store_embedding(db_session, generation, "texte source", embedder)

        assert generation.embedding == [0.5, 0.5]

    @pytest.mark.asyncio
    async def test_noop_on_embedding_failure(self, db_session):
        generation = _generation(embedding=None)
        db_session.add(generation)
        await db_session.flush()
        embedder = _FakeEmbedder({})  # lève systématiquement

        await rag_service.store_embedding(db_session, generation, "texte source", embedder)

        assert generation.embedding is None
