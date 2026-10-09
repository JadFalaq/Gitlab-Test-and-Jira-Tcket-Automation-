"""
Faux provider LLM, réservé aux tests.

Renvoie des réponses pré-définies choisies par mot-clé dans le prompt : la
suite de tests exerce tout le pipeline sans LLM réel, de façon déterministe.
Il n'est jamais enregistré dans app/agents/llm_layer.py.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.agents.llm_layer import BaseLLMProvider
from app.schemas.llm import LLMRequest, LLMResponse, LLMUsage

logger = logging.getLogger(__name__)



class FakeLLMProvider(BaseLLMProvider):
    """
    Provider factice qui renvoie des réponses pré-définies (tests uniquement).

    On peut injecter des réponses custom via le constructeur ou
    utiliser les réponses par défaut.
    """

    # Réponse fictive d'analyse de besoin (module 2 — RequirementAnalysis)
    _REQUIREMENT_ANALYSIS_EXAMPLE: str = json.dumps({
        "title": "[FICTIF] Paiement QR Code — App Mobile",
        "type": "feature",
        "description": "Ajout d'un système de paiement par QR code dans l'application mobile permettant aux utilisateurs de scanner un code pour effectuer un paiement.",
        "user_personas": ["Utilisateur mobile", "Commerçant"],
        "functional_criteria": [
            "L'utilisateur peut scanner un QR code depuis l'app",
            "Le montant du paiement est affiché avant confirmation",
            "Un reçu est généré après paiement réussi",
        ],
        "non_functional_criteria": [
            "Le scan doit fonctionner en moins de 2 secondes",
            "Le paiement doit être chiffré de bout en bout",
        ],
        "priority": "high",
        "dependencies": ["Module caméra", "Gateway de paiement"],
        "ambiguities": [
            "Quels types de QR codes sont supportés (statiques, dynamiques) ?",
            "Quel provider de paiement utiliser ?",
        ],
        "confidence_score": 0.82,
    }, ensure_ascii=False, indent=2)

    # Réponse fictive de conception de tests (module 3 — TestDesignOutput)
    _TEST_DESIGN_EXAMPLE: str = json.dumps({
        "user_stories": [
            {
                "title": "Paiement par scan QR",
                "as_a": "Utilisateur mobile",
                "i_want": "scanner un QR code pour payer",
                "so_that": "je puisse effectuer un paiement rapidement sans saisir de données",
                "acceptance_criteria": [
                    "Le scan démarre en moins de 1 seconde",
                    "Le montant est affiché clairement avant confirmation",
                    "Un message de succès ou d'erreur est affiché",
                ],
                "priority": "high",
            }
        ],
        "gherkin_features": [
            {
                "feature_name": "Paiement par QR Code",
                "description": "En tant qu'utilisateur, je veux payer via QR code",
                "scenarios": [
                    {
                        "name": "Paiement réussi via scan QR",
                        "steps": [
                            {"keyword": "Given", "text": "l'utilisateur est connecté à l'application"},
                            {"keyword": "And", "text": "un QR code de paiement valide est affiché"},
                            {"keyword": "When", "text": "l'utilisateur scanne le QR code"},
                            {"keyword": "Then", "text": "le montant de 25.00 EUR est affiché"},
                            {"keyword": "And", "text": "l'utilisateur confirme le paiement"},
                            {"keyword": "Then", "text": "un reçu de paiement est généré"},
                        ],
                        "tags": ["@smoke", "@payment"],
                    }
                ],
                "tags": ["@feature-qr-payment"],
            }
        ],
        "test_summary": {
            "total_scenarios": 1,
            "coverage_notes": "Couvre le happy path. Tests négatifs (QR invalide, timeout) à ajouter.",
            "suggested_test_types": ["functional", "smoke", "negative"],
        },
    }, ensure_ascii=False, indent=2)

    # Réponses par défaut pour différents cas d'usage. Les clés
    # "requirement analyzer agent" / "test design agent" matchent les
    # system prompts réels des modules 2 et 3 ("Tu es un Requirement
    # Analyzer Agent." / "Tu es un Test Design Agent."), pour que le mock
    # reste utilisable "out of the box" — sans custom_responses. "analyze_requirement" /
    # "generate_tests" sont conservées pour compatibilité (cf. README module 1).
    DEFAULT_RESPONSES: dict[str, str] = {
        "analyze_requirement": _REQUIREMENT_ANALYSIS_EXAMPLE,
        "requirement analyzer agent": _REQUIREMENT_ANALYSIS_EXAMPLE,
        "generate_tests": _TEST_DESIGN_EXAMPLE,
        "test design agent": _TEST_DESIGN_EXAMPLE,
        "default": "Ceci est une réponse fictive du MockProvider. "
                   "En production, cette réponse viendrait d'un vrai LLM.",
    }

    def __init__(self, custom_responses: Optional[dict[str, str]] = None):
        """
        Args:
            custom_responses: Dict de réponses custom à injecter.
                              Clé = mot-clé à chercher dans le prompt,
                              Valeur = réponse à retourner.
                              Prioritaire sur les réponses par défaut.
        """
        self._custom_responses = dict(custom_responses) if custom_responses else {}

    @property
    def provider_name(self) -> str:
        return "mock"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Retourne une réponse pré-définie basée sur le contenu du prompt.

        Cherche un mot-clé dans le system_prompt ou le dernier message pour
        choisir la bonne réponse mock. Les `custom_responses` du constructeur
        sont vérifiées en premier (priorité sur les réponses par défaut),
        pour que les tests puissent surcharger un mot-clé par défaut sans
        ambiguïté.
        """
        # Concaténer le contexte pour chercher un mot-clé
        search_text = ""
        if request.system_prompt:
            search_text += request.system_prompt.lower()
        if request.messages:
            search_text += " " + request.messages[-1].content.lower()

        # Chercher la réponse mock correspondante : custom d'abord, puis defaults
        response_content = self.DEFAULT_RESPONSES["default"]
        matched = False
        for keyword, content in self._custom_responses.items():
            if keyword in search_text:
                response_content = content
                matched = True
                break
        if not matched:
            for keyword, content in self.DEFAULT_RESPONSES.items():
                if keyword != "default" and keyword in search_text:
                    response_content = content
                    break

        logger.debug("FakeLLMProvider: matched keyword for response, length=%d", len(response_content))

        # Simuler un usage de tokens réaliste
        prompt_tokens = sum(len(m.content.split()) for m in request.messages)
        completion_tokens = len(response_content.split())

        return LLMResponse(
            content=response_content,
            model="mock-model",
            provider=self.provider_name,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            raw_response={"mock": True, "matched_keyword": True},
        )
