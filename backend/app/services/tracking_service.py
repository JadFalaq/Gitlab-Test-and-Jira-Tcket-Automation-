"""
Module 6 — Suivi temps réel : agrégation des tickets créés (toutes sessions)

Fournit la vue "tableau de suivi" consommée par le frontend : tous les
tickets créés (module 5), avec leur session d'origine et leur dernier statut
connu. Deux modes :
- `list_tracked_tickets` : lecture rapide depuis la DB (statuts en cache,
  pas d'appel réseau) — utilisé par le polling / SSE fréquent.
- `sync_all_tickets` : interroge réellement chaque provider pour rafraîchir
  les statuts avant de les persister — plus lent, déclenché explicitement
  (bouton "Sync" ou tick périodique du flux SSE, cf. app/routers/tracking.py).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import SessionModel
from app.schemas.ticket import TrackedTicket
from app.services import ticket_service


def _to_tracked(session: SessionModel) -> list[TrackedTicket]:
    title = (
        session.requirement_analysis["title"] if session.requirement_analysis else session.raw_input[:80]
    )
    return [
        TrackedTicket(
            provider=t["provider"],
            external_id=t["external_id"],
            key=t["key"],
            url=t["url"],
            title=t["title"],
            status=t["status"],
            session_id=session.id,
            session_title=title,
            last_synced_at=t["last_synced_at"],
        )
        for t in (session.tickets or [])
    ]


async def list_tracked_tickets(db: AsyncSession) -> list[TrackedTicket]:
    result = await db.execute(select(SessionModel))
    sessions = [s for s in result.scalars().all() if s.tickets]
    tracked = [t for s in sessions for t in _to_tracked(s)]
    tracked.sort(key=lambda t: t.last_synced_at, reverse=True)
    return tracked


async def sync_all_tickets(db: AsyncSession) -> list[TrackedTicket]:
    result = await db.execute(select(SessionModel))
    sessions = [s for s in result.scalars().all() if s.tickets]

    for session in sessions:
        session.tickets = await ticket_service.refresh_ticket_statuses(session.tickets)
    await db.commit()

    tracked = [t for s in sessions for t in _to_tracked(s)]
    tracked.sort(key=lambda t: t.last_synced_at, reverse=True)
    return tracked


__all__ = ["list_tracked_tickets", "sync_all_tickets"]
