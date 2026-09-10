"""
Module 2 — Requirement Analyzer Agent

Comprend la demande brute d'un ingénieur (texte libre / chat) et la
transforme en JSON structuré (RequirementAnalysis) exploitable par le
module 3 (Test Design Agent).

Architecture:
    RequirementAnalyzerAgent
        - Construit un LLMRequest (system prompt + demande utilisateur)
        - Appelle le LLM via le module 1 (LLM Abstraction Layer)
        - Parse et valide la réponse JSON contre le schéma RequirementAnalysis
        - Retente avec un message correctif si le JSON est invalide
          (jusqu'à settings.LLM_MAX_RETRIES fois)

Design decisions:
    - Le system prompt interdit explicitement d'inventer des informations
      absentes du texte fourni : tout ce qui n'est pas déductible doit
      apparaître dans `ambiguities`, jamais halluciné dans les autres champs.
    - Les ambiguïtés ne bloquent jamais l'analyse (cf. schemas/requirement.py) :
      c'est le module 4 qui arbitre avec l'ingénieur.
    - Le parsing JSON est isolé dans `_parse_response` pour être testable
      indépendamment de l'appel LLM.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import ValidationError

from app.agents.dlp_masking import BaseMaskingProvider, get_masking_provider
from app.agents.llm_layer import BaseLLMProvider, LLMProviderError, get_llm_provider
from app.config import settings
from app.schemas.llm import LLMMessage, LLMRequest
from app.schemas.requirement import RequirementAnalysis, RequirementAnalysisRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RequirementAnalysisError(Exception):
    """Levée quand l'agent ne parvient pas à produire une analyse valide."""

    def __init__(self, message: str, raw_content: Optional[str] = None):
        self.raw_content = raw_content
        super().__init__(message)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Tu es un Requirement Analyzer Agent. Ton rôle est de lire la demande \
brute d'un ingénieur (feature, bug, idée) et de la structurer en JSON, sans jamais \
inventer d'information qui ne serait pas présente ou clairement déductible du texte fourni.

Règles strictes :
1. Si une information nécessaire à un champ n'est ni fournie ni déductible avec \
certitude, NE L'INVENTE PAS. Ajoute plutôt une question précise dans "ambiguities".
2. "ambiguities" n'est jamais bloquant : liste les points flous, ne refuse jamais \
de produire une analyse.
3. Réponds UNIQUEMENT avec un objet JSON valide respectant exactement ce schéma \
(aucun texte avant/après, aucun bloc markdown) :

{
  "title": "string, titre court et clair",
  "type": "feature" | "bug" | "improvement" | "other",
  "description": "string, reformulation claire du besoin",
  "user_personas": ["string", ...],
  "functional_criteria": ["string", ...],
  "non_functional_criteria": ["string", ...],
  "priority": "low" | "medium" | "high" | "critical",
  "dependencies": ["string", ...],
  "ambiguities": ["string", ...],
  "confidence_score": nombre entre 0.0 et 1.0
}

