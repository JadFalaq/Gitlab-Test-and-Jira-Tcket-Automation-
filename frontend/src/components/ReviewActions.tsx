import { useState } from "react";

interface ReviewActionsProps {
  onAccept: () => void;
  onReject: () => void;
  onRegenerate: (guidance?: string) => void;
  busy: boolean;
  acceptLabel?: string;
}

/**
 * Barre accept/reject/regenerate, avec une instruction libre optionnelle
 * pour la régénération — donne à l'ingénieur un contrôle direct sur ce que
 * le LLM produit ensuite (ex: "concentre-toi sur les cas d'erreur"), sans
 * édition manuelle champ par champ (cf. ordre de dev du projet, module 4
 * "minimal"). Le texte est injecté dans le prompt de CET appel LLM
 * uniquement (cf. app/services/session_service.py côté backend) — jamais
 * stocké sur la session elle-même.
 */
export function ReviewActions({
  onAccept,
  onReject,
  onRegenerate,
  busy,
  acceptLabel = "Valider",
}: ReviewActionsProps) {
  const [guidance, setGuidance] = useState("");
  const [showGuidance, setShowGuidance] = useState(false);

  const handleRegenerate = () => {
    onRegenerate(guidance.trim() || undefined);
    setGuidance("");
    setShowGuidance(false);
  };

  return (
    <div className="space-y-2.5">
      {showGuidance && (
        <div className="space-y-1">
          <label htmlFor="regen-guidance" className="text-xs font-medium text-slate-400">
            Instructions pour la régénération (optionnel)
          </label>
          <textarea
            id="regen-guidance"
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
            placeholder="Ex : concentre-toi sur les cas d'erreur, sois plus concis, ajoute un scénario limite…"
            className="w-full rounded-md border border-slate-800 bg-slate-900 p-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onAccept}
          disabled={busy}
          className="rounded-md bg-emerald-500 px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {acceptLabel}
        </button>
        <button
          type="button"
          onClick={handleRegenerate}
          disabled={busy}
          className="rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Régénérer
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Rejeter
        </button>
        <button
          type="button"
          onClick={() => setShowGuidance((prev) => !prev)}
          className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
        >
          {showGuidance ? "Masquer les instructions" : "Préciser avant de régénérer"}
        </button>
      </div>
    </div>
  );
}
