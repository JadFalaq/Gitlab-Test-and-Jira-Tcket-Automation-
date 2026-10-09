"""
Tests unitaires — Module 2 : Requirement Analyzer Agent

Teste :
- Schémas Pydantic (RequirementAnalysisRequest, RequirementAnalysis)
- Parsing JSON -> RequirementAnalysis (succès et échec)
- L'agent sur 5 demandes fictives couvrant feature / bug / improvement /
  ambiguïté / non-JSON avec retry
- Échec définitif après épuisement des tentatives

Toutes les données sont fictives.
"""

from __future__ import annotations

import json

import pytest

from app.agents.llm_layer import BaseLLMProvider, LLMTimeoutError
from tests.fakes.llm import FakeLLMProvider
from app.agents.requirement_analyzer import (
    RequirementAnalysisError,
    RequirementAnalyzerAgent,
)
from app.schemas.llm import LLMRequest, LLMResponse, LLMUsage
from app.schemas.requirement import RequirementAnalysisRequest, RequirementAnalysis


# ===========================================================================
# Schémas
# ===========================================================================


class TestRequirementSchemas:
    def test_request_minimal(self):
        req = RequirementAnalysisRequest(user_input="Ajouter un mode sombre")
        assert req.context is None

    def test_request_empty_input_rejected(self):
        with pytest.raises(Exception):
            RequirementAnalysisRequest(user_input="")

    def test_analysis_valid(self):
        analysis = RequirementAnalysis(
            title="[FICTIF] Mode sombre",
            type="feature",
            description="Ajout d'un thème sombre à l'application",
            confidence_score=0.9,
        )
        assert analysis.user_personas == []
        assert analysis.priority == "medium"  # default

    def test_analysis_invalid_type_rejected(self):
        with pytest.raises(Exception):
            RequirementAnalysis(
                title="X",
                type="not_a_valid_type",
                description="X",
                confidence_score=0.5,
            )

    def test_analysis_confidence_score_bounds(self):
        with pytest.raises(Exception):
            RequirementAnalysis(
                title="X",
                type="bug",
                description="X",
                confidence_score=1.5,  # > 1.0
            )


# ===========================================================================
# Fixtures fictives (5 demandes d'ingénieur)
# ===========================================================================

FICTIONAL_REQUESTS = {
    "feature_qr": (
        "En tant que dev mobile, je veux ajouter un paiement par QR code "
        "dans l'app [FICTIF] ShopEasy pour que les utilisateurs paient plus vite en boutique.",
        json.dumps({
            "title": "[FICTIF] Paiement par QR Code",
            "type": "feature",
            "description": "Ajout d'un paiement par scan de QR code dans l'app ShopEasy.",
            "user_personas": ["Utilisateur mobile", "Commerçant"],
            "functional_criteria": ["Scanner un QR code", "Afficher le montant avant confirmation"],
            "non_functional_criteria": ["Scan en moins de 2 secondes"],
            "priority": "high",
            "dependencies": ["Module caméra"],
            "ambiguities": ["Quel provider de paiement utiliser ?"],
            "confidence_score": 0.82,
        }),
    ),
    "bug_login": (
        "Bug fictif : les utilisateurs de [FICTIF] TaskFlow sont déconnectés après 30 secondes "
        "d'inactivité au lieu de 30 minutes.",
        json.dumps({
            "title": "[FICTIF] Déconnexion prématurée après inactivité",
            "type": "bug",
            "description": "Le timeout de session est de 30 secondes au lieu des 30 minutes attendues.",
            "user_personas": ["Utilisateur connecté"],
            "functional_criteria": ["Le timeout de session doit être de 30 minutes"],
            "non_functional_criteria": [],
            "priority": "critical",
            "dependencies": [],
            "ambiguities": [],
            "confidence_score": 0.95,
        }),
    ),
    "improvement_search": (
        "Amélioration fictive : la recherche dans [FICTIF] DocuVault est trop lente sur de gros dossiers.",
        json.dumps({
            "title": "[FICTIF] Lenteur de la recherche sur gros dossiers",
            "type": "improvement",
            "description": "La recherche full-text devient lente sur les dossiers volumineux.",
            "user_personas": ["Utilisateur DocuVault"],
            "functional_criteria": [],
            "non_functional_criteria": ["Temps de réponse acceptable sur gros volumes"],
            "priority": "medium",
            "dependencies": ["Index de recherche"],
            "ambiguities": ["Quel est le seuil de volumétrie considéré 'gros dossier' ?"],
            "confidence_score": 0.6,
        }),
    ),
    "ambiguous_notif": (
        "Idée fictive : on devrait améliorer les notifications de [FICTIF] PulseCRM.",
        json.dumps({
            "title": "[FICTIF] Amélioration des notifications",
            "type": "other",
            "description": "Demande d'amélioration générale des notifications, sans précision.",
            "user_personas": [],
            "functional_criteria": [],
            "non_functional_criteria": [],
            "priority": "low",
            "dependencies": [],
            "ambiguities": [
                "Quel type de notification (push, email, in-app) ?",
                "Quel comportement actuel pose problème ?",
            ],
            "confidence_score": 0.25,
        }),
    ),
}


