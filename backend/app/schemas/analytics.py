"""
Module 7 — Analytics : Schémas de sortie

Agrégats calculés à partir de `SessionModel` (module 4) et des tickets créés
(module 5). Lecture seule — pas d'input, uniquement des vues calculées par
app/services/analytics_service.py.

Design decisions:
- Tous les agrégats sont calculés à la volée depuis la table `sessions`
  (pas de table dédiée) : cohérent avec l'échelle prototype (mono-utilisateur,
  volume de sessions faible). À revoir si le volume grandit (cf. points
  d'incertitude module 4 sur l'absence de pagination).
- Les taux (`acceptance_rate`, ...) sont `Optional[float]` : `None` quand le
  dénominateur est nul (pas encore de session terminée), pour distinguer
  explicitement "0%" de "pas encore de donnée".
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class StatusBreakdown(BaseModel):
    status: str
    count: int


class TypeBreakdown(BaseModel):
    type: str
    count: int


class PriorityBreakdown(BaseModel):
    priority: str
    count: int


class AnalyticsSummary(BaseModel):
    """Vue d'ensemble du pipeline — sert le tableau de bord analytics (frontend)."""

    total_sessions: int = Field(..., ge=0)
    sessions_by_status: list[StatusBreakdown]
    sessions_by_type: list[TypeBreakdown]
    sessions_by_priority: list[PriorityBreakdown]

    average_confidence_score: Optional[float] = Field(
        default=None, description="Moyenne de confidence_score sur les sessions analysées"
    )
    average_scenarios_per_session: Optional[float] = Field(
        default=None, description="Moyenne de test_summary.total_scenarios sur les sessions avec tests générés"
    )

    total_requirement_regenerations: int = 0
    total_test_regenerations: int = 0
    average_regenerations_per_session: Optional[float] = None

    completed_sessions: int = Field(..., ge=0, description="validated + tickets_created")
    rejected_sessions: int = Field(..., ge=0, description="requirement_rejected + tests_rejected")
    acceptance_rate: Optional[float] = Field(
        default=None, description="completed / (completed + rejected), None si aucune session terminée"
    )

    total_tickets_created: int = 0

    average_pipeline_duration_seconds: Optional[float] = Field(
        default=None,
        description="Moyenne (updated_at - created_at) pour les sessions validées/tickets_created",
    )


__all__ = [
    "StatusBreakdown",
    "TypeBreakdown",
    "PriorityBreakdown",
    "AnalyticsSummary",
]
