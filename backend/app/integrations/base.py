"""
Module 5 — Intégration Jira : couche d'abstraction

Isole le reste du système du tracker de tickets, exactement comme
app/agents/llm_layer.py isole le système du provider LLM (module 1). Tout
appel de création/suivi de ticket passe par cette interface.

Architecture:
    BaseTicketProvider (ABC)
        └── JiraProvider    — Jira Cloud REST API v3 (issue réelle)

    get_ticket_provider() — factory (app/integrations/factory.py), lève
    TicketProviderNotConfiguredError si Jira n'est pas configuré. Les tests
    utilisent un faux provider (tests/fakes/tickets.py).

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
    """Erreur générique d'un provider de tickets."""

    def __init__(self, message: str, provider: str, details: dict | None = None):
        self.provider = provider
        self.details = details or {}
        super().__init__(f"[{provider}] {message}")


class TicketNotFoundError(TicketProviderError):
    """Le ticket demandé n'existe plus (ou plus accessible) chez le provider."""

    pass


class TicketProviderNotConfiguredError(TicketProviderError):
    """Le provider de tickets n'est pas configuré : aucune création possible."""

    pass


class BaseTicketProvider(ABC):
    """Interface abstraite des providers de tickets."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nom unique du provider (ex: 'jira')."""
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


__all__ = [
    "BaseTicketProvider",
    "TicketProviderError",
    "TicketNotFoundError",
    "TicketProviderNotConfiguredError",
]
