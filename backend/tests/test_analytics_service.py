"""
Tests unitaires — Module 7 : agrégats analytics (app/services/analytics_service.py)

Teste `compute_summary` en pur (pas de DB) avec des SessionModel construits
en mémoire — pas besoin de persistance pour vérifier une fonction
d'agrégation Python déterministe.

Toutes les données sont fictives.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.session import SessionModel
from app.services.analytics_service import compute_summary


def _session(
    status: str,
    *,
    req_type: str | None = None,
    priority: str | None = None,
    confidence: float | None = None,
    total_scenarios: int | None = None,
    tickets: list[dict] | None = None,
    requirement_regenerations: int = 0,
    test_regenerations: int = 0,
    pipeline_duration_seconds: float | None = None,
) -> SessionModel:
    # created_at loin dans le passé, updated_at "maintenant" : simule un
    # sync de tickets récent (module 6) qui a fait dériver updated_at bien
    # après la validation réelle — le calcul de durée ne doit PAS s'appuyer
    # dessus (cf. test_pipeline_duration_ignores_updated_at_drift).
    created = datetime.now(timezone.utc) - timedelta(hours=1)
    updated = datetime.now(timezone.utc)
    completed = created + timedelta(seconds=pipeline_duration_seconds) if pipeline_duration_seconds is not None else None

    requirement_analysis = None
    if req_type or priority or confidence is not None:
        requirement_analysis = {
            "title": "[FICTIF]",
            "type": req_type or "feature",
            "description": "d",
            "user_personas": [],
            "functional_criteria": [],
            "non_functional_criteria": [],
            "priority": priority or "medium",
            "dependencies": [],
            "ambiguities": [],
            "confidence_score": confidence if confidence is not None else 0.5,
        }

    test_design_output = None
    if total_scenarios is not None:
        test_design_output = {
            "user_stories": [],
            "gherkin_features": [],
            "test_summary": {"total_scenarios": total_scenarios, "coverage_notes": "", "suggested_test_types": []},
        }

    return SessionModel(
        id="id",
        status=status,
        raw_input="[FICTIF] demande",
        requirement_analysis=requirement_analysis,
        test_design_output=test_design_output,
        tickets=tickets or [],
        requirement_regenerations=requirement_regenerations,
        test_regenerations=test_regenerations,
        created_at=created,
        updated_at=updated,
        completed_at=completed,
    )


class TestComputeSummary:
    def test_empty_sessions(self):
        summary = compute_summary([])

        assert summary.total_sessions == 0
        assert summary.average_confidence_score is None
        assert summary.acceptance_rate is None

    def test_status_breakdown(self):
        sessions = [
            _session("validated"),
            _session("validated"),
            _session("requirement_rejected"),
        ]

        summary = compute_summary(sessions)

        counts = {b.status: b.count for b in summary.sessions_by_status}
        assert counts == {"validated": 2, "requirement_rejected": 1}

    def test_average_confidence_score(self):
        sessions = [
            _session("pending_requirement_review", confidence=0.8),
            _session("pending_requirement_review", confidence=0.4),
        ]

        summary = compute_summary(sessions)

        assert summary.average_confidence_score == pytest.approx(0.6)

    def test_acceptance_rate(self):
        sessions = [
            _session("validated"),
            _session("tickets_created"),
            _session("requirement_rejected"),
        ]

        summary = compute_summary(sessions)

        assert summary.completed_sessions == 2
        assert summary.rejected_sessions == 1
        assert summary.acceptance_rate == pytest.approx(2 / 3)

    def test_regeneration_counters(self):
        sessions = [
            _session("validated", requirement_regenerations=2, test_regenerations=1),
            _session("validated", requirement_regenerations=0, test_regenerations=0),
        ]

        summary = compute_summary(sessions)

        assert summary.total_requirement_regenerations == 2
        assert summary.total_test_regenerations == 1
        assert summary.average_regenerations_per_session == pytest.approx(1.5)

    def test_tickets_by_provider(self):
        sessions = [
            _session(
                "tickets_created",
                tickets=[
                    {"provider": "jira", "external_id": "1", "key": "P-1", "url": "u", "title": "t", "status": "s"},
                    {"provider": "gitlab", "external_id": "2", "key": "#2", "url": "u", "title": "t", "status": "s"},
                ],
            )
        ]

        summary = compute_summary(sessions)

        assert summary.total_tickets_created == 2
        counts = {b.provider: b.count for b in summary.tickets_by_provider}
        assert counts == {"jira": 1, "gitlab": 1}

    def test_pipeline_duration_only_for_completed(self):
        sessions = [
            _session("validated", pipeline_duration_seconds=100),
            _session("pending_requirement_review", pipeline_duration_seconds=999999),  # ignoré, pas complété
        ]

        summary = compute_summary(sessions)

        assert summary.average_pipeline_duration_seconds == pytest.approx(100, rel=0.1)

    def test_pipeline_duration_ignores_updated_at_drift(self):
        """Bug corrigé (module 7, point d'incertitude #1) : updated_at
        continue de changer après tickets_created (sync des tickets, module
        6), mais la durée du pipeline doit rester figée à
        completed_at - created_at. Le helper _session() met toujours
        updated_at à ~1h de created_at pour simuler ce drift : si le bug
        réapparaissait (retour à updated_at - created_at), cette assertion
        échouerait avec ~3600s au lieu de 42."""
        session = _session("tickets_created", pipeline_duration_seconds=42)

        summary = compute_summary([session])

        assert summary.average_pipeline_duration_seconds == pytest.approx(42, rel=0.1)

    def test_pipeline_duration_excludes_completed_sessions_without_completed_at(self):
        """Sessions sans completed_at (ex: créées avant l'ajout du champ) :
        exclues proprement de la moyenne plutôt que de crasher sur une
        soustraction avec None."""
        session = _session("validated")  # pas de pipeline_duration_seconds -> completed_at=None

        summary = compute_summary([session])

        assert summary.average_pipeline_duration_seconds is None
