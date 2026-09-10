"""
Module 2 (extension) — Embeddings : couche d'abstraction pour la génération
de vecteurs d'embedding, même principe que llm_layer.py (module 1) :
interface abstraite + providers interchangeables + factory pilotée par
settings.

Utilisé par app/services/rag_service.py pour le RAG few-shot : retrouver,
parmi les générations passées acceptées par un humain (GenerationModel),
celles dont la demande initiale est sémantiquement proche de la nouvelle
demande, à injecter comme exemples dans le prompt du module 2
(RequirementAnalyzerAgent).
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingProviderError(Exception):
    """Erreur générique d'un provider d'embeddings."""

    def __init__(self, message: str, provider: str):
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class BaseEmbeddingProvider(ABC):
    """Interface abstraite pour tout provider d'embeddings."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """
        Retourne le vecteur d'embedding de `text`.

        Raises:
            EmbeddingProviderError: en cas d'échec (réseau, modèle absent...)
        """
        ...


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """
    Provider d'embeddings via Ollama — POST /api/embeddings.
    Docs : https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings

    Nécessite un modèle d'embedding tiré au préalable :
        ollama pull nomic-embed-text
    """

    def __init__(
        self,
        base_url: str = settings.OLLAMA_BASE_URL,
        model: str = settings.EMBEDDING_MODEL,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def embed(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            # Timeout, connexion refusée, 404 modèle absent, 5xx... tout
            # remonte en EmbeddingProviderError typée (même contrat que
            # llm_layer.py) — rag_service.py convertit ça en "pas d'exemples
            # few-shot", jamais en échec de l'analyse elle-même.
            raise EmbeddingProviderError(
                f"Échec de l'appel d'embedding Ollama : {exc}", provider=self.provider_name
            ) from exc

        embedding = data.get("embedding")
        if not embedding:
            raise EmbeddingProviderError(
                f"Réponse Ollama sans embedding (modèle '{self.model}' tiré ? `ollama pull {self.model}`)",
                provider=self.provider_name,
            )
        return embedding


class NoOpEmbeddingProvider(BaseEmbeddingProvider):
    """
    Ne calcule aucun embedding sémantique réel — vecteur déterministe dérivé
    d'un hash du texte (stable, mais sans signification sémantique). Utilisé
    quand RAG_ENABLED=false, et forcé dans toute la suite de tests (cf.
    tests/conftest.py) pour ne jamais dépendre d'un Ollama réel.
    """

    @property
    def provider_name(self) -> str:
        return "noop"

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[:16]]


def get_embedding_provider() -> BaseEmbeddingProvider:
    """Factory — lit settings.RAG_ENABLED (True par défaut)."""
    if not settings.RAG_ENABLED:
        return NoOpEmbeddingProvider()
    return OllamaEmbeddingProvider()


__all__ = [
    "BaseEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "NoOpEmbeddingProvider",
    "EmbeddingProviderError",
    "get_embedding_provider",
]
