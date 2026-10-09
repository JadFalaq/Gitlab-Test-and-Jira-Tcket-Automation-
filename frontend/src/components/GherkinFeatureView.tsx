/**
 * Affiche le texte .feature rendu par le backend
 * (SessionResponse.gherkin_feature_files, cf. app/agents/gherkin_renderer.py) —
 * même texte que celui validé par le parseur Cucumber et envoyé dans Jira.
 */
export function GherkinFeatureView({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-slate-800 bg-black/60 p-4 font-mono text-sm text-cyan-100/90">
      {text}
    </pre>
  );
}
