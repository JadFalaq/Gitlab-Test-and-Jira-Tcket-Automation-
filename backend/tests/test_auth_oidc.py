"""
Tests unitaires — Module 8 : fournisseur SSO OIDC (app/auth/oidc.py)

OidcAuthProvider est testé via httpx.MockTransport (même pattern que
test_vision_layer.py/test_ticket_providers.py), aucun appel réseau réel.
Les id_token sont de vrais JWT RS256 signés par une clé RSA générée pour la
session de test ; le faux fournisseur publie la clé publique en JWKS.

Toutes les données sont fictives.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.oidc import AuthProviderError, OidcAuthProvider

_RealAsyncClient = httpx.AsyncClient

_ISSUER = "https://sso.example.com"
_CLIENT_ID = "fictional-client-id"
_DISCOVERY_DOC = {
    "issuer": _ISSUER,
    "authorization_endpoint": "https://sso.example.com/authorize",
    "token_endpoint": "https://sso.example.com/token",
    "userinfo_endpoint": "https://sso.example.com/userinfo",
    "jwks_uri": "https://sso.example.com/jwks",
}

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "fictional-key-1"


def _jwks(private_key=_SIGNING_KEY, kid: str = _KID) -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "RS256"}]}


def _id_token(private_key=_SIGNING_KEY, kid: str = _KID, **claim_overrides) -> str:
    """id_token RS256 valide ; un claim passé à None est retiré."""
    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "fictional-subject-id",
        "iat": now,
        "exp": now + 300,
        "email": "assignee@example.com",
        "name": "[FICTIF] Assignée Test",
        **claim_overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _sso_handler(
    token_json: dict | None = None,
    userinfo_json: dict | None = None,
    userinfo_status: int = 200,
    jwks: dict | None = None,
):
    """Faux fournisseur SSO complet : découverte, token, JWKS, userinfo."""
    if token_json is None:
        token_json = {"access_token": "fictional-access-token", "id_token": _id_token()}
    if userinfo_json is None:
        userinfo_json = {"sub": "fictional-subject-id", "email": "assignee@example.com"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=_DISCOVERY_DOC)
        if path == "/token":
            assert request.method == "POST"
            return httpx.Response(200, json=token_json)
        if path == "/jwks":
            return httpx.Response(200, json=jwks if jwks is not None else _jwks())
        if path == "/userinfo":
            assert request.headers["Authorization"] == f"Bearer {token_json['access_token']}"
            return httpx.Response(userinfo_status, json=userinfo_json)
        raise AssertionError(f"URL inattendue : {request.url}")

    return handler


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _provider() -> OidcAuthProvider:
    return OidcAuthProvider(
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret="fictional-client-secret",
        timeout=5,
    )


async def _fetch(monkeypatch, handler):
    _patch_async_client(monkeypatch, handler)
    return await _provider().fetch_user("fictional-code", "http://localhost:8000/api/auth/callback")


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
    async def test_success_with_verified_id_token(self, monkeypatch):
        user = await _fetch(monkeypatch, _sso_handler())

        assert user.email == "assignee@example.com"
        assert user.display_name == "[FICTIF] Assignée Test"
        assert user.subject == "fictional-subject-id"

    @pytest.mark.asyncio
    async def test_email_from_userinfo_when_absent_from_id_token(self, monkeypatch):
        """Cas Entra ID : l'email n'est pas toujours dans l'id_token."""
        token = {"access_token": "tok", "id_token": _id_token(email=None, name=None)}
        userinfo = {"sub": "fictional-subject-id", "email": "via-userinfo@example.com"}

        user = await _fetch(monkeypatch, _sso_handler(token_json=token, userinfo_json=userinfo))

        assert user.email == "via-userinfo@example.com"
        assert user.display_name == "via-userinfo@example.com"  # repli sur l'email sans nom

    @pytest.mark.asyncio
    async def test_token_exchange_failure_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=_DISCOVERY_DOC)
            return httpx.Response(400, json={"error": "invalid_grant"})

        with pytest.raises(AuthProviderError):
            await _fetch(monkeypatch, handler)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self, monkeypatch):
        with pytest.raises(AuthProviderError, match="access_token"):
            await _fetch(monkeypatch, _sso_handler(token_json={"id_token": _id_token()}))

    @pytest.mark.asyncio
    async def test_missing_id_token_raises(self, monkeypatch):
        with pytest.raises(AuthProviderError, match="sans id_token"):
            await _fetch(monkeypatch, _sso_handler(token_json={"access_token": "tok"}))

    @pytest.mark.asyncio
    async def test_userinfo_failure_raises(self, monkeypatch):
        handler = _sso_handler(userinfo_status=401, userinfo_json={"error": "invalid_token"})

        with pytest.raises(AuthProviderError):
            await _fetch(monkeypatch, handler)

    @pytest.mark.asyncio
    async def test_missing_email_raises(self, monkeypatch):
        token = {"access_token": "tok", "id_token": _id_token(email=None)}
        handler = _sso_handler(token_json=token, userinfo_json={"sub": "fictional-subject-id"})

        with pytest.raises(AuthProviderError, match="aucun email"):
            await _fetch(monkeypatch, handler)


