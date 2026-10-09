"""
Tests unitaires — Module 4 : service d'orchestration (machine à états)

Teste le cycle de vie complet d'une session à travers les modules 2 et 3 :
création, accept/reject/regenerate à chaque étape, transitions invalides,
gestion des échecs LLM (requirement_failed / test_design_failed).

Toutes les données sont fictives.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.agents.chat_agent import ChatAgent, ChatAgentError
from app.agents.embedding_layer import BaseEmbeddingProvider
from app.agents.llm_layer import BaseLLMProvider, LLMResponse
from tests.fakes.llm import FakeLLMProvider
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.agents.vision_layer import BaseVisionProvider
from app.config import settings
from app.models.session import GenerationModel
from app.schemas.attachment import UploadedFile
from app.schemas.llm import LLMRequest, LLMUsage
from app.schemas.session import SessionCreateRequest
from app.services import session_service
from app.services.session_service import InvalidSessionTransitionError, SessionNotFoundError

FICTIONAL_USER_INPUT = "En tant que dev mobile, je veux ajouter un paiement par QR code dans [FICTIF] ShopEasy."
FICTIONAL_USER_ID = "fictional-user-id"

VALID_REQUIREMENT_JSON = json.dumps({
    "title": "[FICTIF] Paiement par QR Code",
    "type": "feature",
    "description": "Ajout d'un paiement par scan de QR code.",
    "user_personas": ["Utilisateur mobile"],
    "functional_criteria": ["Scanner un QR code"],
    "non_functional_criteria": [],
    "priority": "high",
    "dependencies": [],
    "ambiguities": [],
    "confidence_score": 0.8,
})

VALID_TEST_DESIGN_JSON = json.dumps({
    "user_stories": [
        {"title": "t", "as_a": "u", "i_want": "x", "so_that": "y", "acceptance_criteria": [], "priority": "high"}
    ],
    "gherkin_features": [
        {
            "feature_name": "Paiement par QR Code",
            "description": "",
            "tags": [],
            "scenarios": [
                {
                    "name": "s1",
                    # @negative : satisfait la vérification de couverture du
                    # module 3 (cf. TestDesignAgent._coverage_feedback) —
                    # sans ça, FakeLLMProvider (réponse fixe) déclencherait une
                    # relance corrective à chaque test qui passe par ici.
                    "tags": ["@negative"],
                    "steps": [
                        {"keyword": "Given", "text": "l'utilisateur est connecté"},
                        {"keyword": "Then", "text": "le paiement est confirmé"},
                    ],
                }
            ],
        }
    ],
    "test_summary": {"total_scenarios": 1, "coverage_notes": "", "suggested_test_types": []},
})

INVALID_JSON = "Ceci n'est pas du JSON valide."


def _working_analyzer() -> RequirementAnalyzerAgent:
    return RequirementAnalyzerAgent(llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_REQUIREMENT_JSON}))


def _broken_analyzer() -> RequirementAnalyzerAgent:
    # Clé alignée sur le system prompt (constant à chaque tentative de retry),
    # pas sur le dernier message (qui change après un essai raté) —
    # garantit un échec systématique sur les 3 tentatives.
    return RequirementAnalyzerAgent(
        llm_provider=FakeLLMProvider(custom_responses={"requirement analyzer agent": INVALID_JSON})
    )


def _working_designer() -> TestDesignAgent:
    return TestDesignAgent(llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_TEST_DESIGN_JSON}))


def _broken_designer() -> TestDesignAgent:
    return TestDesignAgent(
        llm_provider=FakeLLMProvider(custom_responses={"test design agent": INVALID_JSON})
    )


async def _create_valid_session(db_session):
    """Kickoff (statut 'chatting') + finalize_chat (statut 'analyzing') +
    exécution immédiate du module 2, pour obtenir directement une session
    'pending_requirement_review' dans les tests qui n'exercent pas
    eux-mêmes le découpage chatting/kickoff/run."""
    session = await session_service.create_session(
        db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
        user_id=FICTIONAL_USER_ID,
    )
    assert session.status == "chatting"
    kicked_off = await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)
    assert kicked_off.status == "analyzing"
    return await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())


async def _accept_and_design(db_session, session_id, designer=None):
    """Kickoff (statut 'designing_tests') + exécution immédiate du module 3."""
    kicked_off = await session_service.accept_requirement(db_session, session_id, FICTIONAL_USER_ID)
    assert kicked_off.status == "designing_tests"
    return await session_service.run_test_design(db_session, session_id, designer or _working_designer())


# ===========================================================================
# Création
# ===========================================================================


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_success(self, db_session):
        session = await _create_valid_session(db_session)

        assert session.status == "pending_requirement_review"
        assert session.requirement_analysis["title"] == "[FICTIF] Paiement par QR Code"
        assert session.error_message is None

    @pytest.mark.asyncio
    async def test_create_session_llm_failure(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )

        result = await session_service.run_requirement_analysis(db_session, session.id, _broken_analyzer())

        assert result.status == "requirement_failed"
        assert result.error_message is not None
        assert result.requirement_analysis is None


# ===========================================================================
# Étape requirement : accept / reject / regenerate
# ===========================================================================


class TestRequirementTransitions:
    @pytest.mark.asyncio
    async def test_accept_requirement_success(self, db_session):
        session = await _create_valid_session(db_session)

        result = await _accept_and_design(db_session, session.id, designer=_working_designer())

        assert result.status == "pending_test_review"
        assert result.test_design_output["user_stories"][0]["title"] == "t"

    @pytest.mark.asyncio
    async def test_accept_requirement_design_fails(self, db_session):
        session = await _create_valid_session(db_session)

        result = await _accept_and_design(db_session, session.id, designer=_broken_designer())

        assert result.status == "test_design_failed"
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_accept_requirement_wrong_state_raises(self, db_session):
        session = await _create_valid_session(db_session)
        await _accept_and_design(db_session, session.id, designer=_working_designer())

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_reject_requirement_success(self, db_session):
        session = await _create_valid_session(db_session)

        result = await session_service.reject_requirement(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "requirement_rejected"

    @pytest.mark.asyncio
    async def test_reject_requirement_is_terminal(self, db_session):
        session = await _create_valid_session(db_session)
        await session_service.reject_requirement(db_session, session.id, FICTIONAL_USER_ID)

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_regenerate_requirement_from_failed(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        session = await session_service.run_requirement_analysis(db_session, session.id, _broken_analyzer())
        assert session.status == "requirement_failed"

        kicked_off = await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)
        assert kicked_off.status == "analyzing"
        result = await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())

        assert result.status == "pending_requirement_review"
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_regenerate_requirement_from_pending(self, db_session):
        session = await _create_valid_session(db_session)

        kicked_off = await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)
        assert kicked_off.status == "analyzing"
        result = await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())

        assert result.status == "pending_requirement_review"


# ===========================================================================
# Étape tests : accept / reject / regenerate
# ===========================================================================


class TestTestsTransitions:
    @pytest.mark.asyncio
    async def test_accept_tests_success(self, db_session):
        session = await _create_valid_session(db_session)
        await _accept_and_design(db_session, session.id, designer=_working_designer())

        result = await session_service.accept_tests(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "validated"

    @pytest.mark.asyncio
    async def test_accept_tests_wrong_state_raises(self, db_session):
        session = await _create_valid_session(db_session)  # toujours pending_requirement_review

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.accept_tests(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_reject_tests_success(self, db_session):
        session = await _create_valid_session(db_session)
        await _accept_and_design(db_session, session.id, designer=_working_designer())

        result = await session_service.reject_tests(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "tests_rejected"

    @pytest.mark.asyncio
    async def test_regenerate_tests_from_failed(self, db_session):
        session = await _create_valid_session(db_session)
        await _accept_and_design(db_session, session.id, designer=_broken_designer())

        kicked_off = await session_service.regenerate_tests(db_session, session.id, FICTIONAL_USER_ID)
        assert kicked_off.status == "designing_tests"
        result = await session_service.run_test_design(db_session, session.id, _working_designer())

        assert result.status == "pending_test_review"


# ===========================================================================
# Lecture
# ===========================================================================


class TestReadSessions:
    @pytest.mark.asyncio
    async def test_get_session_not_found_raises(self, db_session):
        with pytest.raises(SessionNotFoundError):
            await session_service.get_session(db_session, "does-not-exist", FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_list_sessions_returns_all(self, db_session):
        await _create_valid_session(db_session)
        await _create_valid_session(db_session)

        sessions = await session_service.list_sessions(db_session, FICTIONAL_USER_ID)

        assert len(sessions) == 2


# ===========================================================================
# Traitement en tâche de fond (kickoff synchrone + exécution différée)
# ===========================================================================


class TestBackgroundKickoffStates:
    """Vérifie le découpage kickoff/run introduit pour ne jamais bloquer la
    requête HTTP sur l'appel LLM (cf. docstring de tête de session_service.py
    et docs/module_4_validation_interface.md)."""

    @pytest.mark.asyncio
    async def test_create_session_starts_in_chatting(self, db_session):
        """Module 9 : plus d'appel LLM implicite au kickoff — la session
        s'ouvre sur la phase de clarification conversationnelle."""
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )

        assert session.status == "chatting"
        assert session.requirement_analysis is None
        assert session.chat_history == [
            {"role": "user", "content": FICTIONAL_USER_INPUT, "created_at": session.chat_history[0]["created_at"]}
        ]

    @pytest.mark.asyncio
    async def test_finalize_chat_starts_analyzing(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )

        kicked_off = await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)

        assert kicked_off.status == "analyzing"
        assert kicked_off.requirement_analysis is None

    @pytest.mark.asyncio
    async def test_accept_requirement_starts_designing_tests(self, db_session):
        session = await _create_valid_session(db_session)

        kicked_off = await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)

        assert kicked_off.status == "designing_tests"
        assert kicked_off.test_design_output is None

    @pytest.mark.asyncio
    async def test_run_requirement_analysis_never_leaves_session_stuck_on_unexpected_error(self, db_session):
        """Filet de sécurité (règle 3 CLAUDE.md) : même une exception non
        prévue par RequirementAnalyzerAgent.analyze() (pas seulement
        RequirementAnalysisError) doit faire sortir la session de
        'analyzing', jamais la laisser bloquée indéfiniment — critique en
        tâche de fond où il n'existe plus de réponse HTTP pour signaler
        l'échec au client."""
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )

        class _ExplodingAnalyzer:
            async def analyze(self, request):
                raise RuntimeError("bug inattendu, pas une RequirementAnalysisError")

        result = await session_service.run_requirement_analysis(db_session, session.id, _ExplodingAnalyzer())

        assert result.status == "requirement_failed"
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_run_test_design_never_leaves_session_stuck_on_unexpected_error(self, db_session):
        session = await _create_valid_session(db_session)
        await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)

        class _ExplodingDesigner:
            async def design(self, requirement):
                raise RuntimeError("bug inattendu, pas une TestDesignError")

        result = await session_service.run_test_design(db_session, session.id, _ExplodingDesigner())

        assert result.status == "test_design_failed"
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_run_requirement_analysis_task_uses_own_session(self, db_sessionmaker):
        """run_requirement_analysis_task (wrapper BackgroundTasks) ouvre sa
        propre AsyncSession via la factory plutôt que de dépendre de celle
        de la requête HTTP (fermée avant l'exécution de la tâche). Chaque
        phase ouvre ici sa propre session depuis db_sessionmaker (comme le
        ferait get_db() par requête en production) : réutiliser une session
        déjà ouverte masquerait la mise à jour via son identity map
        (expire_on_commit=False), sans rapport avec ce qu'on teste ici."""
        async with db_sessionmaker() as db:
            session = await session_service.create_session(
                db, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
                user_id=FICTIONAL_USER_ID,
            )

        await session_service.run_requirement_analysis_task(db_sessionmaker, session.id, _working_analyzer())

        async with db_sessionmaker() as db:
            reloaded = await session_service.get_session(db, session.id, FICTIONAL_USER_ID)
        assert reloaded.status == "pending_requirement_review"


