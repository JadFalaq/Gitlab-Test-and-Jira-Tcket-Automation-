"""
Module 1 — LLM Abstraction Layer

Isole le reste du système du provider LLM. Tout appel au LLM passe
par cette couche, qui traduit les LLMRequest/LLMResponse normalisés
vers l'API native du provider configuré.

Architecture:
    BaseLLMProvider (ABC)
        ├── OllamaProvider   — appelle Ollama local (http://localhost:11434)
        └── MockProvider     — réponses pré-définies pour les tests

    get_llm_provider(name) — Factory qui retourne le bon provider

Design decisions:
    - Async par défaut (FastAPI est async)
    - Le MockProvider permet de tester tout le pipeline sans LLM réel
    - Le OllamaProvider utilise httpx (async HTTP client) pour appeler
      l'API Ollama documentée : https://github.com/ollama/ollama/blob/main/docs/api.md
    - Les erreurs sont typées (LLMProviderError, LLMTimeoutError, etc.)
      pour que les modules appelants puissent réagir spécifiquement.
"""

from __future__ import annotations

import json
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
        """Nom unique du provider (ex: 'ollama', 'mock')."""
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
# Mock Provider (pour les tests)
# ---------------------------------------------------------------------------

class MockProvider(BaseLLMProvider):
    """
    Provider factice qui renvoie des réponses pré-définies.

    Utilisé pour :
    - Les tests unitaires (pas besoin d'Ollama)
    - Le développement du frontend sans backend LLM
    - La démonstration du pipeline complet

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
    # reste utilisable "out of the box" — sans custom_responses — en dev
    # frontend ou en test manuel via l'API. "analyze_requirement" /
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

        logger.debug("MockProvider: matched keyword for response, length=%d", len(response_content))

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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Registre des providers disponibles
_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "ollama": OllamaProvider,
    "mock": MockProvider,
}


def get_llm_provider(provider_name: Optional[str] = None) -> BaseLLMProvider:
    """
    Factory qui retourne une instance du provider LLM demandé.

    Args:
        provider_name: Nom du provider ("ollama", "mock").
                       Si None, utilise la valeur de LLM_PROVIDER dans la config.

    Returns:
        Instance du provider

    Raises:
        ValueError: Si le provider demandé n'est pas dans le registre

    Exemples:
        >>> provider = get_llm_provider("mock")
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
