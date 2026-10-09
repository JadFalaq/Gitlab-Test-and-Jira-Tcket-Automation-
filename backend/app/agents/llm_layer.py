"""
Module 1 — LLM Abstraction Layer

Isole le reste du système du provider LLM. Tout appel au LLM passe
par cette couche, qui traduit les LLMRequest/LLMResponse normalisés
vers l'API native du provider configuré.

Architecture:
    BaseLLMProvider (ABC)
        └── OllamaProvider   — appelle Ollama (OLLAMA_BASE_URL)

    get_llm_provider(name) — Factory qui retourne le bon provider

    Le faux provider utilisé par les tests vit dans tests/fakes/llm.py : il
    n'est jamais enregistré dans l'application.

Design decisions:
    - Async par défaut (FastAPI est async)
    - Le OllamaProvider utilise httpx (async HTTP client) pour appeler
      l'API Ollama documentée : https://github.com/ollama/ollama/blob/main/docs/api.md
    - Les erreurs sont typées (LLMProviderError, LLMTimeoutError, etc.)
      pour que les modules appelants puissent réagir spécifiquement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import settings
from app.schemas.llm import LLMRequest, LLMResponse, LLMUsage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions spécifiques au LLM Layer
# ---------------------------------------------------------------------------

class LLMProviderError(Exception):
    """Erreur générique du provider LLM."""

    def __init__(self, message: str, provider: str, details: Optional[dict] = None):
        self.provider = provider
        self.details = details or {}
        super().__init__(f"[{provider}] {message}")


class LLMTimeoutError(LLMProviderError):
    """Le provider LLM n'a pas répondu dans le délai imparti."""
    pass


class LLMConnectionError(LLMProviderError):
    """Impossible de se connecter au provider LLM."""
    pass


class LLMModelNotFoundError(LLMProviderError):
    """Le modèle demandé n'est pas disponible sur le provider."""
    pass


