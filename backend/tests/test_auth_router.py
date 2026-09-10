"""
Tests d'intégration — Module 8 : routes HTTP d'authentification
(app/routers/auth.py)

Le fournisseur SSO réel (OidcAuthProvider) est surchargé par un double de
test déterministe (_FakeAuthProvider) — jamais d'appel réseau réel dans un
test, même mécanisme que get_requirement_analyzer/get_test_design_agent
dans test_sessions_router.py.

Toutes les données sont fictives.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

import pytest
import pytest_asyncio

from app.agents.auth_layer import AuthenticatedUser, AuthProviderError, BaseAuthProvider
from app.database import get_db, get_session_factory
from app.dependencies import get_auth_provider_dependency
from app.main import app
from app.services import auth_service


class _FakeAuthProvider(BaseAuthProvider):
    def __init__(self, user: AuthenticatedUser | None = None, fail: bool = False):
        self._user = user or AuthenticatedUser(
            email="assignee@example.com", display_name="[FICTIF] Assignée", subject="sub-1"
        )
        self._fail = fail

    @property
    def provider_name(self) -> str:
        return "fake"

    async def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        if self._fail:
            raise AuthProviderError("SSO indisponible (fictif)")
        return f"https://sso.example.com/authorize?state={state}&redirect_uri={redirect_uri}"

    async def fetch_user(self, code: str, redirect_uri: str) -> AuthenticatedUser:
        if self._fail:
            raise AuthProviderError("Échec du login SSO (fictif)")
        return self._user


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    async def _override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = lambda: db_sessionmaker
    app.dependency_overrides[get_auth_provider_dependency] = lambda: _FakeAuthProvider()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


class TestAuthConfig:
    @pytest.mark.asyncio
    async def test_reports_sso_not_configured_by_default(self, client):
        """conftest.py force AUTH_OIDC_ISSUER à None pour toute la suite
        (_disable_real_sso_in_tests) — comportement attendu ici."""
        response = await client.get("/api/auth/config")

        assert response.status_code == 200
        assert response.json() == {"sso_configured": False}

    @pytest.mark.asyncio
    async def test_reports_sso_configured_when_issuer_set(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "AUTH_OIDC_ISSUER", "https://login.microsoftonline.com/tenant/v2.0")

        response = await client.get("/api/auth/config")

        assert response.json() == {"sso_configured": True}


class TestLogin:
    @pytest.mark.asyncio
    async def test_redirects_to_provider_and_sets_state_cookie(self, client):
        response = await client.get("/api/auth/login", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"].startswith("https://sso.example.com/authorize?")
        assert "oauth_state" in response.cookies

    @pytest.mark.asyncio
    async def test_provider_error_returns_503(self, client):
        app.dependency_overrides[get_auth_provider_dependency] = lambda: _FakeAuthProvider(fail=True)

        response = await client.get("/api/auth/login", follow_redirects=False)

        assert response.status_code == 503


class TestCallback:
    @pytest.mark.asyncio
    async def test_happy_path_sets_session_cookie_and_redirects_to_frontend(self, client):
        login_resp = await client.get("/api/auth/login", follow_redirects=False)
        state = login_resp.cookies["oauth_state"]

        response = await client.get(
            "/api/auth/callback", params={"code": "fictional-code", "state": state}, follow_redirects=False
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith("http://localhost:5173")
        assert "session_token" in response.cookies

    @pytest.mark.asyncio
    async def test_missing_state_cookie_redirects_with_error_not_500(self, client):
        response = await client.get(
            "/api/auth/callback",
            params={"code": "fictional-code", "state": "some-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "auth_error" in response.headers["location"]
        assert "session_token" not in response.cookies

    @pytest.mark.asyncio
    async def test_mismatched_state_redirects_with_error(self, client):
        login_resp = await client.get("/api/auth/login", follow_redirects=False)
        assert login_resp.cookies["oauth_state"]

        client.cookies.set("oauth_state", "attacker-controlled-state")
        response = await client.get(
            "/api/auth/callback",
            params={"code": "fictional-code", "state": "different-state"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "auth_error" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_provider_failure_redirects_with_error_not_502(self, client):
        # Login réussit (état posé), puis le provider bascule en échec pour
        # simuler fetch_user() qui explose au moment de l'échange du code.
        login_resp = await client.get("/api/auth/login", follow_redirects=False)
        state = login_resp.cookies["oauth_state"]
        app.dependency_overrides[get_auth_provider_dependency] = lambda: _FakeAuthProvider(fail=True)

        response = await client.get(
            "/api/auth/callback", params={"code": "fictional-code", "state": state}, follow_redirects=False
        )

        assert response.status_code == 302
        assert "auth_error" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_second_login_reuses_same_account(self, client):
        login_resp = await client.get("/api/auth/login", follow_redirects=False)
        state = login_resp.cookies["oauth_state"]
        first = await client.get(
            "/api/auth/callback", params={"code": "code-1", "state": state}, follow_redirects=False
        )
        client.cookies.set("session_token", first.cookies["session_token"])
        me_first = await client.get("/api/auth/me")

        login_resp2 = await client.get("/api/auth/login", follow_redirects=False)
        state2 = login_resp2.cookies["oauth_state"]
        second = await client.get(
            "/api/auth/callback", params={"code": "code-2", "state": state2}, follow_redirects=False
        )
        client.cookies.set("session_token", second.cookies["session_token"])
        me_second = await client.get("/api/auth/me")

        assert me_first.json()["id"] == me_second.json()["id"]


class TestDevLogin:
    @pytest.mark.asyncio
    async def test_succeeds_when_sso_not_configured(self, client):
        response = await client.post(
            "/api/auth/dev-login", json={"email": "assignee@example.com", "display_name": "[FICTIF] Assignée"}
        )

        assert response.status_code == 200
        assert response.json()["email"] == "assignee@example.com"
        assert "session_token" in response.cookies

    @pytest.mark.asyncio
    async def test_defaults_display_name_to_email(self, client):
        response = await client.post("/api/auth/dev-login", json={"email": "assignee@example.com"})

        assert response.json()["display_name"] == "assignee@example.com"

    @pytest.mark.asyncio
    async def test_returns_404_once_real_sso_is_configured(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "AUTH_OIDC_ISSUER", "https://accounts.google.com")

        response = await client.post("/api/auth/dev-login", json={"email": "assignee@example.com"})

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_session_cookie_from_dev_login_works_on_protected_routes(self, client):
        login_resp = await client.post("/api/auth/dev-login", json={"email": "assignee@example.com"})
        client.cookies.set("session_token", login_resp.cookies["session_token"])

        me_resp = await client.get("/api/auth/me")

        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "assignee@example.com"


class TestMe:
    @pytest.mark.asyncio
    async def test_without_cookie_returns_401(self, client):
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_with_valid_cookie_returns_user(self, client, db_sessionmaker):
        async with db_sessionmaker() as db:
            user = await auth_service.upsert_user(
                db,
                AuthenticatedUser(email="assignee@example.com", display_name="[FICTIF] Assignée", subject="sub-1"),
            )
        token = auth_service.create_session_token(user.id)
        client.cookies.set("session_token", token)

        response = await client.get("/api/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == "assignee@example.com"

    @pytest.mark.asyncio
    async def test_with_garbage_cookie_returns_401(self, client):
        client.cookies.set("session_token", "not-a-valid-jwt")

        response = await client.get("/api/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_with_cookie_for_deleted_account_returns_401(self, client):
        token = auth_service.create_session_token("does-not-exist-in-db")
        client.cookies.set("session_token", token)

        response = await client.get("/api/auth/me")

        assert response.status_code == 401


class TestLogout:
    @pytest.mark.asyncio
    async def test_clears_session_cookie(self, client):
        response = await client.post("/api/auth/logout")

        assert response.status_code == 204
        set_cookie = response.headers.get("set-cookie", "")
        assert "session_token=" in set_cookie