# ===========================================================================
# Agent — cas nominaux
# ===========================================================================


class TestRequirementAnalyzerAgent:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", list(FICTIONAL_REQUESTS.keys()))
    async def test_analyze_fictional_requests(self, key):
        user_input, mock_json = FICTIONAL_REQUESTS[key]
        provider = FakeLLMProvider(custom_responses={user_input[:20].lower(): mock_json})
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        result = await agent.analyze(RequirementAnalysisRequest(user_input=user_input))

        assert isinstance(result, RequirementAnalysis)
        expected = json.loads(mock_json)
        assert result.title == expected["title"]
        assert result.type == expected["type"]
        assert result.ambiguities == expected["ambiguities"]

    @pytest.mark.asyncio
    async def test_analyze_with_context(self):
        user_input, mock_json = FICTIONAL_REQUESTS["feature_qr"]
        provider = FakeLLMProvider(custom_responses={user_input[:20].lower(): mock_json})
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        result = await agent.analyze(
            RequirementAnalysisRequest(
                user_input=user_input,
                context="Précision : uniquement pour les commerçants premium.",
            )
        )
        assert isinstance(result, RequirementAnalysis)


# ===========================================================================
# Retry sur JSON invalide
# ===========================================================================


class _FlakyProvider(BaseLLMProvider):
    """Provider fictif : renvoie N réponses invalides puis une valide."""

    def __init__(self, invalid_content: str, valid_content: str, fail_times: int):
        self._invalid = invalid_content
        self._valid = valid_content
        self._fail_times = fail_times
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "flaky"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        content = self._invalid if self.call_count <= self._fail_times else self._valid
        return LLMResponse(
            content=content,
            model="flaky-model",
            provider=self.provider_name,
            usage=LLMUsage(),
        )


class TestRequirementAnalyzerRetry:
    @pytest.mark.asyncio
    async def test_retries_on_invalid_json_then_succeeds(self):
        _, valid_json = FICTIONAL_REQUESTS["bug_login"]
        provider = _FlakyProvider(
            invalid_content="Ceci n'est pas du JSON valide.",
            valid_content=valid_json,
            fail_times=1,  # échoue une fois, réussit à la 2e tentative
        )
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        result = await agent.analyze(RequirementAnalysisRequest(user_input="Un bug fictif"))

        assert isinstance(result, RequirementAnalysis)
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_fails_after_exhausting_retries(self):
        provider = _FlakyProvider(
            invalid_content="Toujours invalide.",
            valid_content="{}",
            fail_times=999,  # ne réussit jamais
        )
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        with pytest.raises(RequirementAnalysisError):
            await agent.analyze(RequirementAnalysisRequest(user_input="Un bug fictif"))

    @pytest.mark.asyncio
    async def test_fails_when_json_valid_but_schema_invalid(self):
        provider = _FlakyProvider(
            invalid_content=json.dumps({"title": "X"}),  # champs requis manquants
            valid_content=json.dumps({"title": "X"}),
            fail_times=999,
        )
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        with pytest.raises(RequirementAnalysisError):
            await agent.analyze(RequirementAnalysisRequest(user_input="Un bug fictif"))


