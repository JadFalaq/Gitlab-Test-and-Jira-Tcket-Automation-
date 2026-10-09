"""
Faux provider Jira, réservé aux tests.

Branché par tests/conftest.py (fixture autouse `fake_jira`) à la place de
app.integrations.factory.get_ticket_provider : aucun test ne contacte Jira.

Simule une progression de statut (To Do -> In Progress -> Done) selon le
temps écoulé depuis la création, pour tester le suivi (module 6). Le store
est propre à chaque instance : une instance neuve par test, rien à vider.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.integrations.base import (
    BaseTicketProvider,
    TicketNotFoundError,
    TicketProviderError,
    TicketProviderNotConfiguredError,
)
from app.schemas.ticket import CreatedTicket, TicketDraft

# Seuils de progression simulée (secondes écoulées depuis la création)
IN_PROGRESS_AFTER_SECONDS = 20
DONE_AFTER_SECONDS = 60


class FakeJiraProvider(BaseTicketProvider):
    """Faux Jira en mémoire. Réglages utilisables par les tests :

    - `configured = False` : la factory lève TicketProviderNotConfiguredError
      (cas "Jira non configuré").
    - `fail_creation = True` : chaque create_ticket lève TicketProviderError
      (cas "Jira injoignable / refuse la création").
    """

    def __init__(self) -> None:
        self.configured = True
        self.fail_creation = False
        self._created_at: dict[str, datetime] = {}
        self.created: list[TicketDraft] = []

    @property
    def provider_name(self) -> str:
        return "jira"

    async def create_ticket(self, draft: TicketDraft, session_id: str) -> CreatedTicket:
        if self.fail_creation:
            raise TicketProviderError("Création refusée (simulée)", provider=self.provider_name)

        external_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self._created_at[external_id] = now
        self.created.append(draft)

        key = f"FAKE-{len(self._created_at)}"
        return CreatedTicket(
            provider="jira",
            external_id=external_id,
            key=key,
            url=f"https://jira.fictif.test/browse/{key}",
            title=draft.title,
            status="To Do",
            session_id=session_id,
            user_story_title=draft.title,
            created_at=now,
            last_synced_at=now,
        )

    async def get_ticket_status(self, external_id: str) -> str:
        created_at = self._created_at.get(external_id)
        if created_at is None:
            raise TicketNotFoundError(f"Ticket '{external_id}' introuvable", provider=self.provider_name)

        elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
        if elapsed >= DONE_AFTER_SECONDS:
            return "Done"
        if elapsed >= IN_PROGRESS_AFTER_SECONDS:
            return "In Progress"
        return "To Do"

    # --- Helpers de test ---

    def backdate(self, external_id: str, seconds_ago: float) -> None:
        """Recule artificiellement la date de création pour tester la progression."""
        self._created_at[external_id] = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)

    def forget_all(self) -> None:
        """Simule des tickets supprimés côté Jira."""
        self._created_at.clear()

    def factory(self) -> BaseTicketProvider:
        """Remplaçant de app.integrations.factory.get_ticket_provider."""
        if not self.configured:
            raise TicketProviderNotConfiguredError("Jira n'est pas configuré (simulé)", provider="jira")
        return self
