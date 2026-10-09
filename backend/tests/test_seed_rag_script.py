"""
Tests unitaires — scripts/seed_rag_from_documents.py (amorçage RAG depuis
des documents historiques, cf. docs/module_2_requirement_analyzer.md).

N'exerce que _seed_one (logique pure : extraction -> agents -> stockage),
avec des providers factices (FakeLLMProvider/embedder fixe) et la DB SQLite en
mémoire partagée par le reste de la suite — pas argparse/main (juste de la
plomberie CLI, sans valeur à tester). Vérifie surtout le contrat "jamais de
ligne partielle/corrompue en base" sur les deux chemins d'échec (extraction,
analyse module 2) — cf. docs/module_3_test_design_agent.md, section
"Amorçage RAG depuis des documents historiques", pour la vérification
équivalente en conditions réelles (Ollama).

Toutes les données sont fictives.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.dlp_masking import get_masking_provider
from app.agents.embedding_layer import BaseEmbeddingProvider
from tests.fakes.llm import FakeLLMProvider
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.models.session import GenerationModel

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "seed_rag_from_documents.py"
_spec = importlib.util.spec_from_file_location("seed_rag_from_documents", _SCRIPT_PATH)
seed_rag = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = seed_rag
_spec.loader.exec_module(seed_rag)


VALID_REQUIREMENT_JSON = json.dumps({
    "title": "[FICTIF] Export CSV des statistiques de vente",
    "type": "feature",
    "description": "Exporter les statistiques de vente mensuelles en CSV.",
    "user_personas": ["Responsable des ventes"],
    "functional_criteria": ["Exporter en CSV"],
    "non_functional_criteria": [],
    "priority": "medium",
    "dependencies": [],
    "ambiguities": [],
    "confidence_score": 0.75,
})

VALID_TEST_DESIGN_JSON = json.dumps({
    "user_stories": [{"title": "t", "as_a": "u", "i_want": "x", "so_that": "y"}],
    "gherkin_features": [
        {
            "feature_name": "f1",
            "scenarios": [
                {"name": "s1", "tags": ["@negative"], "steps": [{"keyword": "Given", "text": "x"}]}
            ],
        }
    ],
    "test_summary": {"total_scenarios": 1},
})


class _FixedVectorEmbedder(BaseEmbeddingProvider):
    @property
    def provider_name(self) -> str:
        return "fixed"

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _working_analyzer() -> RequirementAnalyzerAgent:
    return RequirementAnalyzerAgent(llm_provider=FakeLLMProvider(custom_responses={"export": VALID_REQUIREMENT_JSON}))


def _broken_analyzer() -> RequirementAnalyzerAgent:
    # Mot-clé aligné sur le system prompt (constant à chaque tentative de
    # retry), pas sur le dernier message — garantit un échec systématique
    # sur les 3 tentatives (même convention que test_session_service.py).
    return RequirementAnalyzerAgent(
        llm_provider=FakeLLMProvider(custom_responses={"requirement analyzer agent": "Pas du JSON valide."})
    )


def _working_designer() -> TestDesignAgent:
    return TestDesignAgent(llm_provider=FakeLLMProvider(custom_responses={"export": VALID_TEST_DESIGN_JSON}))


class TestSeedOne:
    @pytest.mark.asyncio
    async def test_full_stage_seeds_both_requirement_and_test_design(self, db_session, tmp_path):
        doc = tmp_path / "ticket.txt"
        doc.write_text("[FICTIF] Je veux exporter mes statistiques de vente en CSV.", encoding="utf-8")
        embedder = _FixedVectorEmbedder()

        outcome = await seed_rag._seed_one(
            db_session, _working_analyzer(), _working_designer(), embedder, get_masking_provider(), doc, True
        )

        assert outcome == "requirement_and_test_design_seeded"
        rows = (await db_session.execute(select(GenerationModel))).scalars().all()
        assert {r.stage for r in rows} == {"requirement", "test_design"}
        assert all(r.decision == "accepted" for r in rows)
        assert all(r.embedding == [1.0, 0.0] for r in rows)
        assert rows[0].session_id == rows[1].session_id  # même session_id synthétique pour les deux stages

    @pytest.mark.asyncio
    async def test_requirement_only_stage_skips_test_design(self, db_session, tmp_path):
        doc = tmp_path / "ticket.txt"
        doc.write_text("[FICTIF] Je veux exporter mes statistiques de vente en CSV.", encoding="utf-8")
        embedder = _FixedVectorEmbedder()

        outcome = await seed_rag._seed_one(
            db_session, _working_analyzer(), _working_designer(), embedder, get_masking_provider(), doc, False
        )

        assert outcome == "requirement_seeded"
        rows = (await db_session.execute(select(GenerationModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].stage == "requirement"

    @pytest.mark.asyncio
    async def test_unsupported_extension_fails_extraction_without_db_write(self, db_session, tmp_path):
        doc = tmp_path / "notes.exe"
        doc.write_bytes(b"binaire non support\xe9")
        embedder = _FixedVectorEmbedder()

        outcome = await seed_rag._seed_one(
            db_session, _working_analyzer(), _working_designer(), embedder, get_masking_provider(), doc, True
        )

        assert outcome == "extraction_failed"
        rows = (await db_session.execute(select(GenerationModel))).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_broken_analyzer_fails_analysis_without_db_write(self, db_session, tmp_path):
        doc = tmp_path / "ticket.txt"
        doc.write_text("[FICTIF] Contenu quelconque.", encoding="utf-8")
        embedder = _FixedVectorEmbedder()

        outcome = await seed_rag._seed_one(
            db_session, _broken_analyzer(), _working_designer(), embedder, get_masking_provider(), doc, True
        )

        assert outcome == "analysis_failed"
        rows = (await db_session.execute(select(GenerationModel))).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_broken_designer_still_keeps_requirement_generation(self, db_session, tmp_path):
        """Si le module 3 échoue après un module 2 réussi, la génération
        'requirement' déjà réussie doit rester en base (best-effort partiel,
        pas de rollback du tout-ou-rien) — cf. docstring de _seed_one."""
        doc = tmp_path / "ticket.txt"
        doc.write_text("[FICTIF] Je veux exporter mes statistiques de vente en CSV.", encoding="utf-8")
        embedder = _FixedVectorEmbedder()
        broken_designer = TestDesignAgent(
            llm_provider=FakeLLMProvider(custom_responses={"test design agent": "Pas du JSON valide."})
        )

        outcome = await seed_rag._seed_one(
            db_session, _working_analyzer(), broken_designer, embedder, get_masking_provider(), doc, True
        )

        assert outcome == "test_design_failed"
        rows = (await db_session.execute(select(GenerationModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].stage == "requirement"
        assert rows[0].decision == "accepted"
