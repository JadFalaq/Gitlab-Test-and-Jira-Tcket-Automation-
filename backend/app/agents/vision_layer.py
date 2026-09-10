"""
Module 2 (extension) — Vision : décrit factuellement une image jointe par
l'ingénieur (capture d'écran de bug, maquette...), pour l'injecter comme
contexte texte dans le prompt du Requirement Analyzer Agent.

Même pattern que llm_layer.py (module 1) et embedding_layer.py (RAG) :
interface abstraite + providers interchangeables + factory pilotée par
settings. Vit dans app/agents/ (LLM-adjacent), pas dans app/services/.

Design decisions:
    - Prompt de description contraint à l'anti-hallucination du reste du
      projet : décrire factuellement ce qui est visible, jamais interpréter
      ou halluciner une intention au-delà de l'image elle-même. Le
      Requirement Analyzer Agent (module 2) traite ensuite cette
      description exactement comme n'importe quel autre texte de contexte
      (masqué par le DLP, jamais une source de vérité séparée).
    - Modèle par défaut : moondream (~1.7 Go) — choisi pour tenir sur un
      GPU modeste (cf. contrainte VRAM déjà rencontrée avec le LLM de chat,
      voir docs/module_1_llm_layer.md). Un modèle plus gros (llava) donnerait
      de meilleures descriptions mais risquerait la même instabilité OOM que
      llama3.1:8b avant qu'on bascule sur llama3.2:3b.
"""

from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DESCRIPTION_PROMPT = (
    "Décris factuellement le contenu de cette image, en français, pour "
    "aider à comprendre un besoin logiciel (feature, bug, idée) : éléments "
    "d'interface visibles, texte lisible à l'écran, messages d'erreur, mise "
    "en page générale. Ne suppose et n'invente rien au-delà de ce qui est "
    "réellement visible dans l'image."
)


class VisionProviderError(Exception):
    """Erreur générique d'un provider de description d'image."""

    def __init__(self, message: str, provider: str):
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class BaseVisionProvider(ABC):
    """Interface abstraite pour tout provider de description d'image."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def describe(self, image_bytes: bytes) -> str:
        """
        Retourne une description factuelle du contenu de l'image.

        Raises:
            VisionProviderError: en cas d'échec (réseau, modèle absent...)
        """
        ...


class OllamaVisionProvider(BaseVisionProvider):
    """
    Provider de description d'image via Ollama — POST /api/chat avec un
    modèle vision (le champ `images` d'un message accepte une liste
    d'images encodées en base64).
    Docs : https://github.com/ollama/ollama/blob/main/docs/api.md#chat-request-with-images
    """

    def __init__(
        self,
        base_url: str = settings.OLLAMA_BASE_URL,
        model: str = settings.VISION_MODEL,
        timeout: int = settings.OLLAMA_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def describe(self, image_bytes: bytes) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": _DESCRIPTION_PROMPT,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            # Timeout, connexion refusée, 404 modèle absent (`ollama pull
            # moondream` manquant), 5xx... tout remonte en VisionProviderError
            # typée (même contrat que llm_layer.py/embedding_layer.py).
            raise VisionProviderError(
                f"Échec de l'appel vision Ollama : {exc}", provider=self.provider_name
            ) from exc

        content = data.get("message", {}).get("content", "")
        if not content:
            raise VisionProviderError(
                f"Réponse Ollama sans description (modèle '{self.model}' tiré ? "
                f"`ollama pull {self.model}`)",
                provider=self.provider_name,
            )
        return content


class NoOpVisionProvider(BaseVisionProvider):
    """
    Ne décrit rien — utilisé quand VISION_ENABLED=false, et forcé dans toute
    la suite de tests (cf. tests/conftest.py) pour ne jamais dépendre d'un
    Ollama réel avec un modèle vision tiré.
    """

    @property
    def provider_name(self) -> str:
        return "noop"

    async def describe(self, image_bytes: bytes) -> str:
        raise VisionProviderError("Vision désactivée (VISION_ENABLED=false)", provider=self.provider_name)


def get_vision_provider() -> BaseVisionProvider:
    """Factory — lit settings.VISION_ENABLED (True par défaut)."""
    if not settings.VISION_ENABLED:
        return NoOpVisionProvider()
    return OllamaVisionProvider()


__all__ = [
    "BaseVisionProvider",
    "OllamaVisionProvider",
    "NoOpVisionProvider",
    "VisionProviderError",
    "get_vision_provider",
]
