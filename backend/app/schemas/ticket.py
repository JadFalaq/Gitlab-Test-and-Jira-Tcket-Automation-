"""
Module 5 — Intégration Jira : Schémas d'interface (Input/Output)

Transforme une session validée (RequirementAnalysis + TestDesignOutput, sortie
du module 4) en tickets Jira Cloud.

Design decisions:
- `TicketDraft` est le format pivot, indépendant de l'API : le service
  (app/services/ticket_service.py) construit un draft par user story, puis
  le provider (app/integrations/jira_provider.py) le traduit vers son format
  d'API natif — même logique que LLMRequest/LLMResponse au module 1.
- Un ticket créé garde l'URL réelle pour que l'ingénieur puisse cliquer
  directement vers Jira depuis l'UI.
- `status` est une chaîne libre (pas de Literal) : les statuts Jira dépendent
  du workflow configuré dans chaque projet ("To Do", "In Review", ...).
- `provider` reste stocké avec chaque ticket (toujours "jira") pour garder un
  format de données explicite si un autre tracker est ajouté un jour.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TicketProviderName = Literal["jira"]


class TicketDraft(BaseModel):
    """Contenu pivot d'un ticket à créer, indépendant du provider cible."""

    title: str = Field(..., min_length=1)
    description: str = Field(default="")
    labels: list[str] = Field(default_factory=list)
    issue_type: Literal["story", "bug", "task"] = Field(default="story")


class CreatedTicket(BaseModel):
    """Un ticket effectivement créé chez un provider externe."""

    provider: TicketProviderName = "jira"
    external_id: str = Field(..., description="Identifiant interne Jira (id numérique de l'issue)")
    key: str = Field(..., description="Clé lisible (ex: 'PROTO-42')")
    url: str = Field(..., description="Lien direct vers le ticket")
    title: str
    status: str = Field(..., description="Statut brut renvoyé par le provider")
    session_id: str
    user_story_title: str = Field(..., description="User story source de ce ticket")
    created_at: datetime
    last_synced_at: datetime


class TicketCreationResult(BaseModel):
    """Résultat d'une tentative de création de tickets pour une session."""

    tickets: list[CreatedTicket] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TicketStatusUpdate(BaseModel):
    """Statut rafraîchi d'un ticket existant, après appel provider.get_ticket_status."""

    external_id: str
    status: str
    last_synced_at: datetime


class TrackedTicket(BaseModel):
    """Vue agrégée d'un ticket pour le tableau de suivi temps réel (module 6)."""

    provider: TicketProviderName = "jira"
    external_id: str
    key: str
    url: str
    title: str
    status: str
    session_id: str
    session_title: str
    last_synced_at: datetime


__all__ = [
    "TicketProviderName",
    "TicketDraft",
    "CreatedTicket",
    "TicketCreationResult",
    "TicketStatusUpdate",
    "TrackedTicket",
]
