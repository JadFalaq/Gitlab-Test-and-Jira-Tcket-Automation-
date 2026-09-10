import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { TrackedTicket } from "../types";

const STATUS_BADGE: Record<string, string> = {
  "To Do": "bg-slate-800 text-slate-400",
  "In Progress": "bg-amber-500/10 text-amber-300",
  Done: "bg-emerald-500/10 text-emerald-300",
  opened: "bg-slate-800 text-slate-400",
  closed: "bg-emerald-500/10 text-emerald-300",
};

const PROVIDER_LABEL: Record<string, string> = {
  jira: "Jira",
  gitlab: "GitLab",
  mock: "Mock",
};

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * Module 6 — Tableau de suivi temps réel des tickets créés (toutes sessions).
 * Se connecte au flux SSE /api/tracking/stream : chaque événement remplace
 * l'état local par le dernier snapshot connu (statuts rafraîchis côté
 * backend à intervalle régulier, cf. app/routers/tracking.py). Fallback sur
 * un fetch ponctuel si le flux ne s'ouvre pas (ex: proxy qui bufferise SSE).
 */
export function TrackingDashboard() {
  const [tickets, setTickets] = useState<TrackedTicket[]>([]);
  const [connected, setConnected] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api
      .listTrackedTickets()
      .then(setTickets)
      .catch(() => {
        /* le flux SSE prendra le relais */
      });

    // withCredentials: le cookie de session (module 8) doit partir en
    // cross-origin (frontend:5173 -> backend:8000) pour que /api/tracking/stream
    // ne renvoie pas 401.
    const source = new EventSource(api.trackingStreamUrl(), { withCredentials: true });
    eventSourceRef.current = source;

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (event) => {
      try {
        setTickets(JSON.parse(event.data) as TrackedTicket[]);
        setConnected(true);
      } catch {
        // Événement malformé — on garde le dernier état connu.
      }
    };

    return () => source.close();
  }, []);

  const handleSyncNow = async () => {
    setSyncing(true);
    try {
      setTickets(await api.syncTrackedTickets());
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-50">Suivi temps réel</h1>
          <span
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-emerald-400 shadow-[0_0_6px_1px_rgba(52,211,153,0.6)]" : "bg-slate-700"
            }`}
            title={connected ? "Flux temps réel connecté" : "Flux temps réel déconnecté"}
          />
        </div>
        <button
          type="button"
          onClick={handleSyncNow}
          disabled={syncing}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-50"
        >
          {syncing ? "Synchronisation…" : "Synchroniser maintenant"}
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Statuts des tickets Jira/GitLab créés, rafraîchis automatiquement toutes les ~15s.
      </p>

      {tickets.length === 0 ? (
        <p className="text-sm text-slate-600">Aucun ticket créé pour l'instant.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-900/60">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-900 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2">Session</th>
                <th className="px-4 py-2">Ticket</th>
                <th className="px-4 py-2">Provider</th>
                <th className="px-4 py-2">Statut</th>
                <th className="px-4 py-2">Dernière sync</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {tickets.map((ticket) => (
                <tr key={`${ticket.provider}-${ticket.external_id}`}>
                  <td className="max-w-[220px] truncate px-4 py-2 text-slate-400">{ticket.session_title}</td>
                  <td className="px-4 py-2 text-slate-100">
                    {ticket.key} — {ticket.title}
                  </td>
                  <td className="px-4 py-2 text-slate-400">{PROVIDER_LABEL[ticket.provider] ?? ticket.provider}</td>
                  <td className="px-4 py-2">
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs ${
                        STATUS_BADGE[ticket.status] ?? "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {ticket.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-slate-600">{formatTime(ticket.last_synced_at)}</td>
                  <td className="px-4 py-2 text-right">
                    <a href={ticket.url} target="_blank" rel="noreferrer" className="text-cyan-400 hover:text-cyan-300">
                      Ouvrir →
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
