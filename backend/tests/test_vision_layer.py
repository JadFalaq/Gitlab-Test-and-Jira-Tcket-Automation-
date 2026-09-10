"""
Tests unitaires — Module 2 (extension) : Vision (app/agents/vision_layer.py)

OllamaVisionProvider est testé via httpx.MockTransport (même pattern que
test_llm_layer.py/test_ticket_providers.py), aucun appel réseau réel.

Toutes les données sont fictives.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.agents.vision_layer import (
    NoOpVisionProvider,
    OllamaVisionProvider,
    VisionProviderError,
    get_vision_provider,
)
from app.config import settings

_RealAsyncClient = httpx.AsyncClient


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


class TestNoOpVisionProvider:
    @pytest.mark.asyncio
    async def test_always_raises(self):
        with pytest.raises(VisionProviderError):
            await NoOpVisionProvider().describe(b"fake-image-bytes")


class TestOllamaVisionProvider:
    @pytest.mark.asyncio
    async def test_describe_success(self, monkeypatch):
        image_bytes = b"[FICTIF octets d'image factices]"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            payload = json.loads(request.read())
            assert payload["model"] == "moondream"
            assert payload["stream"] is False
            sent_message = payload["messages"][0]
            assert sent_message["images"] == [base64.b64encode(image_bytes).decode("ascii")]
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": "[FICTIF] Capture d'écran d'un formulaire."}},
            )

        provider = OllamaVisionProvider(base_url="http://localhost:11434", model="moondream", timeout=5)
        _patch_async_client(monkeypatch, handler)

        description = await provider.describe(image_bytes)

        assert description == "[FICTIF] Capture d'écran d'un formulaire."

    @pytest.mark.asyncio
    async def test_model_not_found_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        provider = OllamaVisionProvider(base_url="http://localhost:11434", model="moondream", timeout=5)
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(VisionProviderError):
            await provider.describe(b"x")

    @pytest.mark.asyncio
    async def test_empty_response_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

        provider = OllamaVisionProvider(base_url="http://localhost:11434", model="moondream", timeout=5)
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(VisionProviderError):
            await provider.describe(b"x")

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("indisponible", request=request)

        provider = OllamaVisionProvider(base_url="http://localhost:11434", model="moondream", timeout=5)
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(VisionProviderError):
            await provider.describe(b"x")


class TestGetVisionProvider:
    def test_returns_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "VISION_ENABLED", False)
        assert isinstance(get_vision_provider(), NoOpVisionProvider)

    def test_returns_ollama_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "VISION_ENABLED", True)
        assert isinstance(get_vision_provider(), OllamaVisionProvider)
