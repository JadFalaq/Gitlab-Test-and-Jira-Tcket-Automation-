"""
Module 6 — Suivi temps réel : routes HTTP

Trois façons de consommer le suivi des tickets, du plus simple au plus
"temps réel" :
- GET /api/tracking/tickets  — snapshot instantané (DB, pas d'appel provider)
- POST /api/tracking/sync    — rafraîchit vraiment les statuts (appel provider)
- GET /api/tracking/stream   — Server-Sent Events : pousse un snapshot toutes
  les `settings.TRACKING_SYNC_INTERVAL_SECONDS`, avec sync provider inclus.
  Pas de dépendance supplémentaire (sse-starlette, websockets) : un simple
  générateur async + StreamingResponse suffit pour ce prototype.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import get_db, get_session_factory
from app.dependencies import get_current_user
from app.models.session import UserModel
from app.schemas.ticket import TrackedTicket
from app.services import tracking_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

# Module 8 : chaque route ne renvoie que les tickets des sessions de
# l'utilisateur connecté (contrôle de propriété, comme routers/sessions.py).


@router.get("/tickets", response_model=list[TrackedTicket])
async def list_tracked_tickets(
    db: AsyncSession = Depends(get_db), current_user: UserModel = Depends(get_current_user)
) -> list[TrackedTicket]:
    return await tracking_service.list_tracked_tickets(db, current_user.id)


@router.post("/sync", response_model=list[TrackedTicket])
async def sync_tickets(
    db: AsyncSession = Depends(get_db), current_user: UserModel = Depends(get_current_user)
) -> list[TrackedTicket]:
    return await tracking_service.sync_user_tickets(db, current_user.id)


def _to_sse(tickets: list[TrackedTicket]) -> str:
    payload = json.dumps([t.model_dump(mode="json") for t in tickets], ensure_ascii=False)
    return f"data: {payload}\n\n"


async def tracking_snapshot(session_factory: async_sessionmaker[AsyncSession], user_id: str) -> list[TrackedTicket]:
    """Un tick du flux SSE : synchronise puis renvoie les tickets de `user_id`.
    Best-effort : en cas d'erreur provider, renvoie le snapshot en cache."""
    async with session_factory() as db:
        try:
            return await tracking_service.sync_user_tickets(db, user_id)
        except Exception as exc:  # le flux ne doit jamais mourir sur une erreur provider
            logger.warning("Tracking SSE: échec de sync, snapshot en cache utilisé: %s", exc)
            return await tracking_service.list_tracked_tickets(db, user_id)


@router.get("/stream")
async def stream_tracking(
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """
    Flux SSE : un événement `data: [...]` (liste de TrackedTicket) toutes les
    TRACKING_SYNC_INTERVAL_SECONDS secondes, tant que le client reste connecté.
    Chaque tick ouvre sa propre session DB (le flux vit plus longtemps qu'une
    requête HTTP classique, donc pas de Depends(get_db) ici). Seuls les
    tickets des sessions de l'utilisateur connecté sont envoyés.

    Module 8 : la garde d'authentification lit le cookie de session — le
    frontend doit ouvrir l'EventSource avec `withCredentials: true` pour que
    le cookie parte en cross-origin (cf. TrackingDashboard.tsx), sinon 401.
    """

    user_id = current_user.id

    async def event_generator():
        while True:
            if await request.is_disconnected():
                logger.debug("Tracking SSE: client déconnecté, arrêt du flux")
                break

            yield _to_sse(await tracking_snapshot(session_factory, user_id))
            await asyncio.sleep(settings.TRACKING_SYNC_INTERVAL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
