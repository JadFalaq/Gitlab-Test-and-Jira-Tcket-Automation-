"""
Tests unitaires — Module 5 : providers de tickets (Jira, GitLab, mock)

MockTicketProvider est testé en profondeur (pas d'appel réseau, cf.
app/integrations/mock_provider.py). JiraProvider/GitLabProvider sont testés
via httpx.MockTransport (aucun appel réseau réel) pour vérifier le câblage
HTTP (payload envoyé, parsing de la réponse, gestion d'erreurs) sans
dépendre d'un vrai compte Jira/GitLab.

Toutes les données sont fictives.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.base import TicketNotFoundError, TicketProviderError
from app.integrations.gitlab_provider import GitLabProvider
from app.integrations.jira_provider import JiraProvider
from app.integrations.mock_provider import (
    MockTicketProvider,
    _backdate_for_tests,
    _reset_mock_store_for_tests,
)
from app.schemas.ticket import TicketDraft

_RealAsyncClient = httpx.AsyncClient


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """
    Remplace httpx.AsyncClient par une version qui route toutes les requêtes
    vers `handler` (httpx.MockTransport) — aucun appel réseau réel, garde le
    reste du comportement d'AsyncClient intact.
    """

    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.fixture(autouse=True)
def _reset_mock_store():
    _reset_mock_store_for_tests()
    yield
    _reset_mock_store_for_tests()


class TestMockTicketProvider:
    @pytest.mark.asyncio
    async def test_create_ticket_returns_to_do_status(self):
        provider = MockTicketProvider(provider_label="jira")
        draft = TicketDraft(title="[FICTIF] Paiement QR", description="desc", issue_type="story")

        ticket = await provider.create_ticket(draft, session_id="s1")

        assert ticket.provider == "jira"
        assert ticket.status == "To Do"
        assert ticket.title == draft.title
        assert ticket.session_id == "s1"

    @pytest.mark.asyncio
    async def test_status_progresses_with_elapsed_time(self):
        provider = MockTicketProvider(provider_label="gitlab")
        draft = TicketDraft(title="[FICTIF] Story", description="", issue_type="task")
        ticket = await provider.create_ticket(draft, session_id="s1")

        assert await provider.get_ticket_status(ticket.external_id) == "To Do"

        _backdate_for_tests(ticket.external_id, seconds_ago=25)
        assert await provider.get_ticket_status(ticket.external_id) == "In Progress"

        _backdate_for_tests(ticket.external_id, seconds_ago=90)
        assert await provider.get_ticket_status(ticket.external_id) == "Done"

    @pytest.mark.asyncio
    async def test_get_status_unknown_ticket_raises(self):
        provider = MockTicketProvider()

        with pytest.raises(TicketNotFoundError):
            await provider.get_ticket_status("does-not-exist")

    @pytest.mark.asyncio
    async def test_health_check_always_true(self):
        assert await MockTicketProvider().health_check() is True


class TestJiraProvider:
    @pytest.mark.asyncio
    async def test_create_ticket_missing_config_raises(self):
        provider = JiraProvider(base_url=None, api_token=None, user_email=None)

        with pytest.raises(TicketProviderError):
            await provider.create_ticket(TicketDraft(title="x", description=""), session_id="s1")

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/rest/api/3/issue":
                assert b"[FICTIF] Story" in request.read()
                return httpx.Response(201, json={"id": "10001", "key": "PROTO-1"})
            if request.url.path == "/rest/agile/1.0/board":
                # Pas de board Scrum pour ce projet fictif -> pas de sprint à rattacher.
                return httpx.Response(200, json={"values": []})
            raise AssertionError(f"appel HTTP inattendu : {request.method} {request.url.path}")

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net",
            api_token="tok",
            user_email="dev@fictif.test",
            project_key="PROTO",
        )
        _patch_async_client(monkeypatch, handler)

        ticket = await provider.create_ticket(
            TicketDraft(title="[FICTIF] Story", description="desc\n\nligne 2", issue_type="story"),
            session_id="s1",
        )

        assert ticket.key == "PROTO-1"
        assert ticket.url == "https://fictif.atlassian.net/browse/PROTO-1"
        assert ticket.provider == "jira"

    @pytest.mark.asyncio
    async def test_create_ticket_attaches_to_active_sprint(self, monkeypatch):
        """
        Un ticket créé via l'API Jira atterrit dans le backlog par défaut —
        invisible sur un tableau Scrum, qui n'affiche que le sprint actif.
        create_ticket doit donc tenter de le rattacher automatiquement.
        """
        move_calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/rest/api/3/issue":
                return httpx.Response(201, json={"id": "10001", "key": "PROTO-1"})
            if request.url.path == "/rest/agile/1.0/board":
                return httpx.Response(200, json={"values": [{"id": 1, "type": "simple"}]})
            if request.url.path == "/rest/agile/1.0/board/1/sprint":
                assert request.url.params["state"] == "active"
                return httpx.Response(
                    200, json={"values": [{"id": 42, "name": "Sprint 0", "state": "active"}]}
                )
            if request.url.path == "/rest/agile/1.0/sprint/42/issue":
                move_calls.append(json.loads(request.read()))
                return httpx.Response(204)
            raise AssertionError(f"appel HTTP inattendu : {request.method} {request.url.path}")

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        ticket = await provider.create_ticket(
            TicketDraft(title="[FICTIF] Story", description="desc", issue_type="story"), session_id="s1"
        )

        assert ticket.key == "PROTO-1"  # le ticket est créé même si le rattachement échouait
        assert move_calls == [{"issues": ["PROTO-1"]}]

    @pytest.mark.asyncio
    async def test_create_ticket_succeeds_when_no_active_sprint(self, monkeypatch):
        """Board Scrum trouvé mais aucun sprint actif (ex: tous clôturés/futurs) :
        no-op silencieux, le ticket reste créé avec succès."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/rest/api/3/issue":
                return httpx.Response(201, json={"id": "10001", "key": "PROTO-1"})
            if request.url.path == "/rest/agile/1.0/board":
                return httpx.Response(200, json={"values": [{"id": 1, "type": "simple"}]})
            if request.url.path == "/rest/agile/1.0/board/1/sprint":
                return httpx.Response(200, json={"values": []})
            raise AssertionError(f"appel HTTP inattendu : {request.method} {request.url.path}")

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        ticket = await provider.create_ticket(
            TicketDraft(title="[FICTIF] Story", description="desc", issue_type="story"), session_id="s1"
        )

        assert ticket.key == "PROTO-1"

    @pytest.mark.asyncio
    async def test_create_ticket_succeeds_when_sprint_lookup_fails(self, monkeypatch):
        """Board Kanban (pas de concept de sprint, l'API agile renvoie une
        erreur sur /sprint) ou erreur réseau ponctuelle : dégradation
        silencieuse, le ticket reste créé avec succès (règle best-effort du
        module 5)."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/rest/api/3/issue":
                return httpx.Response(201, json={"id": "10001", "key": "PROTO-1"})
            if request.url.path == "/rest/agile/1.0/board":
                raise httpx.ConnectError("réseau indisponible", request=request)
            raise AssertionError(f"appel HTTP inattendu : {request.method} {request.url.path}")

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        ticket = await provider.create_ticket(
            TicketDraft(title="[FICTIF] Story", description="desc", issue_type="story"), session_id="s1"
        )

        assert ticket.key == "PROTO-1"

    @pytest.mark.asyncio
    async def test_get_status_404_raises_not_found(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"errorMessages": ["not found"]})

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(TicketNotFoundError):
            await provider.get_ticket_status("99999")

    @pytest.mark.asyncio
    async def test_get_status_success(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/rest/api/3/issue/10001"
            assert request.url.params["fields"] == "status"
            return httpx.Response(200, json={"fields": {"status": {"name": "In Progress"}}})

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        assert await provider.get_ticket_status("10001") == "In Progress"

    @pytest.mark.asyncio
    async def test_get_status_http_error_raises_provider_error(self, monkeypatch):
        """Un 401/500 Jira doit sortir en TicketProviderError typée (contrat
        BaseTicketProvider), jamais en httpx.HTTPStatusError brute — sinon
        refresh_ticket_statuses (module 6) casse sur une erreur ponctuelle."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom interne")

        provider = JiraProvider(
            base_url="https://fictif.atlassian.net", api_token="tok", user_email="dev@fictif.test"
        )
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(TicketProviderError, match="HTTP 500"):
            await provider.get_ticket_status("10001")


