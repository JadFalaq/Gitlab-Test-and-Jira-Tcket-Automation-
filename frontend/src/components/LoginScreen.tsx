import { useEffect, useState } from "react";
import { api } from "../api";
import type { UserResponse } from "../types";

interface LoginScreenProps {
  error: string | null;
  onDevLogin: (user: UserResponse) => void;
}

export function LoginScreen({ error, onDevLogin }: LoginScreenProps) {
  // undefined tant qu'on ne sait pas encore si le SSO est configuré côté
  // backend — évite d'afficher puis de faire disparaître le bouton SSO.
  const [ssoConfigured, setSsoConfigured] = useState<boolean | undefined>(undefined);
  const [email, setEmail] = useState("");
  const [devError, setDevError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .authConfig()
      .then((config) => setSsoConfigured(config.sso_configured))
      .catch(() => setSsoConfigured(false)); // best-effort : par défaut, propose la connexion de dev
  }, []);

  const handleDevLogin = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    setDevError(null);
    try {
      onDevLogin(await api.devLogin(email.trim()));
    } catch (err) {
      setDevError(
        err instanceof Error
          ? err.message
          : "Connexion de développement indisponible (SSO probablement déjà configuré)."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 p-6">
      <div className="pointer-events-none absolute inset-0 bg-grid bg-grid [mask-image:radial-gradient(ellipse_60%_50%_at_50%_0%,black,transparent)]" />
      <div className="pointer-events-none absolute left-1/2 top-0 h-[420px] w-[420px] -translate-x-1/2 -translate-y-1/3 rounded-full bg-cyan-500/20 blur-[120px]" />

      <div className="relative w-full max-w-sm space-y-6 rounded-xl border border-slate-800 bg-slate-900/80 p-8 text-center shadow-glow backdrop-blur-sm">
        <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-500/30 bg-cyan-500/10">
          <span className="h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_8px_2px_rgba(34,211,238,0.6)]" />
        </div>

        <div>
          <h1 className="text-lg font-semibold tracking-tight text-slate-50">
            Agent de génération de tests &amp; tickets Jira
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            Connecte-toi avec ton compte d'entreprise pour accéder à tes sessions.
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-left text-sm text-red-300">
            {error}
          </div>
        )}

        {ssoConfigured === undefined && (
          <p className="text-xs text-slate-600">Vérification de la configuration…</p>
        )}

        {ssoConfigured === true && (
          <a
            href={api.loginUrl()}
            className="flex w-full items-center justify-center gap-2.5 rounded-md bg-cyan-500 px-4 py-2.5 text-sm font-medium text-slate-950 shadow-glow-sm transition hover:bg-cyan-400"
          >
            <svg viewBox="0 0 21 21" className="h-4 w-4 shrink-0" aria-hidden="true">
              <rect x="1" y="1" width="9" height="9" fill="#f25022" />
              <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
              <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
              <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
            </svg>
            Se connecter avec Microsoft 365
          </a>
        )}

        {/* SSO pas encore configuré côté backend : la connexion de dev est le
            SEUL chemin qui marche, donc on la met directement en avant plutôt
            que de la cacher derrière un bouton qui échouerait (503). */}
        {ssoConfigured === false && (
          <div className="space-y-3 text-left">
            <div className="rounded-md border border-amber-500/25 bg-amber-500/10 p-3 text-xs text-amber-200">
              SSO Microsoft 365 pas encore configuré côté serveur (en attente d'accès admin Entra) — connexion de
              développement en attendant.
            </div>
            <form onSubmit={handleDevLogin} className="space-y-2">
              <label htmlFor="dev-email" className="text-xs font-medium text-slate-400">
                Ton email
              </label>
              <input
                id="dev-email"
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="ton.email@exemple.com"
                autoFocus
                className="w-full rounded-md border border-slate-800 bg-slate-950 p-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
              />
              {devError && <p className="text-xs text-red-400">{devError}</p>}
              <button
                type="submit"
                disabled={busy || !email.trim()}
                className="w-full rounded-md bg-cyan-500 px-4 py-2.5 text-sm font-medium text-slate-950 shadow-glow-sm transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
              >
                {busy ? "Connexion…" : "Entrer"}
              </button>
            </form>
          </div>
        )}

        <p className="text-xs text-slate-600">Prototype — toutes les données sont fictives.</p>
      </div>
    </div>
  );
}
