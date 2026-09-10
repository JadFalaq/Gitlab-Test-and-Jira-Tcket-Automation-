"""
Tests unitaires — Module 3 : Test Design Agent

Teste :
- Schémas Pydantic (GherkinStep/Scenario/Feature, UserStory, TestSummary, TestDesignOutput)
- L'agent sur des RequirementAnalysis fictives (issues du module 2)
- Retry sur JSON invalide
- Retry sur Gherkin syntaxiquement invalide (pas seulement JSON invalide)
- Échec définitif après épuisement des tentatives

Toutes les données sont fictives.
"""

from __future__ import annotations

import json

import pytest

from app.agents.llm_layer import BaseLLMProvider, LLMTimeoutError, MockProvider
from app.agents.test_design_agent import _SYSTEM_PROMPT, TestDesignAgent, TestDesignError
from app.config import settings
from app.schemas.llm import LLMRequest, LLMResponse, LLMUsage
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_design import (
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
    TestDesignOutput,
    TestSummary,
    UserStory,
)


# ===========================================================================
# Schémas
# ===========================================================================


class TestTestDesignSchemas:
    def test_gherkin_step_invalid_keyword_rejected(self):
        with pytest.raises(Exception):
            GherkinStep(keyword="Alors", text="x")  # pas un mot-clé standard

    def test_gherkin_scenario_requires_at_least_one_step(self):
        with pytest.raises(Exception):
            GherkinScenario(name="s1", steps=[])

    def test_gherkin_feature_requires_at_least_one_scenario(self):
        with pytest.raises(Exception):
            GherkinFeature(feature_name="f1", scenarios=[])

    def test_test_design_output_requires_user_story_and_feature(self):
        with pytest.raises(Exception):
            TestDesignOutput(
                user_stories=[],
                gherkin_features=[],
                test_summary=TestSummary(total_scenarios=0),
            )

    def test_gherkin_feature_rejects_tag_without_at_prefix(self):
        """Reproduit le bug réel observé avec une pièce jointe PDF volumineuse :
        le LLM place du texte libre dans "tags" au lieu d'un tag Gherkin, ce
        qui cassait le rendu .feature (cf. gherkin_renderer.py) avec une
        erreur de parseur peu exploitable. Doit être rejeté ici, en amont."""
        with pytest.raises(Exception):
            GherkinFeature(
                feature_name="f1",
                tags=["PDF analyse extraite_informations"],
                scenarios=[
                    GherkinScenario(name="s1", steps=[GherkinStep(keyword="Given", text="x")])
                ],
            )

    def test_gherkin_scenario_rejects_tag_without_at_prefix(self):
        with pytest.raises(Exception):
            GherkinScenario(
                name="s1",
                tags=["pas un tag"],
                steps=[GherkinStep(keyword="Given", text="x")],
            )

    def test_gherkin_feature_strips_whitespace_around_valid_tags(self):
        feature = GherkinFeature(
            feature_name="f1",
            tags=["  @smoke  "],
            scenarios=[
                GherkinScenario(name="s1", steps=[GherkinStep(keyword="Given", text="x")])
            ],
        )
        assert feature.tags == ["@smoke"]

    def test_test_design_output_valid(self):
        output = TestDesignOutput(
            user_stories=[
                UserStory(title="t", as_a="user", i_want="x", so_that="y")
            ],
            gherkin_features=[
                GherkinFeature(
                    feature_name="f1",
                    scenarios=[
                        GherkinScenario(
                            name="s1",
                            steps=[GherkinStep(keyword="Given", text="x")],
                        )
                    ],
                )
            ],
            test_summary=TestSummary(total_scenarios=1),
        )
        assert output.user_stories[0].priority == "medium"  # default


# ===========================================================================
# Fixtures fictives
# ===========================================================================


def _fictional_requirement() -> RequirementAnalysis:
    return RequirementAnalysis(
        title="[FICTIF] Paiement par QR Code",
        type="feature",
        description="Ajout d'un paiement par scan de QR code dans l'app ShopEasy.",
        user_personas=["Utilisateur mobile"],
        functional_criteria=["Scanner un QR code", "Afficher le montant avant confirmation"],
        non_functional_criteria=["Scan en moins de 2 secondes"],
        priority="high",
        dependencies=["Module caméra"],
        ambiguities=["Quel provider de paiement utiliser ?"],
        confidence_score=0.82,
    )