# ===========================================================================
# Historique des générations (fondation RAG/DPO — cf. GenerationModel)
# ===========================================================================


async def _generations_for(db_session, session_id: str, stage: str) -> list[GenerationModel]:
    result = await db_session.execute(
        select(GenerationModel)
        .where(GenerationModel.session_id == session_id, GenerationModel.stage == stage)
        .order_by(GenerationModel.attempt_no)
    )
    return list(result.scalars().all())


class TestGenerationHistory:
    """Vérifie que chaque appel LLM (module 2/3) laisse une trace décidable
    (pending/accepted/rejected/failed) dans GenerationModel — la fondation
    pour du few-shot RAG ou un futur fine-tuning par préférence (DPO) sur les
    paires (rejected, accepted)."""

    @pytest.mark.asyncio
    async def test_successful_analysis_creates_pending_generation(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())

        generations = await _generations_for(db_session, session.id, "requirement")

        assert len(generations) == 1
        assert generations[0].attempt_no == 0
        assert generations[0].decision == "pending"
        assert generations[0].content["title"] == "[FICTIF] Paiement par QR Code"
        assert generations[0].error_message is None

    @pytest.mark.asyncio
    async def test_failed_analysis_creates_failed_generation(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(db_session, session.id, _broken_analyzer())

        generations = await _generations_for(db_session, session.id, "requirement")

        assert len(generations) == 1
        assert generations[0].decision == "failed"
        assert generations[0].content is None
        assert generations[0].error_message is not None

    @pytest.mark.asyncio
    async def test_accept_requirement_marks_generation_accepted(self, db_session):
        session = await _create_valid_session(db_session)

        await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, session.id, "requirement")
        assert generations[0].decision == "accepted"
        assert generations[0].decided_at is not None

    @pytest.mark.asyncio
    async def test_reject_requirement_marks_generation_rejected(self, db_session):
        session = await _create_valid_session(db_session)

        await session_service.reject_requirement(db_session, session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, session.id, "requirement")
        assert generations[0].decision == "rejected"

    @pytest.mark.asyncio
    async def test_regenerate_requirement_yields_preference_pair(self, db_session):
        """Le cas d'usage central : une régénération produit exactement une
        paire (rejected, accepted) exploitable pour du DPO."""
        session = await _create_valid_session(db_session)  # attempt 0, pending

        await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)  # attempt 0 -> rejected (implicite)
        await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())  # attempt 1, pending
        await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)  # attempt 1 -> accepted

        generations = await _generations_for(db_session, session.id, "requirement")

        assert len(generations) == 2
        assert generations[0].attempt_no == 0
        assert generations[0].decision == "rejected"
        assert generations[1].attempt_no == 1
        assert generations[1].decision == "accepted"

    @pytest.mark.asyncio
    async def test_regenerate_from_failed_state_is_noop_on_generation_marking(self, db_session):
        """regenerate depuis 'requirement_failed' : la dernière génération
        est déjà 'failed' (pas 'pending'), _mark_pending_generation ne doit
        rien casser (no-op silencieux)."""
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(db_session, session.id, _broken_analyzer())

        await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())

        generations = await _generations_for(db_session, session.id, "requirement")
        assert len(generations) == 2
        assert generations[0].decision == "failed"  # inchangé, pas de fausse "rejected"
        assert generations[1].decision == "pending"

    @pytest.mark.asyncio
    async def test_test_design_generation_lifecycle(self, db_session):
        """Même mécanique côté module 3 (stage='test_design'), avec en plus
        la vérification de completed_at (module 7 — durée du pipeline)."""
        session = await _create_valid_session(db_session)
        await _accept_and_design(db_session, session.id, designer=_working_designer())

        result = await session_service.accept_tests(db_session, session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, session.id, "test_design")
        assert len(generations) == 1
        assert generations[0].decision == "accepted"
        assert result.completed_at is not None


