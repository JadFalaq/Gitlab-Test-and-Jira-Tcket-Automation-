"""
Module 1 — LLM Abstraction Layer : Schémas d'interface (Input/Output)

Ces schémas normalisent la communication avec n'importe quel LLM provider.
Tout module qui a besoin d'appeler un LLM utilise LLMRequest/LLMResponse,
jamais l'API native du provider directement.

Design decisions:
- Les messages suivent le format OpenAI-like (role + content) car c'est
  devenu un standard de facto que la plupart des providers supportent.
- Le champ `response_format` permet de demander du JSON structuré quand
  le provider le supporte (Ollama le supporte via le paramètre `format`).
- `raw_response` conserve la réponse brute pour le debug sans polluer
  l'interface normalisée.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Message (une entrée dans l'historique de conversation)
# ---------------------------------------------------------------------------

class LLMMessage(BaseModel):
    """Un message dans l'historique de conversation envoyé au LLM."""

    role: Literal["system", "user", "assistant"] = Field(
        ...,
        description="Rôle de l'auteur du message",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Contenu textuel du message",
    )


# ---------------------------------------------------------------------------
# Request (entrée normalisée vers le LLM)
# ---------------------------------------------------------------------------

class LLMRequest(BaseModel):
    """
    Requête normalisée vers n'importe quel LLM provider.

    Tout appel au LLM depuis les modules 2-7 passe par ce schéma.
    Le LLM Layer (module 1) traduit ce schéma vers l'API native
    du provider configuré.
    """

    messages: list[LLMMessage] = Field(
        ...,
        min_length=1,
        description="Historique de conversation (au moins 1 message)",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description=(
            "Prompt système optionnel. S'il est fourni, il est ajouté "
            "automatiquement en tête des messages avec role='system'."
        ),
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Température de sampling. Basse (0.1-0.3) pour du structuré.",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=32768,
        description="Nombre maximum de tokens dans la réponse",
    )
    response_format: Optional[Literal["json", "text"]] = Field(
        default=None,
        description=(
            "Format de réponse souhaité. 'json' demande au LLM de répondre "
            "en JSON valide (si le provider le supporte)."
        ),
    )

    def get_messages_with_system(self) -> list[dict]:
        """
        Retourne la liste de messages avec le system_prompt injecté
        en première position s'il est défini.
        """
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend([m.model_dump() for m in self.messages])
        return msgs


# ---------------------------------------------------------------------------
# Usage (métriques de consommation de tokens)
# ---------------------------------------------------------------------------

class LLMUsage(BaseModel):
    """Métriques de consommation de tokens pour un appel LLM."""

    prompt_tokens: int = Field(default=0, ge=0, description="Tokens dans le prompt")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens dans la réponse")
    total_tokens: int = Field(default=0, ge=0, description="Total de tokens consommés")


# ---------------------------------------------------------------------------
# Response (sortie normalisée du LLM)
# ---------------------------------------------------------------------------

class LLMResponse(BaseModel):
    """
    Réponse normalisée depuis n'importe quel LLM provider.

    Tous les modules consomment ce schéma — jamais la réponse brute
    du provider directement.
    """

    content: str = Field(
        ...,
        description="Texte de la réponse du LLM",
    )
    model: str = Field(
        ...,
        description="Nom du modèle utilisé (ex: 'llama3.1:8b')",
    )
    provider: str = Field(
        ...,
        description="Nom du provider (ex: 'ollama', 'mock')",
    )
    usage: LLMUsage = Field(
        default_factory=LLMUsage,
        description="Métriques de consommation de tokens",
    )
    raw_response: Optional[dict] = Field(
        default=None,
        description="Réponse brute du provider pour le debug",
    )
