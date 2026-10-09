"""
Tests unitaires — scripts/migrate_jira_only.py (nettoyage des tickets non Jira).

Toutes les données sont fictives.
"""

from __future__ import annotations

from app.models.session import SessionModel
from scripts.migrate_jira_only import is_real_jira_ticket, migrate_session

_REAL = {"provider": "jira", "url": "https://fictif.atlassian.net/browse/P-1"}
_FAKE_JIRA = {"provider": "jira", "url": "https://example-mock.invalid/jira/MOCK-1"}
_GITLAB = {"provider": "gitlab", "url": "https://gitlab.fictif.test/p/-/issues/1"}


def test_only_real_jira_tickets_are_kept():
    assert is_real_jira_ticket(_REAL)
    assert not is_real_jira_ticket(_FAKE_JIRA)
    assert not is_real_jira_ticket(_GITLAB)
    assert not is_real_jira_ticket({"provider": "mock", "url": "u"})


def test_session_without_real_tickets_goes_back_to_validated():
    session = SessionModel(status="tickets_created", tickets=[_FAKE_JIRA, _GITLAB], error_message="partiel")

    assert migrate_session(session) == (2, True)
    assert session.tickets == []
    assert session.status == "validated"
    assert session.error_message is None


def test_session_with_real_ticket_keeps_status():
    session = SessionModel(status="tickets_created", tickets=[_REAL, _GITLAB])

    assert migrate_session(session) == (1, False)
    assert session.tickets == [_REAL]
    assert session.status == "tickets_created"


def test_idempotent():
    session = SessionModel(status="tickets_created", tickets=[_REAL])

    assert migrate_session(session) == (0, False)
