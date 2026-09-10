import type { SessionSummary } from "../types";

const STATUS_LABELS: Record<string, string> = {
  analyzing: "Analyse en cours…",
  pending_requirement_review: "Analyse à valider",
  requirement_failed: "Échec analyse",
  requirement_rejected: "Analyse rejetée",
  designing_tests: "Génération des tests en cours…",
  pending_test_review: "Tests à valider",
  test_design_failed: "Échec conception tests",
  tests_rejected: "Tests rejetés",
  validated: "Validé",
};

const STATUS_COLORS: Record<string, string> = {
  analyzing: "bg-cyan-500/10 text-cyan-300",
  pending_requirement_review: "bg-amber-500/10 text-amber-300",
  requirement_failed: "bg-red-500/10 text-red-300",
  requirement_rejected: "bg-slate-800 text-slate-500",
  designing_tests: "bg-cyan-500/10 text-cyan-300",
  pending_test_review: "bg-amber-500/10 text-amber-300",
  test_design_failed: "bg-red-500/10 text-red-300",
  tests_rejected: "bg-slate-800 text-slate-500",
  validated: "bg-emerald-500/10 text-emerald-300",
};

interface SessionHistoryProps {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

export function SessionHistory({ sessions, activeId, onSelect }: SessionHistoryProps) {
  if (sessions.length === 0) {
    return <p className="text-sm text-slate-600">Aucune session pour l'instant.</p>;
  }

  return (
    <ul className="space-y-1">
      {sessions.map((session) => (
        <li key={session.id}>
          <button
            type="button"
            onClick={() => onSelect(session.id)}
            className={`w-full rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-slate-900 ${
              session.id === activeId ? "border border-cyan-500/30 bg-slate-900 font-medium" : "border border-transparent"
            }`}
          >
            <p className="truncate text-slate-200">{session.title}</p>
            <span
              className={`mt-1 inline-block rounded-full px-2 py-0.5 text-xs ${
                STATUS_COLORS[session.status] ?? "bg-slate-800 text-slate-500"
              }`}
            >
              {STATUS_LABELS[session.status] ?? session.status}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