class TestIdTokenVerification:
    """Chaque cas doit être refusé : sinon on pourrait se connecter sous
    l'identité de son choix avec un token forgé ou destiné à une autre app."""

    @staticmethod
    def _handler_with(id_token: str):
        return _sso_handler(token_json={"access_token": "tok", "id_token": id_token})

    @pytest.mark.asyncio
    async def test_signed_by_another_key_refused(self, monkeypatch):
        """Même kid, mais signé par une clé que le fournisseur ne publie pas."""
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(_id_token(private_key=_OTHER_KEY)))

    @pytest.mark.asyncio
    async def test_unknown_kid_refused(self, monkeypatch):
        with pytest.raises(AuthProviderError, match="clé inconnue"):
            await _fetch(monkeypatch, self._handler_with(_id_token(kid="unknown-kid")))

    @pytest.mark.asyncio
    async def test_wrong_audience_refused(self, monkeypatch):
        """Token émis pour une autre application du même fournisseur."""
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(_id_token(aud="another-app")))

    @pytest.mark.asyncio
    async def test_wrong_issuer_refused(self, monkeypatch):
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(_id_token(iss="https://evil.example.com")))

    @pytest.mark.asyncio
    async def test_expired_token_refused(self, monkeypatch):
        past = int(time.time()) - 3600
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(_id_token(iat=past - 300, exp=past)))

    @pytest.mark.asyncio
    async def test_unsigned_token_refused(self, monkeypatch):
        """alg=none : jamais accepté."""
        now = int(time.time())
        unsigned = jwt.encode(
            {"iss": _ISSUER, "aud": _CLIENT_ID, "sub": "x", "iat": now, "exp": now + 300},
            key=None,
            algorithm="none",
            headers={"kid": _KID},
        )
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(unsigned))

    @pytest.mark.asyncio
    async def test_missing_sub_refused(self, monkeypatch):
        with pytest.raises(AuthProviderError, match="id_token invalide"):
            await _fetch(monkeypatch, self._handler_with(_id_token(sub=None)))

    @pytest.mark.asyncio
    async def test_userinfo_for_another_subject_refused(self, monkeypatch):
        handler = _sso_handler(userinfo_json={"sub": "someone-else", "email": "other@example.com"})

        with pytest.raises(AuthProviderError, match="ne correspond pas"):
            await _fetch(monkeypatch, handler)

    @pytest.mark.asyncio
    async def test_jwks_unreachable_refused(self, monkeypatch):
        base = _sso_handler()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/jwks":
                return httpx.Response(503)
            return base(request)

        with pytest.raises(AuthProviderError, match="injoignables"):
            await _fetch(monkeypatch, handler)
