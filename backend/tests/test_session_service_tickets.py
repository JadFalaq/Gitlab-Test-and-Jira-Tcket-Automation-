"""
Tests unitaires — Module 5 : transitions de session liées aux tickets
(session_service.create_tickets / refresh_tickets)

Complète tests/test_session_service.py (module 4) sans le modifier, pour ne
pas toucher un fichier de test déjà "terminé et testé". Aucune config
Jira/GitLab dans l'environnement de test => MockTicketProvider automatique
(cf. app/integrations/factory.py), pas d'appel réseau.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.integrations.mock_provider import _reset_mock_store_for_tests
from app.schemas.session import SessionCreateRequest
from app.services import session_service
from app.services.session_service import InvalidSessionTransitionError
from tests.test_session_service import (
    FICTIONAL_USER_ID,
    FICTIONAL_USER_INPUT,
    _working_analyzer,
    _working_designer,
)


@pytest.fixture(autouse=True)
def _reset_mock_store():
    _reset_mock_store_for_tests()
    yield
    _reset_mock_store_for_tests()


async def _create_validated_session(db_session):
    session = await session_service.create_session(
        db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
        user_id=FICTIONAL_USER_ID,
    )
    await session_service.finalize_chat(db_session, session.id, FICTIONAL_USER_ID)
    session = await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())
    await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)
    await session_service.run_test_design(db_session, session.id, _working_designer())
    return await session_service.accept_tests(db_session, session.id, FICTIONAL_USER_ID)


class TestCreateTickets:
    @pytest.mark.asyncio
    async def test_create_tickets_from_validated_success(self, db_session):
        session = await _create_validated_session(db_session)

        result = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID, providers=["jira"])

        assert result.status == "tickets_created"
        assert len(result.tickets) == 1  # 1 user story mockée dans VALID_TEST_DESIGN_JSON
        assert result.tickets[0]["provider"] == "jira"

    @pytest.mark.asyncio
    async def test_create_tickets_default_providers_both(self, db_session):
        session = await _create_validated_session(db_session)

        result = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "tickets_created"
        assert {t["provider"] for t in result.tickets} == {"jira", "gitlab"}

    @pytest.mark.asyncio
    async def test_create_tickets_wrong_state_raises(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )  # reste en 'analyzing', jamais 'validated'

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_create_tickets_retry_from_ticket_creation_failed(self, db_session):
        session = await _create_validated_session(db_session)

        # Provider inconnu => échec total => 'ticket_creation_failed'
        failed = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID, providers=["unknown"])
        assert failed.status == "ticket_creation_failed"
        assert failed.error_message is not None

        # Retry avec un provider valide, autorisé depuis 'ticket_creation_failed'
        retried = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID, providers=["jira"])
        assert retried.status == "tickets_created"
        assert len(retried.tickets) == 1


class TestRefreshTickets:
    @pytest.mark.asyncio
    async def test_refresh_tickets_updates_last_synced_at(self, db_session):
        session = await _create_validated_session(db_session)
        created = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID, providers=["jira"])
        original_synced_at = created.tickets[0]["last_synced_at"]

        refreshed = await session_service.refresh_tickets(db_session, session.id, FICTIONAL_USER_ID)

        assert refreshed.tickets[0]["last_synced_at"] >= original_synced_at

    @pytest.mark.asyncio
    async def test_refresh_tickets_noop_when_no_tickets(self, db_session):
        session = await _create_validated_session(db_session)

        refreshed = await session_service.refresh_tickets(db_session, session.id, FICTIONAL_USER_ID)

        assert refreshed.tickets == []


class TestRegenerationCounters:
    @pytest.mark.asyncio
    async def test_regenerate_requirement_increments_counter(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        session = await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())
        assert session.requirement_regenerations == 0

        result = await session_service.regenerate_requirement(db_session, session.id, FICTIONAL_USER_ID)

        assert result.requirement_regenerations == 1

    @pytest.mark.asyncio
    async def test_regenerate_tests_increments_counter(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )
        session = await session_service.run_requirement_analysis(db_session, session.id, _working_analyzer())
        await session_service.accept_requirement(db_session, session.id, FICTIONAL_USER_ID)
        session = await session_service.run_test_design(db_session, session.id, _working_designer())
        assert session.test_regenerations == 0

        result = await session_service.regenerate_tests(db_session, session.id, FICTIONAL_USER_ID)

        assert result.test_regenerations == 1
