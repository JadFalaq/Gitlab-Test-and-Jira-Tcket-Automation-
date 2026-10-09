import type { TestDesignOutput } from "../types";
import { GherkinFeatureView } from "./GherkinFeatureView";
import { ReviewActions } from "./ReviewActions";

interface TestDesignPanelProps {
  output: TestDesignOutput;
  featureFiles: string[];
  busy: boolean;
  onAccept: () => void;
  onReject: () => void;
  onRegenerate: (guidance?: string) => void;
}

export function TestDesignPanel({
  output,
  featureFiles,
  busy,
  onAccept,
  onReject,
  onRegenerate,
}: TestDesignPanelProps) {
  return (
    <section className="space-y-5 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-50">User stories</h2>
        <div className="mt-2 space-y-3">
          {output.user_stories.map((story) => (
            <div key={story.title} className="rounded-md border border-slate-800 bg-slate-900/40 p-3">
              <p className="text-sm font-medium text-slate-100">{story.title}</p>
              <p className="mt-1 text-sm text-slate-400">
                En tant que <strong className="text-slate-300">{story.as_a}</strong>, je veux {story.i_want}, afin
                que {story.so_that}.
              </p>
              {story.acceptance_criteria.length > 0 && (
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-sm text-slate-500">
                  {story.acceptance_criteria.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold text-slate-50">Scénarios Gherkin</h2>
        <div className="mt-2 space-y-3">
          {featureFiles.map((text, index) => (
            <GherkinFeatureView key={index} text={text} />
          ))}
        </div>
      </div>

      <div className="rounded-md border border-slate-800 bg-slate-900/40 p-3 text-sm text-slate-400">
        <p>
          {output.test_summary.total_scenarios} scénario(s) ·{" "}
          {output.test_summary.suggested_test_types.join(", ") || "types de test non précisés"}
        </p>
        {output.test_summary.coverage_notes && (
          <p className="mt-1 italic text-slate-500">{output.test_summary.coverage_notes}</p>
        )}
      </div>

      <ReviewActions
        onAccept={onAccept}
        onReject={onReject}
        onRegenerate={onRegenerate}
        busy={busy}
        acceptLabel="Valider les tests"
      />
    </section>
  );
}
