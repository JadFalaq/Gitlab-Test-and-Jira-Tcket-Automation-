"""
Module 9 — Chat Agent : clarification conversationnelle avant l'analyse
structurée du module 2.

Contexte : retour utilisateur du 22/07/2026 — le pipeline précédent
enchaînait directement "une génération -> un bouton Valider", avec un LLM
"confiant" même quand le résultat pouvait être hallucinant, sans laisser à
l'ingénieur l'occasion de discuter/préciser avant qu'un résultat structuré
n'existe. Ce module ajoute une phase de conversation libre (statut
'chatting', cf. app/schemas/session.py) avant tout appel au Requirement
Analyzer Agent (module 2).

Architecture:
    ChatAgent.reply(history) -> str
        - Construit un LLMRequest à partir de TOUT l'historique de la
          conversation (pas de retry, pas de format JSON — une réponse
          conversationnelle n'a rien à valider structurellement)
        - Appelle le LLM via le module 1 (LLM Abstraction Layer)
        - Retourne le texte de la réponse tel quel

Design decisions:
    - Aucune génération de JSON structuré ici, même si on le lui demande
      explicitement (règle du system prompt) — c'est le rôle du module 2,
      déclenché uniquement par un geste explicite de l'ingénieur
      (POST .../chat/finalize, cf. session_service.py).
    - Pas de retry sur échec LLM (contrairement aux modules 2/3) : une
      réponse conversationnelle n'a pas de schéma à satisfaire, un échec est
      soit transitoire (l'ingénieur peut renvoyer son message), soit
      persistant (retry immédiat inutile).
    - DLP : chaque message `role="user"` de l'historique est masqué (cf.
      app/agents/dlp_masking.py) au moment de construire la requête LLM,
      jamais au moment du stockage — même principe que
      RequirementAnalyzerAgent.analyze() : `chat_history` est stocké tel
      quel en base, seul ce qui part réellement vers le LLM est masqué.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.agents.dlp_masking import BaseMaskingProvider, get_masking_provider
from app.agents.llm_layer import BaseLLMProvider, LLMProviderError, get_llm_provider
from app.schemas.llm import LLMMessage, LLMRequest
from app.schemas.session import ChatMessage

logger = logging.getLogger(__name__)


class ChatAgentError(Exception):
    """Échec d'infrastructure (LLM injoignable, modèle absent...)."""


_SYSTEM_PROMPT = """Tu es un assistant qui aide un ingénieur à clarifier une demande \
(feature, bug, idée) AVANT qu'elle soit transformée en spécification structurée par un \
autre agent, plus tard, à la demande explicite de l'ingénieur. Ton rôle ICI est \
UNIQUEMENT de discuter — jamais de produire un résultat final structuré, une liste \
exhaustive de critères, ni un objet JSON.

Règles strictes :
1. Ne prétends jamais avoir une réponse certaine si l'information n'a pas été donnée par \
l'ingénieur — dis que tu ne sais pas et pose une question précise plutôt que d'inventer \
ou de deviner.
2. Pose UNE ou deux questions de clarification à la fois, sur les points réellement flous \
(utilisateurs cibles, cas limites, contraintes non-fonctionnelles, priorité) — jamais une \
liste de dix questions qui submerge l'ingénieur d'un coup.
3. Réponds en français, dans un style naturel et engagé, sans listes à puces \
systématiques ni ton robotique : une vraie conversation, pas un formulaire à remplir.
4. Reste concis, en général 2 à 4 phrases.
5. Ne réponds JAMAIS en JSON ni avec un format structuré, même si on te le demande \
explicitement — ce n'est pas ton rôle dans cette conversation."""


class ChatAgent:
    """Génère la prochaine réponse conversationnelle à partir de l'historique
    complet de la conversation de clarification (module 9)."""

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
        masking_provider: Optional[BaseMaskingProvider] = None,
    ):
        self._provider = llm_provider or get_llm_provider()
        self._masker = masking_provider or get_masking_provider()

    async def reply(self, history: list[ChatMessage]) -> str:
        """
        Raises:
            ChatAgentError: échec d'infrastructure lors de l'appel LLM.
        """
        messages = [
            LLMMessage(
                role=message.role,
                content=self._masker.mask(message.content) if message.role == "user" else message.content,
            )
            for message in history
        ]
        llm_request = LLMRequest(
            messages=messages,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.7,  # plus élevé que les modules 2/3 (0.3) : conversation, pas structuré
            max_tokens=600,  # réponses volontairement courtes, cf. règle 4 du system prompt
        )

        try:
            response = await self._provider.complete(llm_request)
        except LLMProviderError as exc:
            logger.warning("ChatAgent: LLM provider error: %s", exc)
            raise ChatAgentError(f"Échec de l'appel au LLM : {exc}") from exc

        return response.content.strip()


__all__ = ["ChatAgent", "ChatAgentError"]
