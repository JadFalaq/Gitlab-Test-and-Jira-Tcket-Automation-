"""
Tests unitaires — Module 5 : transitions de session liées aux tickets
(session_service.create_tickets / refresh_tickets)

Complète tests/test_session_service.py (module 4). Jira est remplacé par
FakeJiraProvider (fixture `fake_jira`, tests/conftest.py), pas d'appel réseau.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.schemas.session import SessionCreateRequest
from app.services import session_service
from app.services.session_service import InvalidSessionTransitionError
from tests.test_session_service import (
    FICTIONAL_USER_ID,
    FICTIONAL_USER_INPUT,
    _working_analyzer,
    _working_designer,
)


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

        result = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "tickets_created"
        assert len(result.tickets) == 1  # 1 user story mockée dans VALID_TEST_DESIGN_JSON
        assert result.tickets[0]["provider"] == "jira"

    @pytest.mark.asyncio
    async def test_create_tickets_jira_not_configured_fails_explicitly(self, db_session, fake_jira):
        session = await _create_validated_session(db_session)
        fake_jira.configured = False

        result = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)

        assert result.status == "ticket_creation_failed"
        assert result.tickets == []
        assert "pas configuré" in result.error_message

    @pytest.mark.asyncio
    async def test_create_tickets_wrong_state_raises(self, db_session):
        session = await session_service.create_session(
            db_session, SessionCreateRequest(user_input=FICTIONAL_USER_INPUT),
            user_id=FICTIONAL_USER_ID,
        )  # reste en 'analyzing', jamais 'validated'

        with pytest.raises(InvalidSessionTransitionError):
            await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)

    @pytest.mark.asyncio
    async def test_create_tickets_retry_from_ticket_creation_failed(self, db_session, fake_jira):
        session = await _create_validated_session(db_session)

        # Jira refuse la création => échec total => 'ticket_creation_failed'
        fake_jira.fail_creation = True
        failed = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)
        assert failed.status == "ticket_creation_failed"
        assert failed.error_message is not None

        # Retry une fois Jira rétabli, autorisé depuis 'ticket_creation_failed'
        fake_jira.fail_creation = False
        retried = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)
        assert retried.status == "tickets_created"
        assert len(retried.tickets) == 1


class TestRefreshTickets:
    @pytest.mark.asyncio
    async def test_refresh_tickets_updates_last_synced_at(self, db_session):
        session = await _create_validated_session(db_session)
        created = await session_service.create_tickets(db_session, session.id, FICTIONAL_USER_ID)
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
