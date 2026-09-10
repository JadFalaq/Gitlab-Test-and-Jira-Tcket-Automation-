"""
Tests unitaires — Module 1 : LLM Abstraction Layer

Teste :
- Sérialisation/désérialisation des schémas Pydantic
- MockProvider : réponses correctes, matching de mots-clés
- Factory : provider valide, provider inconnu
- OllamaProvider : gestion d'erreurs (sans Ollama réel)

Toutes les données sont fictives.
"""

import json

import httpx
import pytest

from app.schemas.llm import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from app.agents.llm_layer import (
    BaseLLMProvider,
    MockProvider,
    OllamaProvider,
    get_llm_provider,
    register_provider,
    LLMProviderError,
    LLMConnectionError,
    LLMModelNotFoundError,
    LLMTimeoutError,
    _PROVIDER_REGISTRY,
)

_RealAsyncClient = httpx.AsyncClient


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """
    Remplace httpx.AsyncClient par une version qui route toutes les requêtes
    vers `handler` (httpx.MockTransport) — aucun appel réseau réel, même
    pattern que tests/test_ticket_providers.py.
    """

    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


# ===========================================================================
# Tests des schémas Pydantic
# ===========================================================================


class TestLLMSchemas:
    """Tests de sérialisation/désérialisation des schémas."""

    def test_llm_message_valid(self):
        msg = LLMMessage(role="user", content="Bonjour")
        assert msg.role == "user"
        assert msg.content == "Bonjour"

    def test_llm_message_invalid_role(self):
        with pytest.raises(Exception):  # ValidationError
            LLMMessage(role="invalid_role", content="test")

    def test_llm_message_empty_content_rejected(self):
        with pytest.raises(Exception):
            LLMMessage(role="user", content="")

    def test_llm_request_minimal(self):
        req = LLMRequest(
            messages=[LLMMessage(role="user", content="Test")]
        )
        assert len(req.messages) == 1
        assert req.temperature == 0.3  # Default
        assert req.max_tokens == 4096  # Default
        assert req.response_format is None

    def test_llm_request_with_all_fields(self):
        req = LLMRequest(
            messages=[
                LLMMessage(role="user", content="Analyse cette feature"),
            ],
            system_prompt="Tu es un analyste de requirements.",
            temperature=0.7,
            max_tokens=2048,
            response_format="json",
        )
        assert req.system_prompt is not None
        assert req.temperature == 0.7
        assert req.response_format == "json"

    def test_llm_request_get_messages_with_system(self):
        req = LLMRequest(
            messages=[LLMMessage(role="user", content="Hello")],
            system_prompt="Tu es un assistant.",
        )
        msgs = req.get_messages_with_system()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "Tu es un assistant."
        assert msgs[1]["role"] == "user"

    def test_llm_request_get_messages_without_system(self):
        req = LLMRequest(
            messages=[LLMMessage(role="user", content="Hello")],
        )
        msgs = req.get_messages_with_system()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_llm_request_empty_messages_rejected(self):
        with pytest.raises(Exception):
            LLMRequest(messages=[])

    def test_llm_request_temperature_bounds(self):
        with pytest.raises(Exception):
            LLMRequest(
                messages=[LLMMessage(role="user", content="Test")],
                temperature=3.0,  # > 2.0 max
            )

    def test_llm_usage_defaults(self):
        usage = LLMUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_llm_response_serialization(self):
        resp = LLMResponse(
            content="Test response",
            model="test-model",
            provider="test",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
        data = resp.model_dump()
        assert data["content"] == "Test response"
        assert data["usage"]["total_tokens"] == 30
        assert data["raw_response"] is None

    def test_llm_response_with_raw(self):
        resp = LLMResponse(
            content="Test",
            model="m",
            provider="p",
            raw_response={"debug": True},
        )
        assert resp.raw_response == {"debug": True}


# ===========================================================================
# Tests du MockProvider
# ===========================================================================


class TestMockProvider:
    """Tests du provider factice."""

    @pytest.fixture
    def mock_provider(self):
        return MockProvider()

    @pytest.fixture
    def custom_mock_provider(self):
        return MockProvider(custom_responses={
            "custom_keyword": "Réponse custom fictive",
        })

    @pytest.mark.asyncio
    async def test_mock_provider_name(self, mock_provider):
        assert mock_provider.provider_name == "mock"

    @pytest.mark.asyncio
    async def test_mock_default_response(self, mock_provider):
        """Sans mot-clé reconnu, retourne la réponse par défaut."""
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Quelque chose de random")],
        )
        response = await mock_provider.complete(request)

        assert isinstance(response, LLMResponse)
        assert response.provider == "mock"
        assert response.model == "mock-model"
        assert "fictive" in response.content.lower() or "mock" in response.content.lower()

    @pytest.mark.asyncio
    async def test_mock_analyze_requirement_response(self, mock_provider):
        """Avec le mot-clé 'analyze_requirement', retourne la réponse d'analyse."""
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Ajouter un paiement QR code")],
            system_prompt="Tu dois analyze_requirement et retourner du JSON.",
        )
        response = await mock_provider.complete(request)

        # La réponse doit être du JSON parsable
        parsed = json.loads(response.content)
        assert "title" in parsed
        assert "type" in parsed
        assert parsed["type"] == "feature"

    @pytest.mark.asyncio
    async def test_mock_generate_tests_response(self, mock_provider):
        """Avec le mot-clé 'generate_tests', retourne des tests Gherkin."""
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Génère des tests")],
            system_prompt="Tu dois generate_tests en format Gherkin.",
        )
        response = await mock_provider.complete(request)

        parsed = json.loads(response.content)
        assert "user_stories" in parsed
        assert "gherkin_features" in parsed
        assert "test_summary" in parsed

    @pytest.mark.asyncio
    async def test_mock_custom_response(self, custom_mock_provider):
        """Réponse custom injectée via le constructeur."""
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Teste le custom_keyword")],
        )
        response = await custom_mock_provider.complete(request)
        assert response.content == "Réponse custom fictive"

    @pytest.mark.asyncio
    async def test_mock_usage_metrics(self, mock_provider):
        """Les métriques de tokens sont simulées de manière réaliste."""
        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Un deux trois quatre cinq")],
        )
        response = await mock_provider.complete(request)

        assert response.usage.prompt_tokens > 0
        assert response.usage.completion_tokens > 0
        assert response.usage.total_tokens == (
            response.usage.prompt_tokens + response.usage.completion_tokens
        )

    @pytest.mark.asyncio
    async def test_mock_health_check(self, mock_provider):
        """Le MockProvider est toujours healthy."""
        assert await mock_provider.health_check() is True


