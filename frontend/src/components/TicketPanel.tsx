import type { SessionResponse } from "../types";

const STATUS_BADGE: Record<string, string> = {
  "To Do": "bg-slate-800 text-slate-400",
  "In Progress": "bg-amber-500/10 text-amber-300",
  Done: "bg-emerald-500/10 text-emerald-300",
};

interface TicketPanelProps {
  session: SessionResponse;
  busy: boolean;
  onCreateTickets: () => void;
  onRefreshTickets: () => void;
}

/**
 * Module 5 — affiche les tickets Jira créés pour la session (ou
 * propose de les créer) ; module 6 — bouton de rafraîchissement de statut.
 */
export function TicketPanel({ session, busy, onCreateTickets, onRefreshTickets }: TicketPanelProps) {
  const hasTickets = session.tickets.length > 0;

  return (
    <section className="space-y-4 rounded-lg border border-slate-800 bg-slate-900/60 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-50">Tickets Jira</h2>
        {hasTickets && (
          <button
            type="button"
            onClick={onRefreshTickets}
            disabled={busy}
            className="text-xs font-medium text-cyan-400 hover:text-cyan-300 disabled:opacity-50"
          >
            Rafraîchir les statuts
          </button>
        )}
      </div>

      {session.status === "ticket_creation_failed" && (
        <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          Échec de la création des tickets : {session.error_message}
        </div>
      )}

      {!hasTickets && (
        <button
          type="button"
          onClick={onCreateTickets}
          disabled={busy}
          className="rounded-md bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 shadow-glow-sm transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
        >
          {busy ? "Création en cours…" : "Créer les tickets Jira"}
        </button>
      )}

      {hasTickets && (
        <ul className="space-y-2">
          {session.tickets.map((ticket) => (
            <li
              key={ticket.external_id}
              className="flex items-center justify-between rounded-md border border-slate-800 bg-slate-900/40 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-100">{ticket.title}</p>
                <p className="text-xs text-slate-500">
                  {ticket.key}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span
                  className={`rounded-full px-2.5 py-1 text-xs ${
                    STATUS_BADGE[ticket.status] ?? "bg-slate-800 text-slate-400"
                  }`}
                >
                  {ticket.status}
                </span>
                <a
                  href={ticket.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
                >
                  Ouvrir →
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}

      {session.status === "tickets_created" && session.error_message && (
        <p className="text-xs text-amber-300">
          Certains tickets n'ont pas pu être créés : {session.error_message}
        </p>
      )}
    </section>
  );
}