# ---------------------------------------------------------------------------
# Base Provider (ABC)
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """
    Interface abstraite pour tous les providers LLM.

    Tout nouveau provider (OpenAI, Azure, Anthropic...) doit implémenter
    cette interface. Le reste du système n'interagit jamais directement
    avec l'API native du provider.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nom unique du provider (ex: 'ollama')."""
        ...

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Envoie une requête au LLM et retourne une réponse normalisée.

        Args:
            request: Requête normalisée (messages, température, etc.)

        Returns:
            LLMResponse normalisée

        Raises:
            LLMProviderError: En cas d'erreur du provider
            LLMTimeoutError: Si le provider ne répond pas à temps
            LLMConnectionError: Si le provider est injoignable
        """
        ...

    async def health_check(self) -> bool:
        """
        Vérifie que le provider est accessible et fonctionnel.
        Retourne True si OK, False sinon.
        """
        return True


# ---------------------------------------------------------------------------
# Ollama Provider
# ---------------------------------------------------------------------------

class OllamaProvider(BaseLLMProvider):
    """
    Provider LLM pour Ollama (modèle local).

    Appelle l'API Ollama via HTTP :
    - POST /api/chat — Génération de réponse (mode chat)
      Docs : https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion
    - GET /api/tags — Liste des modèles disponibles
      Docs : https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models
    """

    def __init__(
        self,
        base_url: str = settings.OLLAMA_BASE_URL,
        model: str = settings.OLLAMA_MODEL,
        timeout: int = settings.OLLAMA_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Appelle POST /api/chat sur Ollama.

        Le payload suit le format documenté :
        {
            "model": "llama3.1:8b",
            "messages": [{"role": "user", "content": "..."}],
            "stream": false,
            "options": {"temperature": 0.3, "num_predict": 4096},
            "format": "json"  // optionnel
        }
        """
        messages = request.get_messages_with_system()

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,  # Pas de streaming pour simplifier le prototype
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }

        # Demander du JSON si spécifié
        if request.response_format == "json":
            payload["format"] = "json"

        logger.debug(
            "Ollama request: model=%s, messages=%d, temp=%.2f",
            self.model,
            len(messages),
            request.temperature,
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # POST /api/chat — Ollama Chat Completion API
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )

            if response.status_code == 404:
                raise LLMModelNotFoundError(
                    f"Model '{self.model}' not found. Run: ollama pull {self.model}",
                    provider=self.provider_name,
                    details={"model": self.model, "status_code": 404},
                )

            response.raise_for_status()
            data = response.json()

        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"Ollama did not respond within {self.timeout}s",
                provider=self.provider_name,
                details={"timeout": self.timeout},
            ) from exc

        except httpx.ConnectError as exc:
            raise LLMConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?",
                provider=self.provider_name,
                details={"base_url": self.base_url},
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self.provider_name,
                details={"status_code": exc.response.status_code},
            ) from exc

        except httpx.HTTPError as exc:
            # Filet de sécurité : les erreurs transport restantes (ReadError,
            # RemoteProtocolError, ...) ne doivent jamais traverser la couche
            # LLM en exception httpx brute — les agents (modules 2/3) ne
            # savent réagir qu'aux LLMProviderError typées.
            raise LLMConnectionError(
                f"Transport error while calling Ollama: {type(exc).__name__}: {exc}",
                provider=self.provider_name,
                details={"error_type": type(exc).__name__},
            ) from exc

        # Parse la réponse Ollama
        # Format documenté: {"message": {"role": "assistant", "content": "..."}, ...}
        content = data.get("message", {}).get("content", "")

        # Ollama renvoie les métriques de tokens dans ces champs
        usage = LLMUsage(
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        )

        logger.info(
            "Ollama response: model=%s, tokens=%d, content_length=%d",
            self.model,
            usage.total_tokens,
            len(content),
        )

        return LLMResponse(
            content=content,
            model=self.model,
            provider=self.provider_name,
            usage=usage,
            raw_response=data,
        )

    async def health_check(self) -> bool:
        """
        Vérifie qu'Ollama est accessible via GET /api/tags.
        """
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            # Un health check ne lève jamais : toute erreur httpx (connexion,
            # timeout, transport) signifie simplement "indisponible".
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Registre des providers disponibles
_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "ollama": OllamaProvider,
}


def get_llm_provider(provider_name: Optional[str] = None) -> BaseLLMProvider:
    """
    Factory qui retourne une instance du provider LLM demandé.

    Args:
        provider_name: Nom du provider ("ollama").
                       Si None, utilise la valeur de LLM_PROVIDER dans la config.

    Returns:
        Instance du provider

    Raises:
        ValueError: Si le provider demandé n'est pas dans le registre

    Exemples:
        >>> provider = get_llm_provider("ollama")
        >>> provider = get_llm_provider()  # utilise LLM_PROVIDER de .env
    """
    name = provider_name or settings.LLM_PROVIDER

    if name not in _PROVIDER_REGISTRY:
        available = ", ".join(sorted(_PROVIDER_REGISTRY.keys()))
        raise ValueError(
            f"LLM provider '{name}' not found. "
            f"Available providers: {available}. "
            f"Set LLM_PROVIDER env var to one of these values."
        )

    logger.info("Initializing LLM provider: %s", name)
    return _PROVIDER_REGISTRY[name]()


def register_provider(name: str, provider_class: type[BaseLLMProvider]) -> None:
    """
    Enregistre un nouveau provider dans le registre.

    Utile pour ajouter dynamiquement des providers (ex: plugin system)
    sans modifier ce fichier.

    Args:
        name: Nom unique du provider
        provider_class: Classe du provider (doit hériter de BaseLLMProvider)
    """
    if not issubclass(provider_class, BaseLLMProvider):
        raise TypeError(f"{provider_class} must inherit from BaseLLMProvider")
    _PROVIDER_REGISTRY[name] = provider_class
    logger.info("Registered LLM provider: %s", name)
