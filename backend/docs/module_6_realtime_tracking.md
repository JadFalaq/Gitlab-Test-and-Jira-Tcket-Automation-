# Module 6 — Suivi temps réel

## Rôle

Donne une vue live du statut des tickets créés (module 5), tous providers et toutes sessions confondus, sans que l'ingénieur ait à recharger la page ou à rouvrir chaque session.

## Architecture

```
Backend
  app/services/tracking_service.py — list_tracked_tickets() (cache DB), sync_all_tickets() (appel providers)
  app/routers/tracking.py          — GET /api/tracking/tickets, POST /api/tracking/sync, GET /api/tracking/stream (SSE)
  app/config.py                    — TRACKING_SYNC_INTERVAL_SECONDS (défaut 15s)

Frontend
  src/components/TrackingDashboard.tsx — tableau live, connecté au flux SSE
```

## Trois façons de consommer le suivi

| Endpoint | Coût | Usage |
|---|---|---|
| `GET /api/tracking/tickets` | Lecture DB seule, pas d'appel réseau externe | Snapshot rapide, chargement initial |
| `POST /api/tracking/sync` | Appelle `get_ticket_status` sur chaque provider concerné | Rafraîchissement à la demande ("Synchroniser maintenant") |
| `GET /api/tracking/stream` | SSE, un snapshot synchronisé toutes les `TRACKING_SYNC_INTERVAL_SECONDS` | Vue "temps réel" par défaut du frontend |

## Choix techniques

1. **Server-Sent Events, pas de WebSocket** — un flux unidirectionnel (serveur → client) suffit à ce besoin (pas d'interaction bidirectionnelle nécessaire). SSE s'implémente avec un simple générateur async + `StreamingResponse` de FastAPI, sans dépendance supplémentaire (pas de `sse-starlette`, pas de `websockets`).
2. **Chaque tick du flux ouvre sa propre session DB** (`AsyncSessionLocal`, pas de `Depends(get_db)`) — le flux vit plus longtemps qu'une requête HTTP classique, une session DB par requête (pattern du module 4) ne conviendrait pas.
3. **Best-effort** — si `sync_all_tickets` échoue (ex : Jira temporairement injoignable), le flux retombe sur `list_tracked_tickets` (dernier snapshot en cache) plutôt que d'interrompre la connexion SSE.
4. **`MockTicketProvider` simule une progression réaliste** (`To Do` → `In Progress` après 20s → `Done` après 60s, cf. module 5) — pour que le tableau de suivi ait quelque chose à montrer en démo sans compte Jira/GitLab réel connecté.
5. **Frontend : fetch initial + SSE, pas de polling** — `TrackingDashboard.tsx` charge un premier snapshot via `GET /api/tracking/tickets` (affichage immédiat), puis bascule sur les mises à jour poussées par le flux SSE. Un indicateur (point vert/gris) reflète l'état de la connexion.

## Points d'incertitude

1. **Reconnexion SSE** — le comportement de reconnexion automatique du navigateur (`EventSource`) est utilisé tel quel, sans backoff custom ni limite de tentatives.
2. **Un seul flux global** — pas de filtrage par session ou par provider côté SSE ; le frontend reçoit toujours l'intégralité des tickets suivis. Acceptable au volume prototype, à revoir si le nombre de tickets grandit significativement.
3. **`TRACKING_SYNC_INTERVAL_SECONDS=15` par défaut** — valeur arbitraire, à ajuster selon les limites de rate-limiting réelles de Jira/GitLab si utilisé avec de vrais comptes.
