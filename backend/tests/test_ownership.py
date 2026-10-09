"""
Tests d'intégration — contrôle de propriété (module 8) sur TOUS les endpoints
qui exposent le contenu d'une session.

Deux utilisatrices réelles (connexion par cookie via /api/auth/dev-login,
pas de surcharge de get_current_user) : Alice mène une session jusqu'aux
tickets Jira, avec une pièce jointe. Bob ne doit RIEN voir ni pouvoir
actionner : 404 (jamais 403, pour ne pas révéler l'existence de la session)
ou liste vide.

Les analytics (/api/analytics/summary) restent volontairement globales :
elles ne renvoient que des compteurs agrégés, aucun contenu de session.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agents.chat_agent import ChatAgent
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.config import settings
from app.database import get_db, get_session_factory
from app.dependencies import get_chat_agent, get_requirement_analyzer, get_test_design_agent
from app.main import app
from app.routers.tracking import tracking_snapshot
from tests.fakes.llm import FakeLLMProvider
from tests.test_session_service import FICTIONAL_USER_INPUT, VALID_REQUIREMENT_JSON, VALID_TEST_DESIGN_JSON


@pytest_asyncio.fixture
async def clients(db_sessionmaker, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    async def _override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = lambda: db_sessionmaker
    app.dependency_overrides[get_requirement_analyzer] = lambda: RequirementAnalyzerAgent(
        llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_REQUIREMENT_JSON})
    )
    app.dependency_overrides[get_test_design_agent] = lambda: TestDesignAgent(
        llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_TEST_DESIGN_JSON})
    )
    app.dependency_overrides[get_chat_agent] = lambda: ChatAgent(llm_provider=FakeLLMProvider())

    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as alice,
        AsyncClient(transport=transport, base_url="http://test") as bob,
    ):
        for client, email in ((alice, "alice@example.com"), (bob, "bob@example.com")):
            response = await client.post("/api/auth/dev-login", json={"email": email})
            assert response.status_code == 200
            client.cookies.set("session_token", response.cookies["session_token"])
        yield alice, bob

    app.dependency_overrides.clear()


async def _alice_session_with_tickets(alice: AsyncClient) -> tuple[str, str]:
    """Session d'Alice jusqu'à 'tickets_created', avec une pièce jointe.
    Retourne (session_id, attachment_id)."""
    created = await alice.post(
        "/api/sessions",
        data={"user_input": FICTIONAL_USER_INPUT},
        files=[("files", ("specs.txt", b"[FICTIF] Specs du paiement", "text/plain"))],
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    attachment_id = created.json()["attachments"][0]["id"]

    for step in ("chat/finalize", "requirement/accept", "tests/accept", "tickets/create"):
        response = await alice.post(f"/api/sessions/{session_id}/{step}")
        assert response.status_code == 200, (step, response.text)
    assert (await alice.get(f"/api/sessions/{session_id}")).json()["status"] == "tickets_created"
    return session_id, attachment_id


@pytest.mark.asyncio
async def test_owner_sees_everything(clients, db_sessionmaker):
    alice, _ = clients
    session_id, attachment_id = await _alice_session_with_tickets(alice)

    assert (await alice.get(f"/api/sessions/{session_id}")).status_code == 200
    assert (await alice.get(f"/api/sessions/{session_id}/attachments/{attachment_id}/file")).status_code == 200
    assert [s["id"] for s in (await alice.get("/api/sessions")).json()] == [session_id]
    assert len((await alice.get("/api/tracking/tickets")).json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/sessions/{sid}", None),
        ("GET", "/api/sessions/{sid}/attachments/{aid}/file", None),
        ("POST", "/api/sessions/{sid}/chat", {"message": "[FICTIF] intrusion"}),
        ("POST", "/api/sessions/{sid}/chat/finalize", None),
        ("POST", "/api/sessions/{sid}/requirement/accept", None),
        ("POST", "/api/sessions/{sid}/requirement/reject", None),
        ("POST", "/api/sessions/{sid}/requirement/regenerate", None),
        ("POST", "/api/sessions/{sid}/tests/accept", None),
        ("POST", "/api/sessions/{sid}/tests/reject", None),
        ("POST", "/api/sessions/{sid}/tests/regenerate", None),
        ("POST", "/api/sessions/{sid}/tickets/create", None),
        ("POST", "/api/sessions/{sid}/tickets/refresh", None),
    ],
)
async def test_other_user_gets_404_on_every_session_endpoint(clients, method, path, body):
    alice, bob = clients
    session_id, attachment_id = await _alice_session_with_tickets(alice)

    response = await bob.request(method, path.format(sid=session_id, aid=attachment_id), json=body)

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_other_user_lists_and_tracking_are_empty(clients, db_sessionmaker):
    alice, bob = clients
    await _alice_session_with_tickets(alice)

    assert (await bob.get("/api/sessions")).json() == []
    assert (await bob.get("/api/tracking/tickets")).json() == []
    assert (await bob.post("/api/tracking/sync")).json() == []


@pytest.mark.asyncio
async def test_sse_snapshot_only_contains_own_tickets(clients, db_sessionmaker):
    """Le flux SSE est infini : on teste directement la fonction d'un tick."""
    alice, bob = clients
    session_id, _ = await _alice_session_with_tickets(alice)
    alice_id = (await alice.get("/api/auth/me")).json()["id"]
    bob_id = (await bob.get("/api/auth/me")).json()["id"]

    alice_snapshot = await tracking_snapshot(db_sessionmaker, alice_id)
    bob_snapshot = await tracking_snapshot(db_sessionmaker, bob_id)

    assert [t.session_id for t in alice_snapshot] == [session_id]
    assert bob_snapshot == []


@pytest.mark.asyncio
async def test_alice_session_untouched_after_bob_attempts(clients):
    """Les tentatives de Bob n'ont eu aucun effet sur la session d'Alice."""
    alice, bob = clients
    session_id, _ = await _alice_session_with_tickets(alice)
    before = (await alice.get(f"/api/sessions/{session_id}")).json()

    for step in ("requirement/reject", "tests/regenerate", "tickets/create"):
        await bob.post(f"/api/sessions/{session_id}/{step}")

    after = (await alice.get(f"/api/sessions/{session_id}")).json()
    assert after["status"] == before["status"] == "tickets_created"
    assert after["tickets"] == before["tickets"]