# ===========================================================================
# RAG — bout en bout via session_service (embeddings/LLM factices, aucun
# appel réseau réel) : une génération 'accepted' doit être retrouvée et
# injectée comme exemple few-shot pour une session ultérieure.
# ===========================================================================


class _FixedVectorEmbedder(BaseEmbeddingProvider):
    """Renvoie toujours le même vecteur : garantit une similarité cosinus de
    1.0 entre deux appels, sans dépendre d'un vrai modèle d'embedding."""

    @property
    def provider_name(self) -> str:
        return "fixed"

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


class _CapturingLLMProvider(BaseLLMProvider):
    """Capture le dernier LLMRequest reçu — sert à vérifier ce qui a
    effectivement été envoyé au LLM (prompt + exemples few-shot injectés)."""

    def __init__(self, response_content: str):
        self._response_content = response_content
        self.last_request = None

    @property
    def provider_name(self) -> str:
        return "capturing"

    async def complete(self, request):
        self.last_request = request
        return LLMResponse(
            content=self._response_content, model="capturing-model", provider=self.provider_name, usage=LLMUsage()
        )


class TestRAGFewShotIntegration:
    @pytest.mark.asyncio
    async def test_accepted_generation_reused_as_few_shot_example(self, db_session, monkeypatch):
        """
        RAG_ENABLED est forcé à False dans toute la suite (conftest.py) —
        réactivé explicitement ici pour ce test bout en bout : une première
        session validée obtient un embedding sur sa génération 'accepted' ;
        une seconde session (embedder factice -> similarité garantie) doit
        la retrouver et voir son titre apparaître dans le prompt envoyé au
        LLM pour la nouvelle analyse.
        """
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        embedder = _FixedVectorEmbedder()

        first_provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, first_session.id, RequirementAnalyzerAgent(llm_provider=first_provider), embedder=embedder
        )
        await session_service.accept_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, first_session.id, "requirement")
        assert generations[0].embedding == [1.0, 0.0]  # stocké après acceptation

        second_provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, second_session.id, RequirementAnalyzerAgent(llm_provider=second_provider), embedder=embedder
        )

        sent_content = second_provider.last_request.messages[-1].content
        assert "[FICTIF] Paiement par QR Code" in sent_content  # titre de l'exemple injecté
        assert "n'en réutilise jamais le contenu" in sent_content

    @pytest.mark.asyncio
    async def test_rejected_generation_never_used_as_example(self, db_session, monkeypatch):
        """Une génération rejetée ne doit jamais être proposée comme exemple
        few-shot — seule 'accepted' compte (cf. _mark_pending_generation /
        rag_service.get_few_shot_examples)."""
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        embedder = _FixedVectorEmbedder()

        first_provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, first_session.id, RequirementAnalyzerAgent(llm_provider=first_provider), embedder=embedder
        )
        await session_service.reject_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        second_provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, second_session.id, RequirementAnalyzerAgent(llm_provider=second_provider), embedder=embedder
        )

        sent_content = second_provider.last_request.messages[-1].content
        assert "[FICTIF] Paiement par QR Code" not in sent_content


