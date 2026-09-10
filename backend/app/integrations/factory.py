"""
Module 5 — Factory des providers de tickets

Sélectionne automatiquement, pour chaque plateforme (Jira, GitLab), le
provider réel si sa configuration est complète, sinon le mock — même
principe que .env.example : "laisser vide pour utiliser le mock". Décision
prise au démarrage de chaque requête à partir de `settings` (pas de cache
global) pour que les tests puissent surcharger `settings` sans redémarrer
le process.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.integrations.base import BaseTicketProvider
from app.integrations.gitlab_provider import GitLabProvider
from app.integrations.jira_provider import JiraProvider
from app.integrations.mock_provider import MockTicketProvider

logger = logging.getLogger(__name__)


def _jira_configured() -> bool:
    return bool(settings.JIRA_BASE_URL and settings.JIRA_API_TOKEN and settings.JIRA_USER_EMAIL)


def _gitlab_configured() -> bool:
    return bool(settings.GITLAB_BASE_URL and settings.GITLAB_PRIVATE_TOKEN and settings.GITLAB_PROJECT_ID)


def get_jira_provider() -> BaseTicketProvider:
    if _jira_configured():
        return JiraProvider()
    logger.info("Jira non configuré (.env) — utilisation du MockTicketProvider pour 'jira'")
    return MockTicketProvider(provider_label="jira")


def get_gitlab_provider() -> BaseTicketProvider:
    if _gitlab_configured():
        return GitLabProvider()
    logger.info("GitLab non configuré (.env) — utilisation du MockTicketProvider pour 'gitlab'")
    return MockTicketProvider(provider_label="gitlab")


def get_configured_ticket_providers() -> list[BaseTicketProvider]:
    """Retourne les providers actifs (Jira + GitLab), réels ou mock selon la config."""
    return [get_jira_provider(), get_gitlab_provider()]


def get_ticket_provider_by_name(name: str) -> BaseTicketProvider:
    """Résout un provider par nom ('jira' ou 'gitlab'). Lève ValueError sinon."""
    if name == "jira":
        return get_jira_provider()
    if name == "gitlab":
        return get_gitlab_provider()
    raise ValueError(f"Provider de tickets inconnu : '{name}' (attendu: 'jira' ou 'gitlab')")


__all__ = [
    "get_jira_provider",
    "get_gitlab_provider",
    "get_configured_ticket_providers",
    "get_ticket_provider_by_name",
]