# ===========================================================================
# Tests de la Factory
# ===========================================================================


class TestProviderFactory:
    """Tests de la factory get_llm_provider."""

    def test_get_mock_provider(self):
        provider = get_llm_provider("mock")
        assert isinstance(provider, MockProvider)
        assert provider.provider_name == "mock"

    def test_get_ollama_provider(self):
        provider = get_llm_provider("ollama")
        assert isinstance(provider, OllamaProvider)
        assert provider.provider_name == "ollama"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="not found"):
            get_llm_provider("inexistent_provider")

    def test_unknown_provider_lists_available(self):
        with pytest.raises(ValueError, match="mock"):
            get_llm_provider("nope")

    def test_register_custom_provider(self):
        """On peut enregistrer un provider custom dynamiquement."""

        class DummyProvider(BaseLLMProvider):
            @property
            def provider_name(self) -> str:
                return "dummy"

            async def complete(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(
                    content="dummy",
                    model="dummy",
                    provider="dummy",
                )

        register_provider("dummy", DummyProvider)
        provider = get_llm_provider("dummy")
        assert isinstance(provider, DummyProvider)

        # Cleanup
        del _PROVIDER_REGISTRY["dummy"]

    def test_register_non_provider_raises(self):
        """Enregistrer une classe qui n'hérite pas de BaseLLMProvider échoue."""
        with pytest.raises(TypeError):
            register_provider("bad", str)  # type: ignore


# ===========================================================================
# Tests du OllamaProvider (sans Ollama réel)
# ===========================================================================


class TestOllamaProvider:
    """Tests du provider Ollama — sans instance Ollama réelle."""

    @pytest.fixture
    def ollama_provider(self):
        return OllamaProvider(
            base_url="http://localhost:11434",
            model="llama3.1:8b",
            timeout=5,
        )

    def test_ollama_provider_name(self, ollama_provider):
        assert ollama_provider.provider_name == "ollama"

    def test_ollama_config(self, ollama_provider):
        assert ollama_provider.base_url == "http://localhost:11434"
        assert ollama_provider.model == "llama3.1:8b"
        assert ollama_provider.timeout == 5

    @pytest.mark.asyncio
    async def test_ollama_connection_error(self, ollama_provider):
        """Si Ollama n'est pas démarré, on obtient LLMConnectionError."""
        # Utilise un port invalide pour forcer l'erreur
        ollama_provider.base_url = "http://localhost:1"  # port invalide

        request = LLMRequest(
            messages=[LLMMessage(role="user", content="Test")],
        )

        with pytest.raises(LLMConnectionError, match="Cannot connect"):
            await ollama_provider.complete(request)

    @pytest.mark.asyncio
    async def test_ollama_health_check_fails_gracefully(self):
        """Health check retourne False si Ollama est injoignable."""
        provider = OllamaProvider(base_url="http://localhost:1")
        assert await provider.health_check() is False

    @pytest.mark.asyncio
    async def test_ollama_complete_success(self, ollama_provider, monkeypatch):
        """Chemin nominal : payload /api/chat correct et parsing de la réponse."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            payload = json.loads(request.read())
            assert payload["model"] == "llama3.1:8b"
            assert payload["stream"] is False
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "Réponse fictive"},
                    "prompt_eval_count": 12,
                    "eval_count": 7,
                },
            )

        _patch_async_client(monkeypatch, handler)

        response = await ollama_provider.complete(
            LLMRequest(messages=[LLMMessage(role="user", content="Test")])
        )

        assert response.content == "Réponse fictive"
        assert response.provider == "ollama"
        assert response.usage.total_tokens == 19

    @pytest.mark.asyncio
    async def test_ollama_model_not_found(self, ollama_provider, monkeypatch):
        """404 Ollama (modèle non tiré) -> LLMModelNotFoundError."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        _patch_async_client(monkeypatch, handler)

        with pytest.raises(LLMModelNotFoundError):
            await ollama_provider.complete(
                LLMRequest(messages=[LLMMessage(role="user", content="Test")])
            )

    @pytest.mark.asyncio
    async def test_ollama_http_error_raises_provider_error(self, ollama_provider, monkeypatch):
        """500 Ollama -> LLMProviderError typée, jamais httpx.HTTPStatusError brute."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom interne")

        _patch_async_client(monkeypatch, handler)

        with pytest.raises(LLMProviderError, match="HTTP 500"):
            await ollama_provider.complete(
                LLMRequest(messages=[LLMMessage(role="user", content="Test")])
            )

    @pytest.mark.asyncio
    async def test_ollama_timeout_raises_timeout_error(self, ollama_provider, monkeypatch):
        """Timeout de lecture -> LLMTimeoutError (pas httpx.ReadTimeout brute)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("trop lent", request=request)

        _patch_async_client(monkeypatch, handler)

        with pytest.raises(LLMTimeoutError):
            await ollama_provider.complete(
                LLMRequest(messages=[LLMMessage(role="user", content="Test")])
            )

    @pytest.mark.asyncio
    async def test_ollama_transport_error_raises_connection_error(self, ollama_provider, monkeypatch):
        """Erreur transport en vol (ReadError) -> LLMConnectionError via le
        filet httpx.HTTPError, pour que les agents (modules 2/3) ne voient
        jamais d'exception httpx brute."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("connexion coupée", request=request)

        _patch_async_client(monkeypatch, handler)

        with pytest.raises(LLMConnectionError, match="Transport error"):
            await ollama_provider.complete(
                LLMRequest(messages=[LLMMessage(role="user", content="Test")])
            )


# ===========================================================================
# Tests des exceptions
# ===========================================================================


class TestExceptions:
    """Tests des exceptions typées."""

    def test_llm_provider_error(self):
        err = LLMProviderError("Something failed", provider="test")
        assert "test" in str(err)
        assert err.provider == "test"
        assert err.details == {}

    def test_llm_provider_error_with_details(self):
        err = LLMProviderError(
            "Failed", provider="ollama", details={"code": 500}
        )
        assert err.details["code"] == 500

    def test_llm_timeout_error_is_provider_error(self):
        """LLMTimeoutError hérite de LLMProviderError."""
        from app.agents.llm_layer import LLMTimeoutError
        err = LLMTimeoutError("Timeout", provider="ollama")
        assert isinstance(err, LLMProviderError)

    def test_llm_connection_error_is_provider_error(self):
        err = LLMConnectionError("No connection", provider="ollama")
        assert isinstance(err, LLMProviderError)
