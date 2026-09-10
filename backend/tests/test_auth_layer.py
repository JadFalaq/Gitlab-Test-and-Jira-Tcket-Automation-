"""
Tests unitaires — Module 8 : couche OIDC (app/agents/auth_layer.py)

OidcAuthProvider est testé via httpx.MockTransport (même pattern que
test_vision_layer.py/test_ticket_providers.py), aucun appel réseau réel.

Toutes les données sont fictives.
"""

from __future__ import annotations

import httpx
import pytest

from app.agents.auth_layer import AuthProviderError, OidcAuthProvider

_RealAsyncClient = httpx.AsyncClient

_DISCOVERY_DOC = {
    "authorization_endpoint": "https://sso.example.com/authorize",
    "token_endpoint": "https://sso.example.com/token",
    "userinfo_endpoint": "https://sso.example.com/userinfo",
}


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _provider() -> OidcAuthProvider:
    return OidcAuthProvider(
        issuer="https://sso.example.com",
        client_id="fictional-client-id",
        client_secret="fictional-client-secret",
        timeout=5,
    )


class TestConfigMissing:
    @pytest.mark.asyncio
    async def test_get_authorization_url_raises_without_config(self):
        provider = OidcAuthProvider(issuer=None, client_id=None, client_secret=None)
        with pytest.raises(AuthProviderError):
            await provider.get_authorization_url("state", "http://localhost:8000/api/auth/callback")

    @pytest.mark.asyncio
    async def test_fetch_user_raises_without_config(self):
        provider = OidcAuthProvider(issuer=None, client_id=None, client_secret=None)
        with pytest.raises(AuthProviderError):
            await provider.fetch_user("code", "http://localhost:8000/api/auth/callback")


class TestGetAuthorizationUrl:
    @pytest.mark.asyncio
    async def test_builds_url_from_discovery(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/.well-known/openid-configuration"
            return httpx.Response(200, json=_DISCOVERY_DOC)

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        url = await provider.get_authorization_url("fictional-state", "http://localhost:8000/api/auth/callback")

        assert url.startswith("https://sso.example.com/authorize?")
        assert "client_id=fictional-client-id" in url
        assert "state=fictional-state" in url
        assert "response_type=code" in url

    @pytest.mark.asyncio
    async def test_discovery_unreachable_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("indisponible", request=request)

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        with pytest.raises(AuthProviderError):
            await provider.get_authorization_url("state", "http://localhost:8000/api/auth/callback")


class TestFetchUser:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            if request.url.path == "/token":
                assert request.method == "POST"
                return httpx.Response(200, json={"access_token": "fictional-access-token"})
            if request.url.path == "/userinfo":
                assert request.headers["Authorization"] == "Bearer fictional-access-token"
                return httpx.Response(
                    200,
                    json={
                        "email": "assignee@example.com",
                        "name": "[FICTIF] Assignée Test",
                        "sub": "fictional-subject-id",
                    },
                )
            raise AssertionError(f"URL inattendue : {request.url}")

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        user = await provider.fetch_user("fictional-code", "http://localhost:8000/api/auth/callback")

        assert user.email == "assignee@example.com"
        assert user.display_name == "[FICTIF] Assignée Test"
        assert user.subject == "fictional-subject-id"

    @pytest.mark.asyncio
    async def test_falls_back_to_email_when_name_missing(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/userinfo":
                return httpx.Response(200, json={"email": "assignee@example.com", "sub": "sub-id"})
            raise AssertionError(f"URL inattendue : {request.url}")

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        user = await provider.fetch_user("code", "http://localhost:8000/api/auth/callback")

        assert user.display_name == "assignee@example.com"

    @pytest.mark.asyncio
    async def test_token_exchange_failure_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            return httpx.Response(400, json={"error": "invalid_grant"})

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        with pytest.raises(AuthProviderError):
            await provider.fetch_user("bad-code", "http://localhost:8000/api/auth/callback")

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            if request.url.path == "/token":
                return httpx.Response(200, json={})  # pas d'access_token
            raise AssertionError(f"URL inattendue : {request.url}")

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        with pytest.raises(AuthProviderError):
            await provider.fetch_user("code", "http://localhost:8000/api/auth/callback")

    @pytest.mark.asyncio
    async def test_userinfo_failure_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/userinfo":
                return httpx.Response(401, json={"error": "invalid_token"})
            raise AssertionError(f"URL inattendue : {request.url}")

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        with pytest.raises(AuthProviderError):
            await provider.fetch_user("code", "http://localhost:8000/api/auth/callback")

    @pytest.mark.asyncio
    async def test_missing_email_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "tok"})
            if request.url.path == "/userinfo":
                return httpx.Response(200, json={"sub": "sub-id"})  # pas d'email
            raise AssertionError(f"URL inattendue : {request.url}")

        _patch_async_client(monkeypatch, handler)
        provider = _provider()

        with pytest.raises(AuthProviderError):
            await provider.fetch_user("code", "http://localhost:8000/api/auth/callback")
