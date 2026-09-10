import { useEffect, useRef, useState } from "react";
import { api, UnauthorizedError } from "./api";
import { AnalyticsDashboard } from "./components/AnalyticsDashboard";
import { AttachmentPreviewModal } from "./components/AttachmentPreviewModal";
import { ChatThread } from "./components/ChatThread";
import { GherkinFeatureView } from "./components/GherkinFeatureView";
import { LoginScreen } from "./components/LoginScreen";
import { RequirementPanel } from "./components/RequirementPanel";
import { SessionHistory } from "./components/SessionHistory";
import { TestDesignPanel } from "./components/TestDesignPanel";
import { TicketPanel } from "./components/TicketPanel";
import { TrackingDashboard } from "./components/TrackingDashboard";
import { renderPdfThumbnail } from "./pdfThumbnail";
import type { SessionResponse, SessionSummary, UserResponse } from "./types";

type Tab = "sessions" | "tracking" | "analytics";

const TAB_ICONS: Record<Tab, React.ReactNode> = {
  sessions: (
    <path
      d="M4 5.5h12M4 10h12M4 14.5h8"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  ),
  tracking: (
    <path
      d="M4 15V9m4 6V5m4 10v-4m4 4V7"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
  analytics: (
    <path
      d="M3 16.5 7.5 11l3 3L16.5 6M16.5 6h-4M16.5 6v4"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
};

const TABS: { id: Tab; label: string }[] = [
  { id: "sessions", label: "Sessions" },
  { id: "tracking", label: "Suivi temps réel" },
  { id: "analytics", label: "Analytics" },
];

// Modules 2/3 tournent en tâche de fond côté backend (cf.
// backend/app/services/session_service.py) : une action peut renvoyer un
// statut "en cours" qu'il faut re-interroger jusqu'à en sortir.
const isProcessing = (status: SessionResponse["status"]) =>
  status === "analyzing" || status === "designing_tests";

const POLL_INTERVAL_MS = 2000;
// ~5 min, aligné sur OLLAMA_TIMEOUT_SECONDS par défaut (backend/.env) : au-delà,
// le backend a de toute façon abandonné l'appel LLM et mis la session en échec.
const POLL_MAX_ATTEMPTS = 150;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function pollUntilSettled(sessionId: string): Promise<SessionResponse> {
  for (let attempt = 0; attempt < POLL_MAX_ATTEMPTS; attempt += 1) {
    await sleep(POLL_INTERVAL_MS);
    const current = await api.getSession(sessionId);
    if (!isProcessing(current.status)) return current;
  }
  throw new Error(
    "Le traitement prend anormalement longtemps (LLM indisponible ?). Réessaie plus tard."
  );
}

// Types acceptés par l'input fichier — cf. backend/app/services/attachment_service.py
// (documents : extraction de texte ; images : description via modèle vision).
const ATTACHMENT_ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,.pdf,.docx,.xlsx,.pptx,.txt,.md";

function extensionBadge(filename: string): string {
  const ext = filename.split(".").pop()?.toUpperCase() ?? "";
  return ext.length > 4 ? ext.slice(0, 4) : ext || "FILE";
}

// Distinction visuelle par type même sans miniature réelle (docx/xlsx/pptx
// n'ont pas de rendu natif navigateur, contrairement aux PDF/images) — pour
// un aperçu "au coup d'œil" plutôt qu'un badge gris uniforme.
const EXTENSION_BADGE_COLORS: Record<string, string> = {
  PDF: "bg-red-500/15 text-red-300",
  DOCX: "bg-blue-500/15 text-blue-300",
  XLSX: "bg-emerald-500/15 text-emerald-300",
  PPTX: "bg-orange-500/15 text-orange-300",
  TXT: "bg-slate-700 text-slate-300",
  MD: "bg-slate-700 text-slate-300",
};

function extensionBadgeClass(filename: string): string {
  return EXTENSION_BADGE_COLORS[extensionBadge(filename)] ?? "bg-slate-800 text-slate-400";
}

function isPdf(contentTypeOrFile: string): boolean {
  return contentTypeOrFile === "application/pdf";
}

interface PreviewTarget {
  filename: string;
  url: string;
  contentType: string;
}

function App() {
  // undefined = vérification en cours, null = non connecté, sinon connecté.
  const [currentUser, setCurrentUser] = useState<UserResponse | null | undefined>(undefined);
  const [authError, setAuthError] = useState<string | null>(null);

  const [tab, setTab] = useState<Tab>("sessions");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [userInput, setUserInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [filePreviews, setFilePreviews] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const [pdfThumbnails, setPdfThumbnails] = useState<Record<number, string>>({});
  const [attachmentPdfThumbnails, setAttachmentPdfThumbnails] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Module 8 : vérifie la session au chargement — /api/auth/callback peut
  // rediriger ici avec ?auth_error=... si le login SSO a échoué.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const errorParam = params.get("auth_error");
    if (errorParam) {
      setAuthError(errorParam);
      window.history.replaceState({}, "", window.location.pathname);
    }
    api
      .me()
      .then(setCurrentUser)
      .catch(() => setCurrentUser(null));
  }, []);

  const handleLogout = async () => {
    await api.logout().catch(() => {});
    setCurrentUser(null);
    setSession(null);
    setSessions([]);
  };

  // Object URLs locaux (avant envoi) pour CHAQUE fichier, pas seulement les
  // images : permet à la fois la miniature ronde (images) et l'aperçu en
  // grand dans la modale (images ET PDF, rendus nativement par le
  // navigateur — cf. AttachmentPreviewModal). Révoqués à chaque changement
  // pour ne pas fuiter des object URLs.
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setFilePreviews(urls);
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [files]);

  // Vraies miniatures (page 1 rendue en image) pour les PDF joints, pour un
  // aperçu "au coup d'œil" sans avoir à ouvrir la modale — cf. pdfThumbnail.ts.
  // Best-effort : un PDF corrompu/non standard retombe simplement sur le
  // badge d'extension coloré, jamais d'erreur visible pour l'utilisateur.
  useEffect(() => {
    let cancelled = false;
    setPdfThumbnails({});
    files.forEach((file, index) => {
      if (!isPdf(file.type)) return;
      renderPdfThumbnail(file)
        .then((dataUrl) => {
          if (!cancelled) setPdfThumbnails((prev) => ({ ...prev, [index]: dataUrl }));
        })
        .catch(() => {
          /* pas de miniature : le badge d'extension prend le relais */
        });
    });
    return () => {
      cancelled = true;
    };
  }, [files]);

  // Même principe pour les pièces jointes déjà envoyées (session en cours) —
  // rendu depuis l'URL servie par le backend plutôt que depuis un File local.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    session.attachments.forEach((attachment) => {
      if (!isPdf(attachment.content_type)) return;
      renderPdfThumbnail(api.attachmentFileUrl(session.id, attachment.id))
        .then((dataUrl) => {
          if (!cancelled) setAttachmentPdfThumbnails((prev) => ({ ...prev, [attachment.id]: dataUrl }));
        })
        .catch(() => {
          /* pas de miniature : le badge d'extension prend le relais */
        });
    });
    return () => {
      cancelled = true;
    };
  }, [session?.id, session?.attachments]);

  const refreshHistory = async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      // L'historique n'est pas critique pour le flux principal : on ignore.
    }
  };

  useEffect(() => {
    if (currentUser) refreshHistory();
  }, [currentUser]);

  const runAction = async (action: () => Promise<SessionResponse>) => {
    setBusy(true);
    setError(null);
    try {
      let result = await action();
      setSession(result); // affiche immédiatement "analyzing"/"designing_tests" si applicable
      if (isProcessing(result.status)) {
        result = await pollUntilSettled(result.id);
        setSession(result);
      }
      await refreshHistory();
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setCurrentUser(null); // cookie expiré/révoqué en cours d'usage -> écran de login
        return;
      }
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    } finally {
      setBusy(false);
    }
  };

  const handleCreateSession = (event: React.FormEvent) => {
    event.preventDefault();
    if (!userInput.trim()) return;
    runAction(() => api.createSession(userInput.trim(), undefined, files));
  };

  // Module 9 : envoi de message synchrone (pas de tâche de fond côté
  // backend pour /chat, cf. session_service.send_chat_message) — pas besoin
  // de pollUntilSettled ici, contrairement à runAction.
  const handleSendChatMessage = async (message: string) => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await api.sendChatMessage(session.id, message));
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setCurrentUser(null);
        return;
      }
      setError(err instanceof Error ? err.message : "Erreur inconnue");
    } finally {
      setBusy(false);
    }
  };

  const handleFinalizeChat = () => {
    if (!session) return;
    runAction(() => api.finalizeChat(session.id));
  };

  const handleFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []);
    setFiles((prev) => [...prev, ...picked]);
    event.target.value = ""; // permet de rajouter le même fichier après suppression
  };

  const handleRemoveFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSelectSession = (id: string) => {
    runAction(() => api.getSession(id));
  };

  const handleNewSession = () => {
    setSession(null);
    setUserInput("");
    setFiles([]);
    setError(null);
  };

  if (currentUser === undefined) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-500">
        Chargement…
      </div>
    );
  }

  if (currentUser === null) {
    return <LoginScreen error={authError} onDevLogin={setCurrentUser} />;
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {preview && (
        <AttachmentPreviewModal
          filename={preview.filename}
          url={preview.url}
          contentType={preview.contentType}
          onClose={() => setPreview(null)}
        />
      )}

      <div className="mx-auto max-w-7xl px-6 py-6 lg:px-10 lg:py-8">
        <nav className="mb-8 flex items-center justify-between gap-1 border-b border-slate-800/80">
          <div className="flex items-center gap-6">
            <span className="h-2 w-2 rounded-full bg-cyan-400 shadow-[0_0_8px_2px_rgba(34,211,238,0.7)]" />
            <div className="flex gap-1">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-3 text-sm font-medium transition-colors ${
                    tab === t.id
                      ? "border-cyan-400 text-cyan-300"
                      : "border-transparent text-slate-500 hover:text-slate-300"
                  }`}
                >
                  <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
                    {TAB_ICONS[t.id]}
                  </svg>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-3 pb-3 text-xs text-slate-500">
            <span className="hidden text-slate-400 sm:inline">{currentUser.display_name}</span>
            <button type="button" onClick={handleLogout} className="font-medium text-cyan-400 hover:text-cyan-300">
              Déconnexion
            </button>
          </div>
        </nav>

        {tab === "tracking" && <TrackingDashboard />}
        {tab === "analytics" && <AnalyticsDashboard />}

        {tab === "sessions" && (
          <div className="flex flex-col gap-6 lg:flex-row lg:gap-8">
            <aside className="shrink-0 space-y-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4 lg:w-72">
              <div className="flex items-center justify-between">
                <h1 className="text-sm font-semibold text-slate-200">Sessions</h1>
                <button
                  type="button"
                  onClick={handleNewSession}
                  className="rounded-md border border-slate-700 px-2 py-1 text-xs font-medium text-cyan-300 transition-colors hover:bg-slate-800"
                >
                  + Nouvelle
                </button>
              </div>
              <SessionHistory sessions={sessions} activeId={session?.id ?? null} onSelect={handleSelectSession} />
            </aside>

            <main className="flex-1 space-y-8">
              <header className="relative overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40 p-6">
                <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-cyan-500/10 blur-[90px]" />
                <p className="relative text-xs font-medium uppercase tracking-wider text-cyan-400">
                  Agent IA · Tests &amp; Tickets
                </p>
                <h1 className="relative mt-1 text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">
                  Transforme une idée en scénarios de test
                </h1>
                <p className="relative mt-2 max-w-xl text-sm text-slate-400">
                  Prototype — toutes les données sont fictives. Décris un besoin, une idée ou un bug ci-dessous ;
                  chaque étape (analyse, tests, tickets) reste soumise à ta validation.
                </p>
              </header>

              {!session && (
                <form
                  onSubmit={handleCreateSession}
                  className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/40 p-6"
                >
                  <label htmlFor="user-input" className="sr-only">
                    Décris ton besoin, une idée ou un bug
                  </label>
                  <textarea
                    id="user-input"
                    value={userInput}
                    onChange={(e) => setUserInput(e.target.value)}
                    placeholder="Ex : En tant qu'utilisateur, je veux payer par QR code dans l'app [FICTIF] ShopEasy…"
                    rows={6}
                    className="w-full rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-cyan-500/60 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
                  />

                  <div className="flex flex-wrap items-center gap-2.5">
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      accept={ATTACHMENT_ACCEPT}
                      onChange={handleFilesSelected}
                      className="hidden"
                    />
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="flex items-center gap-1.5 rounded-full border border-slate-700 px-3.5 py-2 text-xs font-medium text-slate-300 transition-colors hover:border-cyan-500/50 hover:text-cyan-300"
                    >
                      <svg viewBox="0 0 20 20" fill="none" className="h-3.5 w-3.5" aria-hidden="true">
                        <path
                          d="M13.5 6.5 7.9 12.1a2.5 2.5 0 1 0 3.54 3.54l6.36-6.36a4.5 4.5 0 0 0-6.36-6.37L4.4 9.94a6.5 6.5 0 0 0 9.19 9.19"
                          stroke="currentColor"
                          strokeWidth="1.4"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                      Joindre une photo ou un fichier
                    </button>
                    {files.map((file, index) => (
                      <span
                        key={`${file.name}-${index}`}
                        className="group flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 py-1 pl-1.5 pr-2 text-xs text-slate-300 transition-colors hover:border-cyan-500/40"
                      >
                        <button
                          type="button"
                          onClick={() =>
                            setPreview({ filename: file.name, url: filePreviews[index], contentType: file.type })
                          }
                          className="flex items-center gap-1.5"
                          title={`Prévisualiser ${file.name}`}
                        >
                          {file.type.startsWith("image/") ? (
                            <img
                              src={filePreviews[index]}
                              alt=""
                              className="h-7 w-7 rounded-md object-cover"
                            />
                          ) : pdfThumbnails[index] ? (
                            <img
                              src={pdfThumbnails[index]}
                              alt=""
                              className="h-7 w-7 rounded-md border border-slate-700 object-cover object-top"
                            />
                          ) : (
                            <span
                              className={`flex h-7 w-9 items-center justify-center rounded-md text-[9px] font-semibold ${extensionBadgeClass(file.name)}`}
                              aria-hidden="true"
                            >
                              {extensionBadge(file.name)}
                            </span>
                          )}
                          <span className="max-w-[140px] truncate">{file.name}</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => handleRemoveFile(index)}
                          aria-label={`Retirer ${file.name}`}
                          className="rounded-full px-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                  <p className="text-xs text-slate-600">
                    Images, PDF, Word, Excel, PowerPoint, texte/markdown — utilisés pour enrichir l'analyse. Clique
                    sur un fichier joint pour le prévisualiser.
                  </p>

                  <button
                    type="submit"
                    disabled={busy || !userInput.trim()}
                    className="rounded-md bg-cyan-500 px-5 py-2.5 text-sm font-medium text-slate-950 shadow-glow-sm transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
                  >
                    {busy ? "Démarrage…" : "Démarrer la discussion"}
                  </button>
                </form>
              )}

              {error && (
                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
                  {error}
                </div>
              )}

              {session && (
                <div className="space-y-6">
                  {/* Pendant 'chatting', le premier message est déjà la première bulle du
                      ChatThread ci-dessous — éviter de le montrer en double. */}
                  {session.status !== "chatting" && (
                    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400">
                      <span className="font-medium text-slate-300">Demande initiale :</span> {session.raw_input}
                    </div>
                  )}

                  {session.attachments.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-medium uppercase tracking-wide text-slate-600">
                        Pièces jointes ({session.attachments.length})
                      </p>
                      <ul className="grid gap-2 sm:grid-cols-2">
                        {session.attachments.map((attachment) => {
                          const url = api.attachmentFileUrl(session.id, attachment.id);
                          return (
                            <li
                              key={attachment.id}
                              className="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-sm transition-colors hover:border-cyan-500/40"
                            >
                              <button
                                type="button"
                                onClick={() =>
                                  setPreview({ filename: attachment.filename, url, contentType: attachment.content_type })
                                }
                                className="flex w-full items-center gap-2.5 text-left font-medium text-slate-200"
                                title={`Prévisualiser ${attachment.filename}`}
                              >
                                {attachment.kind === "image" ? (
                                  <img src={url} alt="" className="h-10 w-10 shrink-0 rounded-md object-cover" />
                                ) : attachmentPdfThumbnails[attachment.id] ? (
                                  <img
                                    src={attachmentPdfThumbnails[attachment.id]}
                                    alt=""
                                    className="h-10 w-10 shrink-0 rounded-md border border-slate-700 object-cover object-top"
                                  />
                                ) : (
                                  <span
                                    className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-[10px] font-semibold ${extensionBadgeClass(attachment.filename)}`}
                                    aria-hidden="true"
                                  >
                                    {extensionBadge(attachment.filename)}
                                  </span>
                                )}
                                <span className="min-w-0 truncate">{attachment.filename}</span>
                              </button>
                              {attachment.extraction_error && (
                                <p className="mt-1.5 text-xs text-red-400">
                                  Non exploitée : {attachment.extraction_error}
                                </p>
                              )}
                              {attachment.extracted_text && (
                                <p className="mt-1.5 line-clamp-3 text-xs text-slate-500">
                                  {attachment.extracted_text}
                                </p>
                              )}
                              {/* Vérifications strictes sur `null` : "" (traitée, mais aucun texte
                                  exploitable, ex: PDF scanné sans couche texte) n'est pas "en cours". */}
                              {attachment.extraction_error === null &&
                                attachment.extracted_text === "" && (
                                  <p className="mt-1.5 text-xs text-slate-600">
                                    Aucun texte exploitable trouvé dans ce fichier.
                                  </p>
                                )}
                              {attachment.extraction_error === null &&
                                attachment.extracted_text === null && (
                                  <p className="mt-1.5 text-xs text-slate-600">En cours de traitement…</p>
                                )}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}

                  {session.status === "chatting" && (
                    <ChatThread
                      session={session}
                      busy={busy}
                      onSendMessage={handleSendChatMessage}
                      onFinalize={handleFinalizeChat}
                    />
                  )}

                  {isProcessing(session.status) && (
                    <div
                      className="flex items-center gap-2.5 rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-4 text-sm text-cyan-200"
                      role="status"
                      aria-live="polite"
                    >
                      <span className="h-2 w-2 animate-pulse-glow rounded-full bg-cyan-400" aria-hidden="true" />
                      {session.status === "analyzing"
                        ? "Analyse de la demande en cours…"
                        : "Génération des scénarios de test en cours…"}{" "}
                      Cela peut prendre jusqu'à quelques minutes avec un modèle local.
                    </div>
                  )}

                  {session.status === "pending_requirement_review" && session.requirement_analysis && (
                    <RequirementPanel
                      analysis={session.requirement_analysis}
                      busy={busy}
                      onAccept={() => runAction(() => api.acceptRequirement(session.id))}
                      onReject={() => runAction(() => api.rejectRequirement(session.id))}
                      onRegenerate={(guidance) =>
                        runAction(() => api.regenerateRequirement(session.id, guidance))
                      }
                    />
                  )}

                  {session.status === "requirement_failed" && (
                    <div className="space-y-3 rounded-lg border border-red-500/30 bg-red-500/10 p-4">
                      <p className="text-sm text-red-300">
                        L'analyse de la demande a échoué : {session.error_message}
                      </p>
                      <button
                        type="button"
                        onClick={() => runAction(() => api.regenerateRequirement(session.id))}
                        disabled={busy}
                        className="rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800 disabled:opacity-50"
                      >
                        Réessayer l'analyse
                      </button>
                    </div>
                  )}

                  {session.status === "pending_test_review" && session.test_design_output && (
                    <>
                      {session.requirement_analysis && (
                        <details className="rounded-lg border border-slate-800 p-3">
                          <summary className="cursor-pointer text-sm font-medium text-slate-400">
                            Analyse validée : {session.requirement_analysis.title}
                          </summary>
                        </details>
                      )}
                      <TestDesignPanel
                        output={session.test_design_output}
                        busy={busy}
                        onAccept={() => runAction(() => api.acceptTests(session.id))}
                        onReject={() => runAction(() => api.rejectTests(session.id))}
                        onRegenerate={(guidance) =>
                          runAction(() => api.regenerateTests(session.id, guidance))
                        }
                      />
                    </>
                  )}

                  {session.status === "test_design_failed" && (
                    <div className="space-y-3 rounded-lg border border-red-500/30 bg-red-500/10 p-4">
                      <p className="text-sm text-red-300">
                        La génération des tests a échoué : {session.error_message}
                      </p>
                      <button
                        type="button"
                        onClick={() => runAction(() => api.regenerateTests(session.id))}
                        disabled={busy}
                        className="rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800 disabled:opacity-50"
                      >
                        Réessayer la génération des tests
                      </button>
                    </div>
                  )}

                  {(session.status === "validated" ||
                    session.status === "tickets_created" ||
                    session.status === "ticket_creation_failed") &&
                    session.test_design_output && (
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-300">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
                          Session validée.
                        </div>
                        <TicketPanel
                          session={session}
                          busy={busy}
                          onCreateTickets={() => runAction(() => api.createTickets(session.id))}
                          onRefreshTickets={() => runAction(() => api.refreshSessionTickets(session.id))}
                        />
                        <div className="space-y-3">
                          {session.test_design_output.gherkin_features.map((feature) => (
                            <GherkinFeatureView key={feature.feature_name} feature={feature} />
                          ))}
                        </div>
                      </div>
                    )}

                  {(session.status === "requirement_rejected" || session.status === "tests_rejected") && (
                    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-400">
                      Session rejetée. Crée une nouvelle session pour réessayer.
                    </div>
                  )}
                </div>
              )}
            </main>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