class TestRAGFewShotIntegrationTestDesign:
    """Même principe que TestRAGFewShotIntegration ci-dessus, mais pour le
    module 3 (23/07/2026) : une conception de test 'accepted' doit être
    retrouvée et injectée comme exemple few-shot pour une session ultérieure
    dont la demande brute (raw_input) est sémantiquement proche."""

    @pytest.mark.asyncio
    async def test_accepted_test_design_reused_as_few_shot_example(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        embedder = _FixedVectorEmbedder()

        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.finalize_chat(db_session, first_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, first_session.id, _working_analyzer(), embedder=embedder
        )
        await session_service.accept_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        first_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session, first_session.id, TestDesignAgent(llm_provider=first_provider), embedder=embedder
        )
        await session_service.accept_tests(db_session, first_session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, first_session.id, "test_design")
        assert generations[0].embedding == [1.0, 0.0]  # stocké après acceptation

        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.finalize_chat(db_session, second_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, second_session.id, _working_analyzer(), embedder=embedder
        )
        await session_service.accept_requirement(db_session, second_session.id, FICTIONAL_USER_ID)

        second_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session, second_session.id, TestDesignAgent(llm_provider=second_provider), embedder=embedder
        )

        sent_content = second_provider.last_request.messages[-1].content
        assert "Exemple 1 :" in sent_content  # bloc d'exemples few-shot présent
        assert "n'en réutilise jamais le contenu" in sent_content

    @pytest.mark.asyncio
    async def test_rejected_test_design_never_used_as_example(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        embedder = _FixedVectorEmbedder()

        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.finalize_chat(db_session, first_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, first_session.id, _working_analyzer(), embedder=embedder
        )
        await session_service.accept_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        first_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session, first_session.id, TestDesignAgent(llm_provider=first_provider), embedder=embedder
        )
        await session_service.reject_tests(db_session, first_session.id, FICTIONAL_USER_ID)

        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.finalize_chat(db_session, second_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, second_session.id, _working_analyzer(), embedder=embedder
        )
        await session_service.accept_requirement(db_session, second_session.id, FICTIONAL_USER_ID)

        second_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session, second_session.id, TestDesignAgent(llm_provider=second_provider), embedder=embedder
        )

        sent_content = second_provider.last_request.messages[-1].content
        assert "Exemple 1 :" not in sent_content


