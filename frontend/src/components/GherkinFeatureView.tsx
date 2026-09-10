import type { GherkinFeature } from "../types";

/**
 * Rendu texte d'une GherkinFeature, en miroir de
 * app/agents/gherkin_renderer.py::render_gherkin_feature (backend).
 * Purement présentationnel ici — la validation syntaxique réelle a déjà
 * eu lieu côté backend (module 3) avant que ces données n'arrivent ici.
 */
function renderGherkinFeature(feature: GherkinFeature): string {
  const lines: string[] = [];

  if (feature.tags.length > 0) lines.push(feature.tags.join(" "));
  lines.push(`Feature: ${feature.feature_name}`);

  if (feature.description) {
    for (const line of feature.description.split("\n")) lines.push(`  ${line}`);
  }

  for (const scenario of feature.scenarios) {
    lines.push("");
    if (scenario.tags.length > 0) lines.push("  " + scenario.tags.join(" "));
    lines.push(`  Scenario: ${scenario.name}`);
    for (const step of scenario.steps) lines.push(`    ${step.keyword} ${step.text}`);
  }

  return lines.join("\n");
}

export function GherkinFeatureView({ feature }: { feature: GherkinFeature }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-slate-800 bg-black/60 p-4 font-mono text-sm text-cyan-100/90">
      {renderGherkinFeature(feature)}
    </pre>
  );
}
