"""
Tests unitaires — Module 5 : orchestration de création de tickets
(app/services/ticket_service.py)

Le provider Jira est remplacé par FakeJiraProvider (fixture `fake_jira`,
tests/conftest.py) — pas d'appel réseau, comportement déterministe.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.schemas.test_design import (
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
    TestDesignOutput,
    TestSummary,
    UserStory,
)
from app.services import ticket_service


def _sample_test_design(n_stories: int = 2) -> TestDesignOutput:
    return TestDesignOutput(
        user_stories=[
            UserStory(
                title=f"[FICTIF] Story {i}",
                as_a="utilisateur",
                i_want="faire x",
                so_that="bénéficier de y",
                acceptance_criteria=["critère 1"],
                priority="high",
            )
            for i in range(n_stories)
        ],
        gherkin_features=[
            GherkinFeature(
                feature_name="[FICTIF] Feature",
                description="",
                scenarios=[
                    GherkinScenario(
                        name="Scénario 1",
                        steps=[
                            GherkinStep(keyword="Given", text="contexte"),
                            GherkinStep(keyword="Then", text="résultat"),
                        ],
                    )
                ],
            )
        ],
        test_summary=TestSummary(total_scenarios=1, coverage_notes="", suggested_test_types=[]),
    )


class TestCreateTickets:
    @pytest.mark.asyncio
    async def test_creates_one_jira_ticket_per_story(self):
        result = await ticket_service.create_tickets(session_id="s1", test_design=_sample_test_design(n_stories=2))

        assert len(result.tickets) == 2
        assert not result.errors
        assert {t.provider for t in result.tickets} == {"jira"}

    @pytest.mark.asyncio
    async def test_jira_not_configured_creates_nothing(self, fake_jira):
        """Jamais de faux ticket : sans Jira, une erreur explicite et rien de créé."""
        fake_jira.configured = False

        result = await ticket_service.create_tickets(session_id="s1", test_design=_sample_test_design(n_stories=1))

        assert result.tickets == []
        assert len(result.errors) == 1
        assert "pas configuré" in result.errors[0]

    @pytest.mark.asyncio
    async def test_creation_failure_recorded_as_error(self, fake_jira):
        fake_jira.fail_creation = True

        result = await ticket_service.create_tickets(session_id="s1", test_design=_sample_test_design(n_stories=2))

        assert result.tickets == []
        assert len(result.errors) == 2  # une erreur par story

    @pytest.mark.asyncio
    async def test_ticket_description_includes_story_criteria_and_gherkin(self, fake_jira):
        result = await ticket_service.create_tickets(session_id="s1", test_design=_sample_test_design(n_stories=1))

        assert result.tickets[0].title == "[FICTIF] Story 0"
        draft = fake_jira.created[0]
        assert "En tant que utilisateur, je veux faire x" in draft.description
        assert "- critère 1" in draft.description
        assert "Feature: [FICTIF] Feature" in draft.description
        assert draft.labels == ["priority::high"]


class TestRefreshTicketStatuses:
    @pytest.mark.asyncio
    async def test_refresh_updates_status_field(self):
        creation = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1)
        )
        raw_tickets = [t.model_dump(mode="json") for t in creation.tickets]

        refreshed = await ticket_service.refresh_ticket_statuses(raw_tickets)

        assert len(refreshed) == 1
        assert refreshed[0]["status"] == "To Do"  # juste créé, encore sous le seuil "In Progress"

    @pytest.mark.asyncio
    async def test_refresh_keeps_last_status_on_provider_error(self, fake_jira):
        """Best-effort (docstring de refresh_ticket_statuses) : un ticket dont
        le provider échoue conserve son dernier statut connu, sans exception
        propagée. Ici le faux Jira oublie le ticket après création : get_ticket_status
        lève TicketNotFoundError (sous-classe de TicketProviderError)."""
        creation = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1)
        )
        raw_tickets = [t.model_dump(mode="json") for t in creation.tickets]
        fake_jira.forget_all()  # le provider ne connaît plus le ticket

        refreshed = await ticket_service.refresh_ticket_statuses(raw_tickets)

        assert len(refreshed) == 1
        assert refreshed[0]["status"] == "To Do"  # statut conservé tel quel
        assert refreshed[0]["last_synced_at"] == raw_tickets[0]["last_synced_at"]  # pas resynchronisé

    @pytest.mark.asyncio
    async def test_refresh_without_jira_config_keeps_tickets(self, fake_jira):
        creation = await ticket_service.create_tickets(session_id="s1", test_design=_sample_test_design(n_stories=1))
        raw_tickets = [t.model_dump(mode="json") for t in creation.tickets]
        fake_jira.configured = False

        refreshed = await ticket_service.refresh_ticket_statuses(raw_tickets)

        assert refreshed == raw_tickets
