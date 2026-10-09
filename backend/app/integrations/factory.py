"""
Module 5 — Factory du provider de tickets (Jira uniquement)

Retourne le JiraProvider réel si sa configuration est complète, sinon lève
`TicketProviderNotConfiguredError`. Il n'y a volontairement AUCUN repli
vers un provider factice : un ticket "créé" doit toujours exister dans Jira.
L'ancien repli silencieux vers un mock affichait "ticket créé" à
l'utilisateur alors que rien n'existait côté Jira.

Les tests remplacent `get_ticket_provider` par un faux provider
(tests/fakes/tickets.py, branché dans tests/conftest.py). Le reste du code
appelle donc toujours `factory.get_ticket_provider()` via le module, jamais
via un import direct de la fonction, pour que ce remplacement soit visible.
"""

from __future__ import annotations

from app.config import settings
from app.integrations.base import BaseTicketProvider, TicketProviderNotConfiguredError
from app.integrations.jira_provider import JiraProvider


def jira_configured() -> bool:
    return bool(settings.JIRA_BASE_URL and settings.JIRA_API_TOKEN and settings.JIRA_USER_EMAIL)


def get_ticket_provider() -> BaseTicketProvider:
    """Retourne le provider Jira configuré.

    Raises:
        TicketProviderNotConfiguredError: si JIRA_BASE_URL / JIRA_API_TOKEN /
            JIRA_USER_EMAIL ne sont pas tous renseignés.
    """
    if not jira_configured():
        raise TicketProviderNotConfiguredError(
            "Jira n'est pas configuré (JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_USER_EMAIL) : "
            "aucun ticket ne peut être créé.",
            provider="jira",
        )
    return JiraProvider()


__all__ = ["jira_configured", "get_ticket_provider"]