def _valid_test_design_json() -> str:
    return json.dumps({
        "user_stories": [
            {
                "title": "Paiement par scan QR",
                "as_a": "Utilisateur mobile",
                "i_want": "scanner un QR code pour payer",
                "so_that": "je puisse payer rapidement",
                "acceptance_criteria": ["Le scan démarre en moins de 1 seconde"],
                "priority": "high",
            }
        ],
        "gherkin_features": [
            {
                "feature_name": "Paiement par QR Code",
                "description": "En tant qu'utilisateur, je veux payer via QR code",
                "tags": ["@feature-qr-payment"],
                "scenarios": [
                    {
                        "name": "Paiement réussi via scan QR",
                        "tags": ["@smoke", "@happy-path"],
                        "steps": [
                            {"keyword": "Given", "text": "l'utilisateur est connecté"},
                            {"keyword": "When", "text": "l'utilisateur scanne le QR code"},
                            {"keyword": "Then", "text": "le paiement est confirmé"},
                        ],
                    },
                    {
                        "name": "QR code invalide rejeté",
                        # @negative : satisfait la vérification de couverture
                        # du module 3 (cf. TestDesignAgent._coverage_feedback).
                        "tags": ["@negative"],
                        "steps": [
                            {"keyword": "Given", "text": "l'utilisateur est connecté"},
                            {"keyword": "When", "text": "l'utilisateur scanne un QR code invalide"},
                            {"keyword": "Then", "text": "un message d'erreur explicite est affiché"},
                        ],
                    },
                ],
            }
        ],
        "test_summary": {
            "total_scenarios": 2,
            "coverage_notes": "Couvre le happy path et le rejet d'un QR code invalide.",
            "suggested_test_types": ["functional", "smoke", "negative"],
        },
    })


# ===========================================================================
# Agent — cas nominal
# ===========================================================================


class _CapturingProvider(BaseLLMProvider):
    """Capture le dernier LLMRequest reçu — sert à vérifier ce qui a
    effectivement été envoyé au LLM (même pattern que
    test_session_service.py::_CapturingLLMProvider)."""

    def __init__(self, response_content: str):
        self._response_content = response_content
        self.last_request: LLMRequest | None = None

    @property
    def provider_name(self) -> str:
        return "capturing"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return LLMResponse(
            content=self._response_content, model="capturing-model", provider=self.provider_name, usage=LLMUsage()
        )


class TestTestDesignAgentNominal:
    @pytest.mark.asyncio
    async def test_design_returns_valid_output(self):
        provider = MockProvider(custom_responses={"paiement": _valid_test_design_json()})
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)
        assert result.user_stories[0].title == "Paiement par scan QR"
        assert result.gherkin_features[0].feature_name == "Paiement par QR Code"
        assert result.gherkin_features[0].scenarios[0].steps[0].keyword == "Given"

    @pytest.mark.asyncio
    async def test_design_with_guidance_includes_it_in_the_prompt(self):
        """Module 8 : l'instruction libre de régénération doit atteindre le
        LLM, jamais être silencieusement ignorée."""
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)

        await agent.design(_fictional_requirement(), guidance="[FICTIF] concentre-toi sur les cas d'erreur")

        sent_content = provider.last_request.messages[-1].content
        assert "[FICTIF] concentre-toi sur les cas d'erreur" in sent_content

    @pytest.mark.asyncio
    async def test_design_without_guidance_does_not_mention_it(self):
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)

        await agent.design(_fictional_requirement())

        sent_content = provider.last_request.messages[-1].content
        assert "Instruction spécifique" not in sent_content


# ===========================================================================
# RAG — injection d'exemples few-shot (app/services/rag_service.py appelle
# design() avec `examples`, cet agent ne fait qu'injecter dans le prompt —
# même principe que RequirementAnalyzerAgent, cf. test_requirement_analyzer.py
# ::TestFewShotExamples)
# ===========================================================================


