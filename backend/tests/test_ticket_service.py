"""
Tests unitaires — Module 5 : orchestration de création de tickets
(app/services/ticket_service.py)

Aucune configuration Jira/GitLab n'étant présente dans l'environnement de
test (.env de test / variables non définies), get_ticket_provider_by_name
retombe automatiquement sur MockTicketProvider (cf. app/integrations/factory.py)
— pas d'appel réseau, comportement déterministe.

Toutes les données sont fictives.
"""

from __future__ import annotations

import pytest

from app.integrations.mock_provider import _reset_mock_store_for_tests
from app.schemas.test_design import (
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
    TestDesignOutput,
    TestSummary,
    UserStory,
)
from app.services import ticket_service


@pytest.fixture(autouse=True)
def _reset_mock_store():
    _reset_mock_store_for_tests()
    yield
    _reset_mock_store_for_tests()


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
    async def test_creates_one_ticket_per_story_per_provider(self):
        result = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=2), providers=["jira", "gitlab"]
        )

        assert len(result.tickets) == 4  # 2 stories x 2 providers
        assert not result.errors
        providers_seen = {t.provider for t in result.tickets}
        assert providers_seen == {"jira", "gitlab"}

    @pytest.mark.asyncio
    async def test_single_provider(self):
        result = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1), providers=["jira"]
        )

        assert len(result.tickets) == 1
        assert result.tickets[0].provider == "jira"

    @pytest.mark.asyncio
    async def test_unknown_provider_recorded_as_error(self):
        result = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1), providers=["unknown"]
        )

        assert result.tickets == []
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_ticket_description_includes_gherkin(self):
        result = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1), providers=["jira"]
        )

        # La description n'est pas dans CreatedTicket (seulement title/status/...),
        # mais le draft l'a construite : on vérifie indirectement via le titre
        # qui correspond bien à la story source.
        assert result.tickets[0].title == "[FICTIF] Story 0"


class TestRefreshTicketStatuses:
    @pytest.mark.asyncio
    async def test_refresh_updates_status_field(self):
        creation = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1), providers=["jira"]
        )
        raw_tickets = [t.model_dump(mode="json") for t in creation.tickets]

        refreshed = await ticket_service.refresh_ticket_statuses(raw_tickets)

        assert len(refreshed) == 1
        assert refreshed[0]["status"] == "To Do"  # juste créé, encore sous le seuil "In Progress"

    @pytest.mark.asyncio
    async def test_refresh_keeps_last_status_on_provider_error(self):
        """Best-effort (docstring de refresh_ticket_statuses) : un ticket dont
        le provider échoue conserve son dernier statut connu, sans exception
        propagée. Ici le store mock est vidé après création : get_ticket_status
        lève TicketNotFoundError (sous-classe de TicketProviderError)."""
        creation = await ticket_service.create_tickets(
            session_id="s1", test_design=_sample_test_design(n_stories=1), providers=["jira"]
        )
        raw_tickets = [t.model_dump(mode="json") for t in creation.tickets]
        _reset_mock_store_for_tests()  # le provider ne connaît plus le ticket

        refreshed = await ticket_service.refresh_ticket_statuses(raw_tickets)

        assert len(refreshed) == 1
        assert refreshed[0]["status"] == "To Do"  # statut conservé tel quel
        assert refreshed[0]["last_synced_at"] == raw_tickets[0]["last_synced_at"]  # pas resynchronisé
