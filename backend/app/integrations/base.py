"""
Module 5 — Intégration Jira/GitLab : couche d'abstraction

Isole le reste du système du tracker de tickets configuré, exactement comme
app/agents/llm_layer.py isole le système du provider LLM (module 1). Tout
appel de création/suivi de ticket passe par cette interface.

Architecture:
    BaseTicketProvider (ABC)
        ├── JiraProvider    — Jira Cloud REST API v3 (issue réelle)
        ├── GitLabProvider  — GitLab REST API v4 (issue réelle)
        └── MockTicketProvider — tickets simulés, statut évolue avec le temps
                                 (utile en démo sans compte Jira/GitLab et
                                 dans les tests, pas d'appel réseau)

    get_configured_ticket_providers() — factory (app/integrations/factory.py)
    qui retourne la liste des providers actifs selon la config (.env)

Design decisions:
    - Async par défaut (FastAPI est async), même convention que le module 1.
    - Les erreurs sont typées (TicketProviderError, TicketNotFoundError) pour
      que ticket_service.py puisse réagir sans dépendre du provider concret.
    - `get_ticket_status` est séparé de `create_ticket` : le suivi temps réel
      (module 6) appelle uniquement `get_ticket_status` en boucle, sans
      recréer de ticket.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.ticket import CreatedTicket, TicketDraft


class TicketProviderError(Exception):
    """Erreur générique d'un provider de tickets (Jira, GitLab)."""

    def __init__(self, message: str, provider: str, details: dict | None = None):
        self.provider = provider
        self.details = details or {}
        super().__init__(f"[{provider}] {message}")


class TicketNotFoundError(TicketProviderError):
    """Le ticket demandé n'existe plus (ou plus accessible) chez le provider."""

    pass


class BaseTicketProvider(ABC):
    """Interface abstraite pour tous les providers de tickets (Jira, GitLab, ...)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nom unique du provider (ex: 'jira', 'gitlab', 'mock')."""
        ...

    @abstractmethod
    async def create_ticket(self, draft: TicketDraft, session_id: str) -> CreatedTicket:
        """
        Crée un ticket chez le provider à partir d'un draft pivot.

        Raises:
            TicketProviderError: en cas d'échec de création (auth, réseau, validation serveur)
        """
        ...

    @abstractmethod
    async def get_ticket_status(self, external_id: str) -> str:
        """
        Récupère le statut courant d'un ticket déjà créé.

        Raises:
            TicketNotFoundError: si le ticket n'existe plus chez le provider
            TicketProviderError: en cas d'autre échec (réseau, auth)
        """
        ...

    async def health_check(self) -> bool:
        """Vérifie que le provider est accessible et correctement configuré."""
        return True


__all__ = ["BaseTicketProvider", "TicketProviderError", "TicketNotFoundError"]