Si un tableau n'a pas d'éléments pertinents, renvoie un tableau vide [], jamais null."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class RequirementAnalyzerAgent:
    """Analyse une demande brute d'ingénieur et produit un RequirementAnalysis."""

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
        masking_provider: Optional[BaseMaskingProvider] = None,
    ):
        self._provider = llm_provider or get_llm_provider()
        self._masker = masking_provider or get_masking_provider()

    async def analyze(
        self,
        request: RequirementAnalysisRequest,
        examples: Optional[list[dict]] = None,
    ) -> RequirementAnalysis:
        """
        Analyse la demande et retourne un RequirementAnalysis validé.

        DLP : `request.user_input`/`request.context` sont masqués (cf.
        app/agents/dlp_masking.py) AVANT toute construction de message LLM —
        seul point d'entrée du texte brut de l'ingénieur dans le pipeline,
        le masquage se propage donc naturellement au module 3 puis aux
        tickets Jira/GitLab (module 5) sans logique dupliquée.

        RAG (few-shot) : `examples` (optionnel) est une liste de
        RequirementAnalysis passés, déjà validés par un humain, retrouvés
        par similarité sémantique avec la demande courante (cf.
        app/services/rag_service.py, appelé par session_service.py — cet
        agent ne fait AUCUN accès DB/embedding lui-même, il se contente
        d'injecter ce qu'on lui fournit dans le prompt). Le system prompt
        interdit déjà d'inventer hors du texte fourni ; le message utilisateur
        précise en plus explicitement de ne jamais réutiliser le contenu des
        exemples (seulement leur structure/niveau de détail), pour ne pas
        rompre ce principe anti-hallucination avec une contamination
        few-shot.

        Raises:
            RequirementAnalysisError: si le LLM ne produit pas de JSON valide
                après settings.LLM_MAX_RETRIES tentatives supplémentaires.
        """
        masked_request = RequirementAnalysisRequest(
            user_input=self._masker.mask(request.user_input),
            context=self._masker.mask(request.context) if request.context else None,
        )
        messages = [LLMMessage(role="user", content=self._build_user_message(masked_request, examples))]
        last_error: Optional[str] = None
        last_content: Optional[str] = None

        attempts = settings.LLM_MAX_RETRIES + 1
        for attempt in range(1, attempts + 1):
            llm_request = LLMRequest(
                messages=messages,
                system_prompt=_SYSTEM_PROMPT,
                temperature=settings.LLM_DEFAULT_TEMPERATURE,
                max_tokens=settings.LLM_DEFAULT_MAX_TOKENS,
                response_format="json",
            )

            try:
                response = await self._provider.complete(llm_request)
            except LLMProviderError as exc:
                # Erreur d'infrastructure (timeout, connexion, modèle absent) :
                # pas de retry, ça ne se résoudra pas tout seul dans la même requête.
                logger.warning("RequirementAnalyzerAgent: LLM provider error: %s", exc)
                raise RequirementAnalysisError(
                    f"Échec de l'appel au LLM : {exc}", raw_content=None
                ) from exc

            last_content = response.content

            try:
                return self._parse_response(response.content)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)
                logger.warning(
                    "RequirementAnalyzerAgent: invalid JSON on attempt %d/%d: %s",
                    attempt,
                    attempts,
                    last_error,
                )
                # Message correctif pour la tentative suivante
                messages.append(LLMMessage(role="assistant", content=response.content))
                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            f"Le JSON précédent était invalide ({last_error}). "
                            "Renvoie UNIQUEMENT un JSON valide respectant exactement "
                            "le schéma demandé, sans texte ni markdown autour."
                        ),
                    )
                )

        raise RequirementAnalysisError(
            f"Échec de l'analyse après {attempts} tentatives : {last_error}",
            raw_content=last_content,
        )

    @staticmethod
    def _build_user_message(
        request: RequirementAnalysisRequest,
        examples: Optional[list[dict]] = None,
    ) -> str:
        if not examples:
            # Comportement identique à avant l'ajout du RAG : pas de bloc
            # "Demande :" superflu quand il n'y a pas d'exemples à séparer
            # visuellement de la vraie demande.
            if request.context:
                return f"Contexte additionnel :\n{request.context}\n\nDemande :\n{request.user_input}"
            return request.user_input

        parts: list[str] = [
            "Exemples d'analyses précédentes, déjà validées par un ingénieur — "
            "UNIQUEMENT pour t'indiquer le format et le niveau de détail attendus. "
            "Ces exemples concernent d'AUTRES demandes : n'en réutilise jamais le "
            "contenu (titre, description, critères...) dans ta réponse."
        ]
        for i, example in enumerate(examples, start=1):
            parts.append(f"Exemple {i} :\n{json.dumps(example, ensure_ascii=False)}")
        parts.append("---")
        if request.context:
            parts.append(f"Contexte additionnel :\n{request.context}")
        parts.append(f"Demande :\n{request.user_input}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(content: str) -> RequirementAnalysis:
        """
        Parse et valide le contenu brut du LLM contre RequirementAnalysis.

        Séparé de `analyze()` pour être testable sans appel LLM.
        """
        data = json.loads(content)
        return RequirementAnalysis(**data)
