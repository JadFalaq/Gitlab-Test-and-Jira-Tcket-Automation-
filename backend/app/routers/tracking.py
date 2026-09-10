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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.dependencies import get_current_user
from app.models.session import UserModel
from app.schemas.ticket import TrackedTicket
from app.services import tracking_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

# Module 8 : garde d'authentification simple (pas de filtrage par
# utilisateur — mêmes tickets pour toute l'équipe, cf. routers/analytics.py).


@router.get("/tickets", response_model=list[TrackedTicket])
async def list_tracked_tickets(
    db: AsyncSession = Depends(get_db), _current_user: UserModel = Depends(get_current_user)
) -> list[TrackedTicket]:
    return await tracking_service.list_tracked_tickets(db)


@router.post("/sync", response_model=list[TrackedTicket])
async def sync_tickets(
    db: AsyncSession = Depends(get_db), _current_user: UserModel = Depends(get_current_user)
) -> list[TrackedTicket]:
    return await tracking_service.sync_all_tickets(db)


def _to_sse(tickets: list[TrackedTicket]) -> str:
    payload = json.dumps([t.model_dump(mode="json") for t in tickets], ensure_ascii=False)
    return f"data: {payload}\n\n"


@router.get("/stream")
async def stream_tracking(request: Request, _current_user: UserModel = Depends(get_current_user)):
    """
    Flux SSE : un événement `data: [...]` (liste de TrackedTicket) toutes les
    TRACKING_SYNC_INTERVAL_SECONDS secondes, tant que le client reste connecté.
    Chaque tick ouvre sa propre session DB (le flux vit plus longtemps qu'une
    requête HTTP classique, donc pas de Depends(get_db) ici).

    Module 8 : la garde d'authentification lit le cookie de session — le
    frontend doit ouvrir l'EventSource avec `withCredentials: true` pour que
    le cookie parte en cross-origin (cf. TrackingDashboard.tsx), sinon 401.
    """

    async def event_generator():
        while True:
            if await request.is_disconnected():
                logger.debug("Tracking SSE: client déconnecté, arrêt du flux")
                break

            async with AsyncSessionLocal() as db:
                try:
                    tickets = await tracking_service.sync_all_tickets(db)
                except Exception as exc:  # best-effort : le flux ne doit jamais mourir sur une erreur provider
                    logger.warning("Tracking SSE: échec de sync, snapshot en cache utilisé: %s", exc)
                    tickets = await tracking_service.list_tracked_tickets(db)

            yield _to_sse(tickets)
            await asyncio.sleep(settings.TRACKING_SYNC_INTERVAL_SECONDS)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
