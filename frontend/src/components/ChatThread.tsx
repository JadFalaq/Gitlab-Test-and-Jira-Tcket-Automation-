import { useEffect, useRef, useState } from "react";
import type { SessionResponse } from "../types";

interface ChatThreadProps {
  session: SessionResponse;
  busy: boolean;
  onSendMessage: (message: string) => void;
  onFinalize: () => void;
}

/**
 * Module 9 — phase de clarification conversationnelle libre, avant tout
 * appel au Requirement Analyzer Agent (module 2). Historique complet
 * toujours affiché (jamais tronqué au dernier message) ; le bouton "Générer
 * l'analyse" est une action explicite de l'ingénieur, jamais automatique —
 * cf. retour utilisateur du 22/07/2026.
 */
export function ChatThread({ session, busy, onSendMessage, onFinalize }: ChatThreadProps) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [session.chat_history.length, busy]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft.trim() || busy) return;
    onSendMessage(draft.trim());
    setDraft("");
  };

  const hasDiscussed = session.chat_history.length > 1;

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
      <div className="max-h-[440px] min-h-[220px] space-y-4 overflow-y-auto p-5">
        {session.chat_history.map((message, index) => (
          <div key={index} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                message.role === "user"
                  ? "bg-cyan-500 text-slate-950"
                  : "border border-slate-800 bg-slate-900 text-slate-200"
              }`}
            >
              {message.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="flex items-center gap-1.5 rounded-2xl border border-slate-800 bg-slate-900 px-4 py-3">
              <span className="h-1.5 w-1.5 animate-pulse-glow rounded-full bg-slate-500" />
              <span
                className="h-1.5 w-1.5 animate-pulse-glow rounded-full bg-slate-500"
                style={{ animationDelay: "0.2s" }}
              />
              <span
                className="h-1.5 w-1.5 animate-pulse-glow rounded-full bg-slate-500"
                style={{ animationDelay: "0.4s" }}
              />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-slate-800 p-3">
        <label htmlFor="chat-draft" className="sr-only">
          Répondre dans la conversation
        </label>
        <input
          id="chat-draft"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Réponds, précise, ou pose une question…"
          disabled={busy}
          className="flex-1 rounded-full border border-slate-800 bg-slate-950 px-4 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none focus:ring-1 focus:ring-cyan-500/40 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="shrink-0 rounded-full bg-slate-800 p-2.5 text-slate-300 transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Envoyer"
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
            <path
              d="M3 10h13M11 4l6 6-6 6"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </form>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 bg-slate-950/40 px-4 py-3">
        <p className="text-xs text-slate-500">
          {hasDiscussed
            ? "Prêt(e) ? Génère l'analyse structurée quand tu veux."
            : "Discute avec l'assistant pour préciser ta demande, ou génère directement l'analyse."}
        </p>
        <button
          type="button"
          onClick={onFinalize}
          disabled={busy}
          className="shrink-0 rounded-md bg-cyan-500 px-4 py-2 text-xs font-medium text-slate-950 shadow-glow-sm transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
        >
          Générer l'analyse →
        </button>
      </div>
    </div>
  );
}
