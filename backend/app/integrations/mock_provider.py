"""
Module 5/6 — Provider de tickets factice (mock)

Utilisé pour :
- Les tests unitaires (pas besoin de compte Jira/GitLab réel)
- La démonstration du pipeline complet sans configuration
- Le fallback automatique quand JIRA_*/GITLAB_* ne sont pas renseignés dans
  .env (cf. app/integrations/factory.py) — mentionné dès .env.example :
  "laisser vide pour utiliser le mock"

Simule une progression de statut réaliste au fil du temps (To Do -> In
Progress -> Done) pour que le tableau de suivi temps réel (module 6) ait
quelque chose à montrer même sans provider réel connecté. Le mapping
external_id -> created_at est gardé en mémoire au niveau module (process),
suffisant pour un prototype mono-utilisateur/mono-instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.integrations.base import BaseTicketProvider, TicketNotFoundError
from app.schemas.ticket import CreatedTicket, TicketDraft

# Seuils de progression simulée (secondes écoulées depuis la création)
_IN_PROGRESS_AFTER_SECONDS = 20
_DONE_AFTER_SECONDS = 60

# Store en mémoire, partagé par toutes les instances du process — les
# tickets mock créés doivent "progresser" de la même façon quel que soit
# l'endroit d'où on interroge leur statut.
_MOCK_TICKETS: dict[str, datetime] = {}


class MockTicketProvider(BaseTicketProvider):
    """Provider factice — crée des tickets simulés, statut évolue avec le temps."""

    def __init__(self, provider_label: str = "mock"):
        # `provider_label` permet de simuler soit "jira" soit "gitlab" en
        # mode dégradé (mêmes clés attendues côté frontend/analytics), tout
        # en restant un provider mock (pas d'appel réseau).
        self._label = provider_label

    @property
    def provider_name(self) -> str:
        return self._label

    async def create_ticket(self, draft: TicketDraft, session_id: str) -> CreatedTicket:
        external_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        _MOCK_TICKETS[external_id] = now

        key = f"MOCK-{len(_MOCK_TICKETS)}"
        return CreatedTicket(
            provider=self.provider_name,
            external_id=external_id,
            key=key,
            url=f"https://example-mock.invalid/{self.provider_name}/{key}",
            title=draft.title,
            status="To Do",
            session_id=session_id,
            user_story_title=draft.title,
            created_at=now,
            last_synced_at=now,
        )

    async def get_ticket_status(self, external_id: str) -> str:
        created_at = _MOCK_TICKETS.get(external_id)
        if created_at is None:
            raise TicketNotFoundError(
                f"Ticket mock '{external_id}' introuvable", provider=self.provider_name
            )

        elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
        if elapsed >= _DONE_AFTER_SECONDS:
            return "Done"
        if elapsed >= _IN_PROGRESS_AFTER_SECONDS:
            return "In Progress"
        return "To Do"

    async def health_check(self) -> bool:
        return True


def _reset_mock_store_for_tests() -> None:
    """Réservé aux tests — vide le store en mémoire entre deux cas de test."""
    _MOCK_TICKETS.clear()


def _backdate_for_tests(external_id: str, seconds_ago: float) -> None:
    """Réservé aux tests — recule artificiellement created_at pour tester la progression."""
    _MOCK_TICKETS[external_id] = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


__all__ = ["MockTicketProvider"]
