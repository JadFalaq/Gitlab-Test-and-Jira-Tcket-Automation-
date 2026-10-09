"""
Tests d'intégration — Module 6 : routes HTTP de suivi (app/routers/tracking.py)

Vérifie /api/tracking/tickets (snapshot DB) et /api/tracking/sync (rafraîchit
via Jira — FakeJiraProvider ici, pas d'appel réseau). Le flux SSE
(/api/tracking/stream) n'est pas testé ici en boucle infinie ; sa logique de
snapshot est déjà couverte par tracking_service (mêmes fonctions).

Toutes les données sont fictives.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

import pytest
import pytest_asyncio

from tests.fakes.llm import FakeLLMProvider
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.database import get_db, get_session_factory
from app.dependencies import get_current_user, get_requirement_analyzer, get_test_design_agent
from app.main import app
from app.models.session import UserModel
from tests.test_session_service import (
    FICTIONAL_USER_INPUT,
    VALID_REQUIREMENT_JSON,
    VALID_TEST_DESIGN_JSON,
)

_FAKE_USER = UserModel(id="fictional-user-id", email="test@example.com", display_name="Test User")


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    async def _override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # Sans cet override, les tâches de fond (POST /api/sessions et
    # /requirement/accept, cf. module 4) écriraient dans le fichier DB de
    # production au lieu de la DB de test en mémoire — SessionNotFoundError
    # silencieuse côté tâche de fond, 500 en cascade.
    app.dependency_overrides[get_session_factory] = lambda: db_sessionmaker
    app.dependency_overrides[get_requirement_analyzer] = lambda: RequirementAnalyzerAgent(
        llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_REQUIREMENT_JSON})
    )
    app.dependency_overrides[get_test_design_agent] = lambda: TestDesignAgent(
        llm_provider=FakeLLMProvider(custom_responses={"paiement": VALID_TEST_DESIGN_JSON})
    )
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def _create_session_with_tickets(client: AsyncClient) -> str:
    create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
    session_id = create_resp.json()["id"]
    finalize_resp = await client.post(f"/api/sessions/{session_id}/chat/finalize")
    assert finalize_resp.json()["status"] == "analyzing"
    await client.post(f"/api/sessions/{session_id}/requirement/accept")
    await client.post(f"/api/sessions/{session_id}/tests/accept")
    tickets_resp = await client.post(
        f"/api/sessions/{session_id}/tickets/create"
    )
    assert tickets_resp.json()["status"] == "tickets_created"
    return session_id


class TestTrackingRouter:
    @pytest.mark.asyncio
    async def test_list_tracked_tickets_empty_when_no_tickets(self, client):
        response = await client.get("/api/tracking/tickets")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_tracked_tickets_returns_created_ticket(self, client):
        session_id = await _create_session_with_tickets(client)

        response = await client.get("/api/tracking/tickets")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["session_id"] == session_id
        assert body[0]["provider"] == "jira"

    @pytest.mark.asyncio
    async def test_sync_tickets_returns_refreshed_list(self, client):
        await _create_session_with_tickets(client)

        response = await client.post("/api/tracking/sync")

        assert response.status_code == 200
        assert len(response.json()) == 1


class TestTicketsRouterErrors:
    @pytest.mark.asyncio
    async def test_create_tickets_before_validated_returns_409(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        response = await client.post(f"/api/sessions/{session_id}/tickets/create")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_refresh_tickets_unknown_session_returns_404(self, client):
        response = await client.post("/api/sessions/does-not-exist/tickets/refresh")

        assert response.status_code == 404
