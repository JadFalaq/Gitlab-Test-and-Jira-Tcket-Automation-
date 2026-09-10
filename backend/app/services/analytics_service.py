"""
Module 7 — Analytics : calcul des agrégats

Lit toutes les sessions (app/models/session.py) et calcule les métriques du
tableau de bord analytics. Pas de table dédiée : tout est recalculé à la
volée depuis `sessions`, cohérent avec l'échelle prototype (cf.
app/schemas/analytics.py pour les choix de design).
"""

from __future__ import annotations

from collections import Counter
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import SessionModel
from app.schemas.analytics import (
    AnalyticsSummary,
    PriorityBreakdown,
    StatusBreakdown,
    TicketProviderBreakdown,
    TypeBreakdown,
)

_COMPLETED_STATUSES = {"validated", "tickets_created"}
_REJECTED_STATUSES = {"requirement_rejected", "tests_rejected"}


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def compute_summary(sessions: list[SessionModel]) -> AnalyticsSummary:
    total = len(sessions)

    status_counts = Counter(s.status for s in sessions)
    type_counts = Counter(
        s.requirement_analysis["type"] for s in sessions if s.requirement_analysis
    )
    priority_counts = Counter(
        s.requirement_analysis["priority"] for s in sessions if s.requirement_analysis
    )

    confidence_scores = [
        s.requirement_analysis["confidence_score"] for s in sessions if s.requirement_analysis
    ]
    scenario_counts = [
        s.test_design_output["test_summary"]["total_scenarios"]
        for s in sessions
        if s.test_design_output
    ]

    total_req_regen = sum(s.requirement_regenerations for s in sessions)
    total_test_regen = sum(s.test_regenerations for s in sessions)

    completed = sum(1 for s in sessions if s.status in _COMPLETED_STATUSES)
    rejected = sum(1 for s in sessions if s.status in _REJECTED_STATUSES)

    all_tickets = [t for s in sessions for t in (s.tickets or [])]
    provider_counts = Counter(t["provider"] for t in all_tickets)

    # completed_at (figé à la validation, module 4) plutôt que updated_at
    # (qui continue de changer à chaque sync de tickets, module 6) — cf.
    # docs/module_7_analytics.md, point d'incertitude #1 (résolu). Sessions
    # sans completed_at (ex: créées avant l'ajout du champ) exclues plutôt
    # que de crasher sur une soustraction avec None.
    durations = [
        (s.completed_at - s.created_at).total_seconds()
        for s in sessions
        if s.status in _COMPLETED_STATUSES and s.completed_at is not None
    ]

    return AnalyticsSummary(
        total_sessions=total,
        sessions_by_status=[StatusBreakdown(status=k, count=v) for k, v in sorted(status_counts.items())],
        sessions_by_type=[TypeBreakdown(type=k, count=v) for k, v in sorted(type_counts.items())],
        sessions_by_priority=[
            PriorityBreakdown(priority=k, count=v) for k, v in sorted(priority_counts.items())
        ],
        average_confidence_score=_safe_mean(confidence_scores),
        average_scenarios_per_session=_safe_mean(scenario_counts),
        total_requirement_regenerations=total_req_regen,
        total_test_regenerations=total_test_regen,
        average_regenerations_per_session=(
            (total_req_regen + total_test_regen) / total if total > 0 else None
        ),
        completed_sessions=completed,
        rejected_sessions=rejected,
        acceptance_rate=(
            completed / (completed + rejected) if (completed + rejected) > 0 else None
        ),
        total_tickets_created=len(all_tickets),
        tickets_by_provider=[
            TicketProviderBreakdown(provider=k, count=v) for k, v in sorted(provider_counts.items())
        ],
        average_pipeline_duration_seconds=_safe_mean(durations),
    )


async def get_summary(db: AsyncSession) -> AnalyticsSummary:
    result = await db.execute(select(SessionModel))
    sessions = list(result.scalars().all())
    return compute_summary(sessions)


__all__ = ["compute_summary", "get_summary"]