class _ExplodingEmbedder(BaseEmbeddingProvider):
    """Lève si .embed() est appelé — sert à prouver que RAG_ENABLED=False
    court-circuite VRAIMENT tout appel à l'embedder (retrouver des exemples
    ET stocker un embedding), pas seulement le résultat qui serait ignoré.
    Important en pratique : si l'embedder plante (ex: Ollama down, modèle
    d'embedding absent) est précisément une raison plausible de couper le
    RAG en urgence — le kill switch doit garantir qu'on ne le sollicite
    plus du tout, pas juste qu'on ignore ce qu'il renvoie."""

    @property
    def provider_name(self) -> str:
        return "exploding"

    async def embed(self, text: str) -> list[float]:
        raise AssertionError("embedder.embed() ne doit jamais être appelé quand RAG_ENABLED=False")


class TestRAGKillSwitch:
    """Réponse à une question légitime côté ingénierie : RAG est actif par
    défaut (settings.RAG_ENABLED=True) — si on le bascule à False en
    urgence (édition de .env puis redémarrage : pydantic-settings charge
    .env une seule fois au démarrage du process, pas de rechargement à
    chaud, cf. docs/module_1_llm_layer.md), le pipeline doit revenir
    proprement au mode zero-shot, jamais planter, et ne plus solliciter
    l'embedder du tout — même si des embeddings existent déjà en base d'une
    période où RAG était actif (jamais relus, jamais un problème)."""

    @pytest.mark.asyncio
    async def test_disabling_rag_after_it_was_active_falls_back_to_zero_shot(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        working_embedder = _FixedVectorEmbedder()

        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, first_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, first_session.id, _working_analyzer(), embedder=working_embedder
        )
        await session_service.accept_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, first_session.id, "requirement")
        assert generations[0].embedding is not None  # RAG était bien actif avant la bascule

        # "Urgence" : RAG_ENABLED bascule à False (simule un redémarrage
        # après édition du .env) — l'embedder factice qui suit lève à la
        # moindre sollicitation, pour prouver le court-circuit.
        monkeypatch.setattr(settings, "RAG_ENABLED", False)
        exploding_embedder = _ExplodingEmbedder()

        second_provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, second_session.id, FICTIONAL_USER_ID)
        result = await session_service.run_requirement_analysis(
            db_session,
            second_session.id,
            RequirementAnalyzerAgent(llm_provider=second_provider),
            embedder=exploding_embedder,
        )

        # Le pipeline continue de fonctionner normalement — pas d'exception
        # propagée depuis l'embedder, pas de statut _failed.
        assert result.status == "pending_requirement_review"

        # Prompt strictement identique à avant l'ajout du RAG — aucun exemple.
        sent_content = second_provider.last_request.messages[-1].content
        assert sent_content == "Une tout autre demande fictive"

        # Aucun embedding stocké pour cette nouvelle génération.
        new_generations = await _generations_for(db_session, second_session.id, "requirement")
        assert new_generations[0].embedding is None

    @pytest.mark.asyncio
    async def test_disabling_rag_also_falls_back_cleanly_for_test_design(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLED", True)
        working_embedder = _FixedVectorEmbedder()

        first_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, first_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, first_session.id, _working_analyzer(), embedder=working_embedder
        )
        await session_service.accept_requirement(db_session, first_session.id, FICTIONAL_USER_ID)

        first_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session, first_session.id, TestDesignAgent(llm_provider=first_provider), embedder=working_embedder
        )
        await session_service.accept_tests(db_session, first_session.id, FICTIONAL_USER_ID)

        generations = await _generations_for(db_session, first_session.id, "test_design")
        assert generations[0].embedding is not None  # RAG était bien actif avant la bascule

        monkeypatch.setattr(settings, "RAG_ENABLED", False)
        exploding_embedder = _ExplodingEmbedder()

        second_session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input="Une tout autre demande fictive"), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, second_session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, second_session.id, _working_analyzer(), embedder=exploding_embedder
        )
        await session_service.accept_requirement(db_session, second_session.id, FICTIONAL_USER_ID)

        second_provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        result = await session_service.run_test_design(
            db_session,
            second_session.id,
            TestDesignAgent(llm_provider=second_provider),
            embedder=exploding_embedder,
        )

        assert result.status == "pending_test_review"

        sent_content = second_provider.last_request.messages[-1].content
        assert "Exemple 1 :" not in sent_content

        new_generations = await _generations_for(db_session, second_session.id, "test_design")
        assert new_generations[0].embedding is None