class TestGitLabProvider:
    @pytest.mark.asyncio
    async def test_create_ticket_missing_config_raises(self):
        provider = GitLabProvider(base_url=None, private_token=None, project_id=None)

        with pytest.raises(TicketProviderError):
            await provider.create_ticket(TicketDraft(title="x", description=""), session_id="s1")

    @pytest.mark.asyncio
    async def test_create_ticket_success(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v4/projects/123/issues"
            assert request.headers["PRIVATE-TOKEN"] == "tok"
            return httpx.Response(
                201,
                json={"iid": 17, "web_url": "https://gitlab.fictif.test/proj/-/issues/17", "state": "opened"},
            )

        provider = GitLabProvider(
            base_url="https://gitlab.fictif.test", private_token="tok", project_id="123"
        )
        _patch_async_client(monkeypatch, handler)

        ticket = await provider.create_ticket(
            TicketDraft(title="[FICTIF] Bug", description="desc", issue_type="bug"), session_id="s1"
        )

        assert ticket.key == "#17"
        assert ticket.status == "opened"
        assert ticket.provider == "gitlab"

    @pytest.mark.asyncio
    async def test_get_status_success(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v4/projects/123/issues/17"
            return httpx.Response(200, json={"state": "closed"})

        provider = GitLabProvider(
            base_url="https://gitlab.fictif.test", private_token="tok", project_id="123"
        )
        _patch_async_client(monkeypatch, handler)

        assert await provider.get_ticket_status("17") == "closed"

    @pytest.mark.asyncio
    async def test_get_status_404_raises_not_found(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "404 Not found"})

        provider = GitLabProvider(
            base_url="https://gitlab.fictif.test", private_token="tok", project_id="123"
        )
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(TicketNotFoundError):
            await provider.get_ticket_status("99999")

    @pytest.mark.asyncio
    async def test_get_status_http_error_raises_provider_error(self, monkeypatch):
        """Même contrat que Jira : un 401 GitLab doit sortir en
        TicketProviderError typée, jamais en httpx.HTTPStatusError brute."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "401 Unauthorized"})

        provider = GitLabProvider(
            base_url="https://gitlab.fictif.test", private_token="tok", project_id="123"
        )
        _patch_async_client(monkeypatch, handler)

        with pytest.raises(TicketProviderError, match="HTTP 401"):
            await provider.get_ticket_status("17")