class TestFewShotExamples:
    @pytest.mark.asyncio
    async def test_examples_injected_into_prompt(self):
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)
        example = {
            "user_stories": [
                {"title": "[FICTIF] Exemple précédent", "as_a": "u", "i_want": "x", "so_that": "y"}
            ],
            "gherkin_features": [],
            "test_summary": {"total_scenarios": 0},
        }

        await agent.design(_fictional_requirement(), examples=[example])

        sent_content = provider.last_request.messages[-1].content
        assert "[FICTIF] Exemple précédent" in sent_content
        # Garde-fou anti-hallucination explicite (règle CLAUDE.md) : le
        # prompt doit interdire de réutiliser le contenu des exemples.
        assert "n'en réutilise jamais le contenu" in sent_content
        assert "Paiement par QR Code" in sent_content  # la vraie demande reste présente

    @pytest.mark.asyncio
    async def test_no_examples_block_when_examples_empty(self):
        """RAG_ENABLED=false ou historique vide -> examples=[] -> le prompt
        ne doit pas changer par rapport à avant l'ajout du RAG."""
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)

        await agent.design(_fictional_requirement(), examples=[])

        sent_content = provider.last_request.messages[-1].content
        assert "Exemples de conceptions de test précédentes" not in sent_content

    @pytest.mark.asyncio
    async def test_design_without_examples_arg_still_works(self):
        """`examples` est optionnel : les appels existants (sans RAG) ne
        changent pas de comportement."""
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)


# ===========================================================================
# Retry
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
            content=content, model="flaky-model", provider=self.provider_name, usage=LLMUsage()
        )


class TestTestDesignAgentRetry:
    @pytest.mark.asyncio
    async def test_retries_on_invalid_json_then_succeeds(self):
        provider = _FlakyProvider(
            invalid_content="Pas du JSON valide.",
            valid_content=_valid_test_design_json(),
            fail_times=1,
        )
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_invalid_gherkin_syntax_then_succeeds(self):
        """JSON valide au sens Pydantic mais Gherkin cassé (step avec saut de ligne) -> retry."""
        broken_gherkin = json.dumps({
            "user_stories": [
                {"title": "t", "as_a": "u", "i_want": "x", "so_that": "y"}
            ],
            "gherkin_features": [
                {
                    "feature_name": "f1",
                    "scenarios": [
                        {
                            "name": "s1",
                            "steps": [
                                {"keyword": "Given", "text": "quelque chose\nde cassé"},
                                {"keyword": "Then", "text": "ok"},
                            ],
                        }
                    ],
                }
            ],
            "test_summary": {"total_scenarios": 1},
        })
        provider = _FlakyProvider(
            invalid_content=broken_gherkin,
            valid_content=_valid_test_design_json(),
            fail_times=1,
        )
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_when_llm_puts_free_text_in_tags_then_succeeds(self):
        """Reproduit le bug réel (session avec pièce jointe PDF, 22/07/2026) :
        JSON structurellement valide mais "tags" contient du texte libre
        (contenu de la pièce jointe mal placé par le LLM) au lieu d'un tag
        Gherkin — doit être rattrapé par la validation Pydantic (retry),
        jamais atteindre le rendu .feature avec une erreur de parseur opaque."""
        invalid_tags_json = json.dumps({
            "user_stories": [
                {"title": "t", "as_a": "u", "i_want": "x", "so_that": "y"}
            ],
            "gherkin_features": [
                {
                    "feature_name": "f1",
                    "tags": ["PDF analyse extraite_informations"],
                    "scenarios": [
                        {
                            "name": "s1",
                            "steps": [{"keyword": "Given", "text": "x"}],
                        }
                    ],
                }
            ],
            "test_summary": {"total_scenarios": 1},
        })
        provider = _FlakyProvider(
            invalid_content=invalid_tags_json,
            valid_content=_valid_test_design_json(),
            fail_times=1,
        )
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_fails_after_exhausting_retries(self):
        provider = _FlakyProvider(
            invalid_content="Toujours invalide.",
            valid_content="{}",
            fail_times=999,
        )
        agent = TestDesignAgent(llm_provider=provider)

        with pytest.raises(TestDesignError):
            await agent.design(_fictional_requirement())


# ===========================================================================
# Relance corrective sur couverture mince (22/07/2026, cf. règle 4/5 du
# system prompt + TestDesignAgent._coverage_feedback) — distincte de la
# relance sur JSON/Gherkin invalide : jamais bloquante.
# ===========================================================================


def _thin_coverage_json() -> str:
    """JSON valide (JSON + Gherkin) mais sans aucun tag de couverture
    (@negative/@edge-case/@non-functional) — reproduit le comportement réel
    observé avec llama3.2:3b le 22/07/2026 (chemin nominal uniquement)."""
    return json.dumps({
        "user_stories": [{"title": "t", "as_a": "u", "i_want": "x", "so_that": "y"}],
        "gherkin_features": [
            {
                "feature_name": "f1",
                "scenarios": [
                    {"name": "s1", "tags": ["@happy-path"], "steps": [{"keyword": "Given", "text": "x"}]}
                ],
            }
        ],
        "test_summary": {"total_scenarios": 1},
    })


