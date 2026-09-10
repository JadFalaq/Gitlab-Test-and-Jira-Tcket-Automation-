"""
Module 3 — Test Design Agent

Transforme un RequirementAnalysis (sortie du module 2) en artefacts de
test : user stories, features Gherkin, résumé de couverture.

Architecture:
    TestDesignAgent
        - Construit un LLMRequest (system prompt + RequirementAnalysis sérialisé)
        - Appelle le LLM via le module 1 (LLM Abstraction Layer)
        - Parse et valide la réponse JSON contre TestDesignOutput
        - Rend chaque GherkinFeature en texte .feature standard et valide
          sa syntaxe avec le parseur officiel Cucumber (gherkin_renderer.py)
        - Retente avec un message correctif si le JSON est invalide OU si
          un scénario généré produit un Gherkin syntaxiquement invalide
          (jusqu'à settings.LLM_MAX_RETRIES fois)

Design decisions:
    - Le LLM ne génère jamais de texte Gherkin brut, seulement du JSON
      structuré (cf. schemas/test_design.py) — le rendu en syntaxe Gherkin
      standard est déterministe, côté code, jamais laissé au LLM.
    - Le system prompt interdit d'inventer des scénarios hors du périmètre
      du RequirementAnalysis fourni (mêmes règles anti-hallucination que
      le module 2).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import ValidationError

from app.agents.gherkin_renderer import GherkinSyntaxError, render_and_validate
from app.agents.llm_layer import BaseLLMProvider, LLMProviderError, get_llm_provider
from app.config import settings
from app.schemas.llm import LLMMessage, LLMRequest
from app.schemas.requirement import RequirementAnalysis
from app.schemas.test_design import TestDesignOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TestDesignError(Exception):
    """Levée quand l'agent ne parvient pas à produire des artefacts de test valides."""

    def __init__(self, message: str, raw_content: Optional[str] = None):
        self.raw_content = raw_content
        super().__init__(message)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Tu es un Test Design Agent. À partir d'une analyse de besoin déjà \
structurée (JSON), tu produis des user stories, des scénarios de test Gherkin ET un \
résumé de couverture — pas seulement le chemin nominal, une vraie couverture exploitable \
en test. Tu ne travailles que sur le périmètre décrit dans l'analyse fournie — n'invente \
jamais de fonctionnalité ou de critère qui n'y figure pas.

Règles strictes :
1. Chaque scénario Gherkin doit être structuré en étapes (steps), pas en texte libre. \
Chaque step a un "keyword" parmi EXACTEMENT : "Given", "When", "Then", "And", "But" \
(aucune variante, aucune traduction).
2. Un scénario typique commence par un ou plusieurs "Given" (contexte), puis "When" \
(action), puis "Then" (résultat attendu). Utilise "And"/"But" pour enchaîner sur le \
même type d'étape.
3. Base les user stories et scénarios sur les champs de l'analyse fournie \
(functional_criteria, non_functional_criteria, user_personas, description). Si un \
point de l'analyse est dans "ambiguities", tu peux quand même produire un scénario \
raisonnable mais mentionne la limite dans "coverage_notes".
4. Couverture obligatoire, pour chaque critère fonctionnel qui s'y prête (ne force JAMAIS \
un cas creux ou artificiel juste pour cocher une catégorie — si un critère ne s'y prête \
vraiment pas, dis-le dans "coverage_notes" plutôt que d'inventer) :
   a. Un scénario "chemin nominal" (le cas qui marche), tag "@happy-path".
   b. Un scénario d'erreur/cas négatif si le critère a un mode d'échec identifiable \