# ===========================================================================
# Pièces jointes — bout en bout (extraction/description en tâche de fond,
# contexte enrichi envoyé au module 2)
# ===========================================================================


class _FixedVisionProvider2(BaseVisionProvider):
    """Nommé distinctement de celui de test_attachment_service.py (import
    séparé), même comportement : description déterministe, pas d'appel réseau."""

    @property
    def provider_name(self) -> str:
        return "fixed-vision"

    async def describe(self, image_bytes: bytes) -> str:
        return "[FICTIF] Capture d'écran montrant un message d'erreur de connexion."


class TestAttachmentIntegration:
    @pytest.mark.asyncio
    async def test_document_attachment_text_reaches_the_llm_prompt(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)

        session = await session_service.create_session(
            db_session,
            SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            files=[UploadedFile(filename="notes.txt", content_type="text/plain", content=b"[FICTIF] precision cle")],
            user_id=FICTIONAL_USER_ID,
        )
        assert len(session.attachments) == 1
        assert session.attachments[0].extracted_text is None  # pas encore traité au kickoff

        await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider)
        )

        sent_content = provider.last_request.messages[-1].content
        assert "notes.txt" in sent_content
        assert "[FICTIF] precision cle" in sent_content

    @pytest.mark.asyncio
    async def test_image_attachment_description_reaches_the_llm_prompt(self, db_session, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "VISION_ENABLED", True)
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)

        session = await session_service.create_session(
            db_session,
            SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            files=[UploadedFile(filename="capture.png", content_type="image/png", content=b"[FICTIF octets]")],
            user_id=FICTIONAL_USER_ID,
        )

        await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider),
            vision_provider=_FixedVisionProvider2(),
        )

        sent_content = provider.last_request.messages[-1].content
        assert "capture.png" in sent_content
        assert "message d'erreur de connexion" in sent_content

    @pytest.mark.asyncio
    async def test_attachment_not_reprocessed_on_regenerate(self, db_session, monkeypatch, tmp_path):
        """Une pièce jointe déjà traitée (extracted_text renseigné) ne doit
        pas être retraitée à chaque regenerate — le vision provider ne doit
        être appelé qu'une fois."""
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        call_count = {"n": 0}

        class _CountingVisionProvider(BaseVisionProvider):
            @property
            def provider_name(self) -> str:
                return "counting"

            async def describe(self, image_bytes: bytes) -> str:
                call_count["n"] += 1
                return "[FICTIF] description comptée"

        session = await session_service.create_session(
            db_session,
            SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            files=[UploadedFile(filename="capture.png", content_type="image/png", content=b"[FICTIF octets]")],
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, session.id, _working_analyzer(), vision_provider=_CountingVisionProvider(),
        )
        assert call_count["n"] == 1

        await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)
        await session_service.run_requirement_analysis(
            db_session, session.id, _working_analyzer(), vision_provider=_CountingVisionProvider(),
        )

        assert call_count["n"] == 1  # toujours 1 : pas de nouvel appel vision

    @pytest.mark.asyncio
    async def test_failing_attachment_does_not_break_analysis(self, db_session, monkeypatch, tmp_path):
        """Best-effort : une pièce jointe illisible/non supportée ne doit
        jamais empêcher l'analyse de se dérouler normalement."""
        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)

        session = await session_service.create_session(
            db_session,
            SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            files=[UploadedFile(filename="archive.zip", content_type="application/zip", content=b"PK\x03\x04")],
            user_id=FICTIONAL_USER_ID,
        )

        result = await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider)
        )

        assert result.status == "pending_requirement_review"
        assert result.attachments[0].extraction_error is not None

    @pytest.mark.asyncio
    async def test_no_attachments_leaves_prompt_unchanged(self, db_session):
        """Sans pièce jointe, le comportement doit être identique à avant
        l'ajout de cette fonctionnalité (pas de bloc de contexte superflu)."""
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)

        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider)
        )

        assert provider.last_request.messages[-1].content == FICTIONAL_USER_INPUT