class _AlwaysTimingOutProvider(BaseLLMProvider):
    """Provider fictif : simule un timeout LLM (erreur d'infrastructure, pas de contenu)."""

    def __init__(self):
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "always-timing-out"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        raise LLMTimeoutError("Simulated timeout", provider=self.provider_name)


class TestRequirementAnalyzerProviderError:
    @pytest.mark.asyncio
    async def test_llm_provider_error_fails_fast_without_retry(self):
        """
        Une erreur d'infrastructure (timeout/connexion) doit être convertie en
        RequirementAnalysisError immédiatement, sans consommer les tentatives
        de retry prévues pour les erreurs de parsing/validation JSON.
        """
        provider = _AlwaysTimingOutProvider()
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        with pytest.raises(RequirementAnalysisError):
            await agent.analyze(RequirementAnalysisRequest(user_input="Un bug fictif"))

        assert provider.call_count == 1


# ===========================================================================
# RAG — injection d'exemples few-shot (app/services/rag_service.py appelle
# analyze() avec `examples`, cet agent ne fait qu'injecter dans le prompt)
# ===========================================================================


class _CapturingProvider(BaseLLMProvider):
    """Capture le dernier LLMRequest reçu, pour inspecter le prompt construit."""

    def __init__(self, response_content: str):
        self._response_content = response_content
        self.last_request = None

    @property
    def provider_name(self) -> str:
        return "capturing"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            content=self._response_content, model="capturing-model", provider=self.provider_name, usage=LLMUsage()
        )


class TestFewShotExamples:
    @pytest.mark.asyncio
    async def test_examples_injected_into_prompt(self):
        _, valid_json = FICTIONAL_REQUESTS["feature_qr"]
        provider = _CapturingProvider(valid_json)
        agent = RequirementAnalyzerAgent(llm_provider=provider)
        example = {
            "title": "[FICTIF] Exemple précédent",
            "type": "feature",
            "description": "d",
            "confidence_score": 0.7,
        }

        await agent.analyze(
            RequirementAnalysisRequest(user_input="Nouvelle demande fictive"), examples=[example]
        )

        sent_content = provider.last_request.messages[-1].content
        assert "[FICTIF] Exemple précédent" in sent_content
        # Garde-fou anti-hallucination explicite (règle CLAUDE.md) : le
        # prompt doit interdire de réutiliser le contenu des exemples.
        assert "n'en réutilise jamais le contenu" in sent_content
        assert "Nouvelle demande fictive" in sent_content

    @pytest.mark.asyncio
    async def test_no_examples_block_when_examples_empty(self):
        """RAG_ENABLED=false ou historique vide -> examples=[] -> le prompt
        ne doit pas changer par rapport à avant l'ajout du RAG."""
        _, valid_json = FICTIONAL_REQUESTS["feature_qr"]
        provider = _CapturingProvider(valid_json)
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        await agent.analyze(RequirementAnalysisRequest(user_input="Nouvelle demande fictive"), examples=[])

        sent_content = provider.last_request.messages[-1].content
        assert sent_content == "Nouvelle demande fictive"

    @pytest.mark.asyncio
    async def test_analyze_without_examples_arg_still_works(self):
        """`examples` est optionnel : les appels existants (sans RAG) ne
        changent pas de comportement."""
        _, valid_json = FICTIONAL_REQUESTS["feature_qr"]
        provider = _CapturingProvider(valid_json)
        agent = RequirementAnalyzerAgent(llm_provider=provider)

        result = await agent.analyze(RequirementAnalysisRequest(user_input="Nouvelle demande fictive"))

        assert isinstance(result, RequirementAnalysis)
