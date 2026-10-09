"""
Module 5 — Intégration Jira : routes HTTP

Étend la machine à états de session_service.py (états 'validated' /
'ticket_creation_failed' / 'tickets_created'). Mêmes conventions que
app/routers/sessions.py : les erreurs de logique métier sont converties en
réponses HTTP par les gestionnaires enregistrés dans app/main.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.session import SessionModel, UserModel
from app.schemas.session import SessionResponse
from app.services import session_service

router = APIRouter(prefix="/api/sessions", tags=["tickets"])


@router.post("/{session_id}/tickets/create", response_model=SessionResponse)
async def create_tickets(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.create_tickets(db, session_id, current_user.id)


@router.post("/{session_id}/tickets/refresh", response_model=SessionResponse)
async def refresh_tickets(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.refresh_tickets(db, session_id, current_user.id)