# ===========================================================================
# Module 8 — Instruction libre de régénération ("guidance"), jamais stockée
# sur la session, injectée uniquement dans le prompt de l'appel concerné.
# ===========================================================================


class TestRegenerationGuidance:
    @pytest.mark.asyncio
    async def test_requirement_guidance_reaches_the_llm_prompt(self, db_session):
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )

        await session_service.run_requirement_analysis(
            db_session,
            session.id,
            RequirementAnalyzerAgent(llm_provider=provider),
            guidance="[FICTIF] précise le moyen de paiement accepté",
        )

        sent_content = provider.last_request.messages[-1].content
        assert "[FICTIF] précise le moyen de paiement accepté" in sent_content

    @pytest.mark.asyncio
    async def test_requirement_guidance_never_persisted_on_session(self, db_session):
        """Le guidance est transitoire : session.context ne doit jamais en
        garder trace après l'appel."""
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )

        result = await session_service.run_requirement_analysis(
            db_session,
            session.id,
            RequirementAnalyzerAgent(llm_provider=provider),
            guidance="[FICTIF] ne garde pas cette trace",
        )

        assert result.context is None

    @pytest.mark.asyncio
    async def test_test_design_guidance_reaches_the_llm_prompt(self, db_session):
        session = await _create_valid_session(db_session)
        await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)

        provider = _CapturingLLMProvider(VALID_TEST_DESIGN_JSON)
        await session_service.run_test_design(
            db_session,
            session.id,
            TestDesignAgent(llm_provider=provider),
            guidance="[FICTIF] ajoute un scénario d'échec réseau",
        )

        sent_content = provider.last_request.messages[-1].content
        assert "[FICTIF] ajoute un scénario d'échec réseau" in sent_content


