import type { RequirementAnalysis } from "../types";
import { ReviewActions } from "./ReviewActions";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</h3>
      <div className="mt-1 text-sm text-slate-300">{children}</div>
    </div>
  );
}

function TagList({ items, empty = "—" }: { items: string[]; empty?: string }) {
  if (items.length === 0) return <span className="text-slate-600">{empty}</span>;
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

interface RequirementPanelProps {
  analysis: RequirementAnalysis;
  busy: boolean;
  onAccept: () => void;
  onReject: () => void;
  onRegenerate: (guidance?: string) => void;
}

export function RequirementPanel({
  analysis,
  busy,
  onAccept,
  onReject,
  onRegenerate,
}: RequirementPanelProps) {
  return (
    <section className="space-y-4 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-cyan-400">
            {analysis.type} · priorité {analysis.priority}
          </p>
          <h2 className="text-lg font-semibold text-slate-50">{analysis.title}</h2>
        </div>
        <span className="shrink-0 rounded-full border border-slate-800 bg-slate-900 px-2.5 py-1 text-xs text-slate-400">
          confiance {(analysis.confidence_score * 100).toFixed(0)}%
        </span>
      </header>

      <p className="text-sm text-slate-300">{analysis.description}</p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Personas">
          <TagList items={analysis.user_personas} />
        </Field>
        <Field label="Dépendances">
          <TagList items={analysis.dependencies} />
        </Field>
        <Field label="Critères fonctionnels">
          <TagList items={analysis.functional_criteria} />
        </Field>
        <Field label="Critères non-fonctionnels">
          <TagList items={analysis.non_functional_criteria} />
        </Field>
      </div>

      {analysis.ambiguities.length > 0 && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-300">
            Ambiguïtés à trancher
          </h3>
          <div className="mt-1 text-sm text-amber-100/80">
            <TagList items={analysis.ambiguities} />
          </div>
        </div>
      )}

      <ReviewActions
        onAccept={onAccept}
        onReject={onReject}
        onRegenerate={onRegenerate}
        busy={busy}
        acceptLabel="Valider l'analyse → générer les tests"
      />
    </section>
  );
}
