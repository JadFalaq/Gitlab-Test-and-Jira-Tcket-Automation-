"""
Module 5 — Intégration Jira/GitLab : orchestration de la création de tickets

Transforme le `TestDesignOutput` validé d'une session (module 4) en tickets
créés chez un ou plusieurs providers externes (Jira, GitLab), puis rafraîchit
leur statut à la demande (module 6 — suivi temps réel).

Design decisions:
- Un ticket par user story (pas de granularité plus fine en prototype). La
  description embarque les critères d'acceptation de la story + le rendu
  Gherkin de TOUTES les features de la session : le schéma module 3
  (UserStory / GherkinFeature) ne les relie pas explicitement par ID, donc
  on ne peut pas garantir un mapping story -> scénario précis sans modifier
  un module déjà "terminé et testé". Documenté comme simplification connue
  (voir docs/module_5_jira_gitlab_integration.md).
- La création est best-effort par provider : un échec sur Jira n'empêche pas
  la tentative sur GitLab. `TicketCreationResult.errors` accumule les
  messages, la session passe en 'ticket_creation_failed' seulement si AUCUN
  ticket n'a pu être créé.
- `refresh_ticket_statuses` fonctionne à partir des tickets déjà stockés en
  DB (session.tickets), pas d'un nouvel appel de création.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.agents.gherkin_renderer import render_gherkin_feature
from app.integrations.base import TicketProviderError
from app.integrations.factory import get_ticket_provider_by_name
from app.schemas.test_design import TestDesignOutput
from app.schemas.ticket import CreatedTicket, TicketCreationResult, TicketDraft

logger = logging.getLogger(__name__)

_PRIORITY_LABEL_PREFIX = "priority::"


def _build_drafts(test_design: TestDesignOutput) -> list[TicketDraft]:
    """Un TicketDraft par user story, avec le rendu Gherkin complet en pièce jointe textuelle."""
    gherkin_appendix = "\n\n".join(
        render_gherkin_feature(feature) for feature in test_design.gherkin_features
    )

    drafts: list[TicketDraft] = []
    for story in test_design.user_stories:
        description_parts = [
            f"En tant que {story.as_a}, je veux {story.i_want}, afin que {story.so_that}.",
        ]
        if story.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in story.acceptance_criteria)
            description_parts.append(f"Critères d'acceptation :\n{criteria}")
        if gherkin_appendix:
            description_parts.append(f"Scénarios Gherkin associés à la session :\n\n{gherkin_appendix}")

        drafts.append(
            TicketDraft(
                title=story.title,
                description="\n\n".join(description_parts),
                labels=[f"{_PRIORITY_LABEL_PREFIX}{story.priority}"],
                issue_type="story",
            )
        )
    return drafts


async def create_tickets(
    session_id: str,
    test_design: TestDesignOutput,
    providers: list[str],
) -> TicketCreationResult:
    """
    Crée un ticket par user story sur chaque provider demandé.

    Args:
        session_id: id de la session source (traçabilité, cf. CreatedTicket.session_id)
        test_design: sortie validée du module 3/4
        providers: sous-ensemble de ["jira", "gitlab"] à utiliser

    Returns:
        TicketCreationResult (tickets créés + erreurs par provider, best-effort)
    """
    drafts = _build_drafts(test_design)
    tickets: list[CreatedTicket] = []
    errors: list[str] = []

    for provider_name in providers:
        try:
            provider = get_ticket_provider_by_name(provider_name)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        for draft in drafts:
            try:
                ticket = await provider.create_ticket(draft, session_id=session_id)
                tickets.append(ticket)
            except TicketProviderError as exc:
                logger.warning("ticket_service: échec création ticket %s: %s", provider_name, exc)
                errors.append(str(exc))

    return TicketCreationResult(tickets=tickets, errors=errors)


async def refresh_ticket_statuses(tickets: list[dict]) -> list[dict]:
    """
    Rafraîchit le statut de chaque ticket déjà créé, en interrogeant son provider.

    Best-effort : un ticket dont le rafraîchissement échoue conserve son
    dernier statut connu (pas d'exception propagée, cf. module 6 — le suivi
    ne doit jamais bloquer l'UI sur une erreur réseau ponctuelle).
    """
    updated: list[dict] = []
    for raw in tickets:
        ticket = CreatedTicket(**raw)
        try:
            provider = get_ticket_provider_by_name(ticket.provider)
            status = await provider.get_ticket_status(ticket.external_id)
            ticket = ticket.model_copy(update={"status": status, "last_synced_at": datetime.now(timezone.utc)})
        except TicketProviderError as exc:
            logger.warning("ticket_service: échec rafraîchissement statut %s: %s", ticket.key, exc)
        updated.append(ticket.model_dump(mode="json"))
    return updated


__all__ = ["create_tickets", "refresh_ticket_statuses"]