(entrée invalide, action refusée, ressource absente, échec réseau...), tag "@negative".
   c. Un scénario de cas limite si le critère s'y prête (valeur limite/vide, action \
répétée ou concurrente, volume important, délai dépassé...), tag "@edge-case".
5. Chaque critère non-fonctionnel VÉRIFIABLE par un scénario concret (ex: "le scan doit \
prendre moins de 2 secondes", "les données doivent être chiffrées") devient un scénario à \
part entière, tag "@non-functional". Un critère non-fonctionnel trop vague pour être \
transformé en scénario concret (ex: "doit être maintenable") reste seulement mentionné \
dans "coverage_notes", jamais inventé en scénario creux.
6. Si le message contient une "Instruction spécifique de l'ingénieur", applique-la \
en priorité (ex: se concentrer sur les cas d'erreur, être plus concis) — mais reste \
toujours dans le périmètre de l'analyse fournie, n'invente jamais de fonctionnalité \
hors de ce périmètre même si l'instruction semble le suggérer. Cette instruction peut \
librement rééquilibrer la couverture ci-dessus (ex: si elle demande de se concentrer sur \
les cas d'erreur, tu peux réduire les scénarios "@happy-path" en conséquence).
7. Réponds UNIQUEMENT avec un objet JSON valide respectant exactement ce schéma \
(aucun texte avant/après, aucun bloc markdown) :

{
  "user_stories": [
    {
      "title": "string",
      "as_a": "string, persona",
      "i_want": "string, besoin",
      "so_that": "string, bénéfice",
      "acceptance_criteria": ["string", ...],
      "priority": "low" | "medium" | "high" | "critical"
    }
  ],
  "gherkin_features": [
    {
      "feature_name": "string",
      "description": "string",
      "tags": ["@string", ...],
      "scenarios": [
        {
          "name": "string",
          "tags": ["@string", ...],
          "steps": [
            {"keyword": "Given" | "When" | "Then" | "And" | "But", "text": "string"}
          ]
        }
      ]
    }
  ],
  "test_summary": {
    "total_scenarios": nombre entier,
    "coverage_notes": "string — mentionne explicitement les critères non couverts et pourquoi",
    "suggested_test_types": ["string", ...]
  }
}

"suggested_test_types" reflète les catégories de tags réellement utilisées ci-dessus \
(ex: "functional", "negative", "edge-case", "non-functional", "smoke") — pas une liste \
générique décorrélée des scénarios produits.

Si un tableau n'a pas d'éléments pertinents, renvoie un tableau vide [], jamais null. \
Génère au moins 1 user story et au moins 1 feature avec au moins 1 scénario. Un besoin \
avec plusieurs critères fonctionnels distincts doit produire plusieurs scénarios \
distincts (règle 4) — ne compresse pas tout en un seul scénario "fourre-tout"."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class TestDesignAgent:
    """Transforme un RequirementAnalysis en artefacts de test (TestDesignOutput)."""

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None):
        self._provider = llm_provider or get_llm_provider()

    async def design(
        self,
        requirement: RequirementAnalysis,
        guidance: Optional[str] = None,
        examples: Optional[list[dict]] = None,
    ) -> TestDesignOutput:
        """
        Génère les artefacts de test pour l'analyse de besoin fournie.

        `guidance` (optionnel) : instruction libre de l'ingénieur pour CET
        appel précis (ex: "concentre-toi sur les cas d'erreur"), saisie côté
        frontend au moment de "Régénérer" (cf. ReviewActions.tsx). Injectée
        dans le message utilisateur, jamais dans le system prompt — reste
        contrainte par les mêmes règles anti-hallucination (périmètre de
        `requirement` uniquement).

        `examples` (optionnel, RAG few-shot) : liste de `TestDesignOutput`
        passés, déjà validés par un humain, retrouvés par similarité
        sémantique avec la session courante (cf. app/services/rag_service.py,
        appelé par session_service.py — même principe que
        RequirementAnalyzerAgent.analyze : cet agent ne fait AUCUN accès
        DB/embedding lui-même, il se contente d'injecter ce qu'on lui
        fournit). Le message utilisateur précise explicitement de ne jamais
        réutiliser le contenu des exemples (seulement leur format/niveau de
        détail), pour ne pas rompre le principe anti-hallucination.

        Couverture (règle 4/5 du system prompt) : si la première réponse
        valide ne contient aucun scénario tagué @negative/@edge-case/
        @non-functional, UNE relance corrective est tentée (cf.
        _coverage_feedback) avant d'accepter le résultat tel quel — jamais
        bloquant, contrairement à un JSON/Gherkin invalide.

        Raises:
            TestDesignError: si le LLM ne produit pas de JSON valide, ou si le
                Gherkin généré reste syntaxiquement invalide après
                settings.LLM_MAX_RETRIES tentatives supplémentaires.
        """
        content = self._build_user_message(requirement, guidance, examples)
        messages = [LLMMessage(role="user", content=content)]
        last_error: Optional[str] = None
        last_content: Optional[str] = None

        attempts = settings.LLM_MAX_RETRIES + 1
        for attempt in range(1, attempts + 1):
            llm_request = LLMRequest(
                messages=messages,
                system_prompt=_SYSTEM_PROMPT,
                temperature=settings.LLM_DEFAULT_TEMPERATURE,
                max_tokens=settings.TEST_DESIGN_MAX_TOKENS,
                response_format="json",
            )

            try:
                response = await self._provider.complete(llm_request)
            except LLMProviderError as exc:
                # Erreur d'infrastructure (timeout, connexion, modèle absent) :
                # pas de retry, ça ne se résoudra pas tout seul dans la même requête.
                logger.warning("TestDesignAgent: LLM provider error: %s", exc)
                raise TestDesignError(
                    f"Échec de l'appel au LLM : {exc}", raw_content=None
                ) from exc

            last_content = response.content

            try:
                output = self._parse_response(response.content)
                self._validate_gherkin(output)
            except (json.JSONDecodeError, ValidationError, GherkinSyntaxError) as exc:
                last_error = str(exc)
                logger.warning(
                    "TestDesignAgent: invalid output on attempt %d/%d: %s",
                    attempt,
                    attempts,
                    last_error,
                )
                messages.append(LLMMessage(role="assistant", content=response.content))
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            f"La réponse précédente était invalide ({last_error}). "
                            "Renvoie UNIQUEMENT un JSON valide respectant exactement "
                            "le schéma demandé, sans texte ni markdown autour."
                        ),
                    )
                )
                continue

            coverage_feedback = self._coverage_feedback(output)
            if coverage_feedback and attempt < attempts:
                # Best-effort seulement : contrairement au bloc ci-dessus, une
                # couverture trop mince n'est jamais bloquante (cf. docstring
                # de _coverage_feedback) — juste UNE relance corrective.
                logger.info(
                    "TestDesignAgent: couverture insuffisante à la tentative %d/%d, relance corrective",
                    attempt,
                    attempts,
                )
                messages.append(LLMMessage(role="assistant", content=response.content))
                messages.append(LLMMessage(role="user", content=coverage_feedback))
                continue

            return output

        raise TestDesignError(
            f"Échec de la génération après {attempts} tentatives : {last_error}",
            raw_content=last_content,
        )

    @staticmethod
    def _build_user_message(
        requirement: RequirementAnalysis,
        guidance: Optional[str],
        examples: Optional[list[dict]],
    ) -> str:
        parts: list[str] = []
        if examples:
            parts.append(
                "Exemples de conceptions de test précédentes, déjà validées par un "
                "ingénieur — UNIQUEMENT pour t'indiquer le format et le niveau de "
                "détail attendus (structure des user stories, granularité des "
                "scénarios, style des tags). Ces exemples concernent d'AUTRES "
                "analyses de besoin : n'en réutilise jamais le contenu (titres, "
                "textes de scénarios, steps...) dans ta réponse."
            )
            for i, example in enumerate(examples, start=1):
                parts.append(f"Exemple {i} :\n{json.dumps(example, ensure_ascii=False)}")
            parts.append("---")

        parts.append("Analyse de besoin (JSON) :\n" + requirement.model_dump_json(indent=2))
        if guidance:
            parts.append(
                "Instruction spécifique de l'ingénieur pour cette génération "
                f"(reste dans le périmètre de l'analyse ci-dessus) : {guidance}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(content: str) -> TestDesignOutput:
        """Parse et valide le contenu brut du LLM contre TestDesignOutput."""
        data = json.loads(content)
        return TestDesignOutput(**data)

    @staticmethod
    def _validate_gherkin(output: TestDesignOutput) -> None:
        """Rend et valide la syntaxe de chaque feature Gherkin. Lève GherkinSyntaxError si invalide."""
        for feature in output.gherkin_features:
            render_and_validate(feature)

    _COVERAGE_TAGS = frozenset({"@negative", "@edge-case", "@non-functional"})

    @classmethod
    def _coverage_feedback(cls, output: TestDesignOutput) -> Optional[str]:
        """
        Heuristique best-effort (ajoutée le 22/07/2026 après un test réel où
        `llama3.2:3b` a ignoré la règle 4/5 du system prompt et n'a produit
        que des scénarios chemin nominal) : si AUCUN scénario ne porte l'un
        des tags de couverture demandés, c'est le signe que le modèle est
        resté sur le seul chemin nominal malgré l'instruction. Retourne un
        message correctif ciblé à renvoyer au LLM ; None si la couverture
        contient déjà au moins un signal de cas négatif/limite/non-fonctionnel.

        Jamais bloquant : contrairement à un JSON/Gherkin invalide, une
        couverture mince n'est PAS une erreur — un petit modèle local peut
        structurellement ne pas réussir à l'enrichir même après relance, et
        un résultat correct-mais-simple reste largement préférable à aucun
        résultat. Le retry n'a lieu qu'une fois (`attempt < attempts` côté
        appelant) — pas de boucle jusqu'à épuisement des tentatives pour ce
        critère qualitatif.
        """
        all_tags = {tag for feature in output.gherkin_features for scenario in feature.scenarios for tag in scenario.tags}
        if all_tags & cls._COVERAGE_TAGS:
            return None
        return (
            "Ta réponse ne contient aucun scénario tagué @negative, @edge-case ou "
            "@non-functional (règle 4/5 du system prompt) — seulement des chemins "
            "nominaux. Reprends la génération en ajoutant, pour au moins un critère "
            "fonctionnel qui s'y prête vraiment, un scénario d'échec ou de cas limite "
            "avec le tag correspondant. N'invente pas de cas artificiel si aucun "
            "critère ne s'y prête : dans ce cas, explique pourquoi dans coverage_notes."
        )
