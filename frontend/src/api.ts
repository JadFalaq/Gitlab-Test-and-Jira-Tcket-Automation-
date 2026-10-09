/**
 * Client HTTP pour l'API du backend (app/routers/sessions.py).
 * Un endpoint = une fonction. Pas de state management ici — les
 * composants appelants gèrent leur propre état React.
 */

import type {
  AnalyticsSummary,
  AuthConfig,
  SessionResponse,
  SessionSummary,
  TrackedTicket,
  UserResponse,
} from "./types";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class UnauthorizedError extends Error {}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  // FormData : ne jamais fixer Content-Type nous-mêmes, le navigateur doit
  // poser le boundary multipart/form-data lui-même (cf. createSession, qui
  // envoie du texte + des pièces jointes).
  const isFormData = options?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // Module 8 : le cookie de session (posé par /api/auth/callback) doit
    // partir sur CHAQUE appel, y compris cross-origin (5173 -> 8000).
    credentials: "include",
    ...(isFormData ? {} : { headers: { "Content-Type": "application/json" } }),
    ...options,
  });

  if (response.status === 401) {
    throw new UnauthorizedError("Non authentifié");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}) as { detail?: string });
    throw new Error(body.detail ?? `Erreur HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  // --- Module 8 : authentification SSO ---

  loginUrl: () => `${API_BASE_URL}/api/auth/login`,

  // Indique si un vrai SSO est configuré côté backend — pas de garde
  // d'authentification sur cette route (rien de sensible), cf. LoginScreen.tsx.
  authConfig: () => request<AuthConfig>("/api/auth/config"),

  me: () => request<UserResponse>("/api/auth/me"),

  // Connexion de secours sans SSO réel — 404 dès que AUTH_OIDC_ISSUER est
  // configuré côté backend (cf. app/routers/auth.py::dev_login).
  devLogin: (email: string, displayName?: string) =>
    request<UserResponse>("/api/auth/dev-login", {
      method: "POST",
      body: JSON.stringify({ email, display_name: displayName || undefined }),
    }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  createSession: (userInput: string, context?: string, files?: File[]) => {
    const form = new FormData();
    form.append("user_input", userInput);
    if (context) form.append("context", context);
    for (const file of files ?? []) form.append("files", file);
    return request<SessionResponse>("/api/sessions", { method: "POST", body: form });
  },

  listSessions: () => request<SessionSummary[]>("/api/sessions"),

  getSession: (id: string) => request<SessionResponse>(`/api/sessions/${id}`),

  // --- Module 9 : chat de clarification avant l'analyse structurée ---

  sendChatMessage: (id: string, message: string) =>
    request<SessionResponse>(`/api/sessions/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  finalizeChat: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/chat/finalize`, { method: "POST" }),

  attachmentFileUrl: (sessionId: string, attachmentId: string) =>
    `${API_BASE_URL}/api/sessions/${sessionId}/attachments/${attachmentId}/file`,

  acceptRequirement: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/requirement/accept`, { method: "POST" }),

  rejectRequirement: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/requirement/reject`, { method: "POST" }),

  // `guidance` (optionnel) : instruction libre de l'ingénieur pour CETTE
  // régénération (ex: "concentre-toi sur les cas d'erreur") — jamais
  // stockée sur la session elle-même, seulement injectée dans le prompt de
  // cet appel LLM (cf. backend/app/services/session_service.py).
  regenerateRequirement: (id: string, guidance?: string) =>
    request<SessionResponse>(`/api/sessions/${id}/requirement/regenerate`, {
      method: "POST",
      body: JSON.stringify(guidance ? { guidance } : {}),
    }),

  acceptTests: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/tests/accept`, { method: "POST" }),

  rejectTests: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/tests/reject`, { method: "POST" }),

  regenerateTests: (id: string, guidance?: string) =>
    request<SessionResponse>(`/api/sessions/${id}/tests/regenerate`, {
      method: "POST",
      body: JSON.stringify(guidance ? { guidance } : {}),
    }),

  // --- Module 5 : Jira ---

  createTickets: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/tickets/create`, { method: "POST" }),

  refreshSessionTickets: (id: string) =>
    request<SessionResponse>(`/api/sessions/${id}/tickets/refresh`, { method: "POST" }),

  // --- Module 6 : suivi temps réel ---

  listTrackedTickets: () => request<TrackedTicket[]>("/api/tracking/tickets"),

  syncTrackedTickets: () => request<TrackedTicket[]>("/api/tracking/sync", { method: "POST" }),

  trackingStreamUrl: () => `${API_BASE_URL}/api/tracking/stream`,

  // --- Module 7 : analytics ---

  getAnalyticsSummary: () => request<AnalyticsSummary>("/api/analytics/summary"),
};