# ===========================================================================
# Module 9 — Chat de clarification avant l'analyse structurée
# ===========================================================================


class _FailingChatProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "always-failing"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from app.agents.llm_layer import LLMProviderError

        raise LLMProviderError("Simulated failure", provider=self.provider_name)


class TestChatFlow:
    @pytest.mark.asyncio
    async def test_create_session_starts_chatting_with_seeded_history(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )

        assert session.status == "chatting"
        assert len(session.chat_history) == 1
        assert session.chat_history[0]["role"] == "user"
        assert session.chat_history[0]["content"] == FICTIONAL_USER_INPUT

    @pytest.mark.asyncio
    async def test_send_chat_message_appends_user_and_assistant_turns(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        chat_agent = ChatAgent(llm_provider=FakeLLMProvider())

        result = await session_service.send_chat_message(
            db_session, session.id, FICTIONAL_USER_ID, "[FICTIF] c'est pour des clients particuliers", chat_agent
        )

        assert result.status == "chatting"  # reste en chatting, pas de transition
        assert len(result.chat_history) == 3
        assert result.chat_history[1]["role"] == "user"
        assert result.chat_history[1]["content"] == "[FICTIF] c'est pour des clients particuliers"
        assert result.chat_history[2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_send_chat_message_wrong_state_raises(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)  # -> analyzing

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.send_chat_message(
                db_session, session.id, FICTIONAL_USER_ID, "trop tard", ChatAgent(llm_provider=FakeLLMProvider())
            )

    @pytest.mark.asyncio
    async def test_send_chat_message_llm_failure_persists_nothing(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        chat_agent = ChatAgent(llm_provider=_FailingChatProvider())

        with pytest.raises(ChatAgentError):
            await session_service.send_chat_message(
                db_session, session.id, FICTIONAL_USER_ID, "[FICTIF] un message", chat_agent
            )

        reloaded = await session_service.get_session(db_session, session.id, FICTIONAL_USER_ID)
        assert len(reloaded.chat_history) == 1  # rien d'ajouté, ni le message ni de réponse partielle
        assert reloaded.status == "chatting"

    @pytest.mark.asyncio
    async def test_finalize_chat_transitions_to_analyzing(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )

        result = await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "analyzing"

    @pytest.mark.asyncio
    async def test_finalize_chat_wrong_state_raises(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_single_message_history_omitted_from_analysis_context(self, db_session):
        """Sans discussion (juste le message initial), pas de bloc
        'Conversation de clarification' superflu dans le contexte — déjà
        couvert par raw_input, cf. _render_chat_transcript."""
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)
        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)

        await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider)
        )

        assert provider.last_request.messages[-1].content == FICTIONAL_USER_INPUT

    @pytest.mark.asyncio
    async def test_chat_transcript_reaches_the_analysis_prompt_after_finalize(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT), user_id=FICTIONAL_USER_ID
        )
        await session_service.send_chat_message(
            db_session,
            session.id,
            FICTIONAL_USER_ID,
            "[FICTIF] uniquement pour les clients particuliers, pas les commerçants",
            ChatAgent(llm_provider=FakeLLMProvider()),
        )
        await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)

        provider = _CapturingLLMProvider(VALID_REQUIREMENT_JSON)
        await session_service.run_requirement_analysis(
            db_session, session.id, RequirementAnalyzerAgent(llm_provider=provider)
        )

        sent_content = provider.last_request.messages[-1].content
        assert "Conversation de clarification" in sent_content
        assert "uniquement pour les clients particuliers, pas les commerçants" in sent_content
