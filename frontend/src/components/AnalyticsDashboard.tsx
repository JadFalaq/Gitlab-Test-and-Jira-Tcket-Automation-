import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnalyticsSummary } from "../types";

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes}min ${remaining}s`;
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-50">{value}</p>
    </div>
  );
}

function BarBreakdown({ title, items }: { title: string; items: { label: string; count: number }[] }) {
  const max = Math.max(1, ...items.map((i) => i.count));

  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        <p className="mt-2 text-sm text-slate-600">Pas encore de donnée.</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
      <div className="mt-3 space-y-2">
        {items.map((item) => (
          <div key={item.label}>
            <div className="flex justify-between text-xs text-slate-400">
              <span>{item.label}</span>
              <span className="tabular-nums">{item.count}</span>
            </div>
            <div className="mt-0.5 h-1.5 w-full rounded-full bg-slate-800">
              <div
                className="h-1.5 rounded-full bg-cyan-500 shadow-[0_0_6px_0_rgba(34,211,238,0.5)]"
                style={{ width: `${(item.count / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Module 7 — Tableau de bord analytics : agrégats calculés côté backend
 * (app/services/analytics_service.py). Lecture seule, pas de rafraîchissement
 * automatique (contrairement au suivi temps réel du module 6) — un simple
 * bouton "Actualiser" suffit à cette échelle.
 */
export function AnalyticsDashboard() {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setSummary(await api.getAnalyticsSummary());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  if (!summary) {
    return <p className="text-sm text-slate-600">{loading ? "Chargement…" : "Aucune donnée."}</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50">Analytics</h1>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-50"
        >
          {loading ? "Actualisation…" : "Actualiser"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Sessions totales" value={String(summary.total_sessions)} />
        <StatCard label="Terminées" value={String(summary.completed_sessions)} />
        <StatCard label="Rejetées" value={String(summary.rejected_sessions)} />
        <StatCard label="Taux d'acceptation" value={formatPercent(summary.acceptance_rate)} />
        <StatCard label="Confiance moyenne" value={formatPercent(summary.average_confidence_score)} />
        <StatCard
          label="Scénarios / session"
          value={summary.average_scenarios_per_session?.toFixed(1) ?? "—"}
        />
        <StatCard label="Tickets créés" value={String(summary.total_tickets_created)} />
        <StatCard label="Durée moyenne pipeline" value={formatDuration(summary.average_pipeline_duration_seconds)} />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <BarBreakdown
          title="Sessions par statut"
          items={summary.sessions_by_status.map((b) => ({ label: b.status, count: b.count }))}
        />
        <BarBreakdown
          title="Sessions par type"
          items={summary.sessions_by_type.map((b) => ({ label: b.type, count: b.count }))}
        />
        <BarBreakdown
          title="Sessions par priorité"
          items={summary.sessions_by_priority.map((b) => ({ label: b.priority, count: b.count }))}
        />
      </div>

      <div className="rounded-md border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">
        {summary.total_requirement_regenerations} régénération(s) d'analyse ·{" "}
        {summary.total_test_regenerations} régénération(s) de tests ·{" "}
        {summary.average_regenerations_per_session?.toFixed(2) ?? "—"} en moyenne / session
      </div>
    </div>
  );
}
