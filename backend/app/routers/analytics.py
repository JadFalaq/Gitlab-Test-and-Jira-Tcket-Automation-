"""
Module 7 — Analytics : routes HTTP

Un seul endpoint en lecture seule, agrégé à la volée (voir
app/services/analytics_service.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.session import UserModel
from app.schemas.analytics import AnalyticsSummary
from app.services import analytics_service

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    db: AsyncSession = Depends(get_db), _current_user: UserModel = Depends(get_current_user)
) -> AnalyticsSummary:
    """
    Module 8 : simple garde d'authentification (il faut être connecté pour
    voir le dashboard), PAS de filtrage par utilisateur — analytics agrège
    volontairement TOUTE l'équipe : uniquement des compteurs, aucun contenu
    de session (contrairement au suivi, filtré par utilisateur, cf.
    routers/tracking.py). À revoir si un besoin de vue "mes stats à moi"
    apparaît.
    """
    return await analytics_service.get_summary(db)