class _CoverageFlakyProvider(BaseLLMProvider):
    """Renvoie une réponse valide mais à couverture mince N fois, puis une
    réponse valide et couverte — même pattern que _FlakyProvider, mais les
    deux réponses sont valides au sens JSON/Gherkin (seule la couverture diffère)."""

    def __init__(self, thin_content: str, rich_content: str, thin_times: int):
        self._thin = thin_content
        self._rich = rich_content
        self._thin_times = thin_times
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "coverage-flaky"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        content = self._thin if self.call_count <= self._thin_times else self._rich
        return LLMResponse(
            content=content, model="coverage-flaky-model", provider=self.provider_name, usage=LLMUsage()
        )


class TestCoverageRetry:
    @pytest.mark.asyncio
    async def test_retries_once_when_no_coverage_tags_then_accepts_richer_result(self):
        provider = _CoverageFlakyProvider(
            thin_content=_thin_coverage_json(), rich_content=_valid_test_design_json(), thin_times=1
        )
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert provider.call_count == 2
        all_tags = {t for f in result.gherkin_features for s in f.scenarios for t in s.tags}
        assert all_tags & {"@negative", "@edge-case", "@non-functional"}

    @pytest.mark.asyncio
    async def test_no_wasted_retry_when_coverage_already_present(self):
        provider = _CoverageFlakyProvider(
            thin_content=_thin_coverage_json(), rich_content=_valid_test_design_json(), thin_times=0
        )
        agent = TestDesignAgent(llm_provider=provider)

        await agent.design(_fictional_requirement())

        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_thin_coverage_never_raises_even_after_exhausting_retries(self):
        """Best-effort strict : une couverture mince n'est JAMAIS une erreur
        bloquante, contrairement à un JSON/Gherkin invalide — un petit
        modèle local qui ignore structurellement la règle 4/5 doit quand
        même produire un résultat exploitable plutôt qu'un TestDesignError."""
        provider = _CoverageFlakyProvider(
            thin_content=_thin_coverage_json(), rich_content=_valid_test_design_json(), thin_times=999
        )
        agent = TestDesignAgent(llm_provider=provider)

        result = await agent.design(_fictional_requirement())

        assert isinstance(result, TestDesignOutput)
        assert provider.call_count == settings.LLM_MAX_RETRIES + 1  # une seule relance corrective, pas en boucle


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


class TestTestDesignAgentProviderError:
    @pytest.mark.asyncio
    async def test_llm_provider_error_fails_fast_without_retry(self):
        """Erreur d'infrastructure -> TestDesignError immédiat, sans consommer de retries."""
        provider = _AlwaysTimingOutProvider()
        agent = TestDesignAgent(llm_provider=provider)

        with pytest.raises(TestDesignError):
            await agent.design(_fictional_requirement())

        assert provider.call_count == 1


# ===========================================================================
# Couverture renforcée (22/07/2026) — garde-fous de non-régression sur le
# system prompt, pour ne pas perdre silencieusement ces règles plus tard.
# ===========================================================================


class TestSystemPromptCoverageRules:
    def test_requires_happy_path_negative_and_edge_case_tags(self):
        for tag in ("@happy-path", "@negative", "@edge-case", "@non-functional"):
            assert tag in _SYSTEM_PROMPT

    def test_forbids_hollow_scenarios_when_criterion_does_not_apply(self):
        """La contrainte de couverture doit s'accompagner d'une échappatoire
        anti-hallucination explicite — sinon un modèle strict inventerait un
        cas négatif/limite artificiel juste pour cocher la règle."""
        assert "creux" in _SYSTEM_PROMPT or "artificiel" in _SYSTEM_PROMPT
        assert "coverage_notes" in _SYSTEM_PROMPT

    def test_guidance_rule_can_rebalance_default_coverage(self):
        assert "rééquilibrer" in _SYSTEM_PROMPT


class TestTestDesignAgentMaxTokens:
    @pytest.mark.asyncio
    async def test_uses_dedicated_test_design_max_tokens_setting(self):
        """Un plafond de tokens propre au module 3 (pas le défaut partagé
        avec le module 2) — la couverture renforcée produit une sortie plus
        longue, cf. config.py::TEST_DESIGN_MAX_TOKENS."""
        provider = _CapturingProvider(_valid_test_design_json())
        agent = TestDesignAgent(llm_provider=provider)

        await agent.design(_fictional_requirement())

        assert provider.last_request.max_tokens == settings.TEST_DESIGN_MAX_TOKENS
