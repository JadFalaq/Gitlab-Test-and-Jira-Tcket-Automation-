# Module 5 — Intégration Jira / GitLab

## Rôle

Transforme une session validée (module 4 : `TestDesignOutput`) en tickets créés chez un ou plusieurs providers externes (Jira Cloud, GitLab), avec un ticket par user story. Couche interchangeable réel/mock, sur le même principe que le module 1 (LLM Abstraction Layer).

## Architecture

```
Backend
  app/schemas/ticket.py          — TicketDraft (pivot), CreatedTicket, TicketCreationResult, TrackedTicket
  app/integrations/base.py       — BaseTicketProvider (ABC), TicketProviderError, TicketNotFoundError
  app/integrations/jira_provider.py    — Jira Cloud REST API v3 (issue réelle, Basic Auth)
  app/integrations/gitlab_provider.py  — GitLab REST API v4 (issue réelle, PRIVATE-TOKEN)
  app/integrations/mock_provider.py    — tickets simulés, statut évolue avec le temps (démo + tests)
  app/integrations/factory.py    — sélection auto réel/mock selon .env (JIRA_*/GITLAB_*)
  app/services/ticket_service.py — construit les drafts, crée les tickets, rafraîchit leur statut
  app/services/session_service.py — create_tickets() / refresh_tickets() (nouvelles transitions)
  app/routers/tickets.py         — POST /api/sessions/{id}/tickets/create, /tickets/refresh

Frontend
  src/components/TicketPanel.tsx — bouton de création + liste des tickets créés (lien + statut)
```

## Machine à états (extension du module 4)

```
validated --create_tickets--> tickets_created
        |--create_tickets (échec total)--> ticket_creation_failed (retry via create_tickets)
```

`tickets_created` n'est pas terminal : `create_tickets` reste appelable (ex : ajouter un provider configuré après coup, ou relancer sur un provider qui avait échoué).

## Sélection réel / mock

Comme annoncé dès `.env.example` ("laisser vide pour utiliser le mock") : `get_jira_provider()` / `get_gitlab_provider()` (`app/integrations/factory.py`) retournent le provider réel si sa configuration est complète (`JIRA_BASE_URL`+`JIRA_API_TOKEN`+`JIRA_USER_EMAIL`, ou `GITLAB_BASE_URL`+`GITLAB_PRIVATE_TOKEN`+`GITLAB_PROJECT_ID`), sinon `MockTicketProvider`. Décision relue à chaque appel (pas de cache), donc pilotable uniquement via `.env` — pas de bascule à chaud nécessaire côté API.

## Endpoints

| Méthode | Route | Action |
|---|---|---|
| `POST` | `/api/sessions/{id}/tickets/create` | Crée les tickets (corps optionnel `{"providers": ["jira"]}`, défaut = les deux) |
| `POST` | `/api/sessions/{id}/tickets/refresh` | Rafraîchit le statut des tickets déjà créés pour cette session |

## Choix techniques et simplifications connues

1. **Un ticket par user story** — pas de granularité plus fine. La description embarque les critères d'acceptation de la story + le rendu Gherkin de **toutes** les features de la session (le schéma module 3 ne relie pas explicitement `UserStory` et `GherkinFeature` par ID, et modifier ce mapping toucherait un module déjà "terminé et testé"). Limitation documentée, à revoir si un mapping story↔scénario plus fin est nécessaire.
2. **Best-effort par provider** — un échec sur Jira n'empêche pas la tentative sur GitLab. `ticket_creation_failed` n'est atteint que si **aucun** ticket n'a pu être créé ; les erreurs partielles restent visibles dans `error_message` même en cas de succès (`tickets_created`).
3. **Jira description en ADF** — l'API Jira v3 refuse le texte brut pour `description`, conversion minimale en Atlassian Document Format (`_text_to_adf`), un paragraphe par bloc séparé par une ligne vide.
4. **GitLab : pas de type d'issue natif** — encodé en label `type::story`/`type::bug`/`type::task` (convention GitLab standard), pas de champ dédié dans l'API v4.
5. **Pas de DI FastAPI pour les providers** (contrairement aux agents LLM du module 4) — le choix réel/mock dépend uniquement de `.env`, jamais besoin de le surcharger explicitement en test : l'absence de config Jira/GitLab dans l'environnement de test suffit à activer le mock automatiquement, déterministe et sans appel réseau.
6. **Contrat d'erreurs typées appliqué sur tous les chemins** — `get_ticket_status` (Jira et GitLab) convertit désormais aussi `httpx.HTTPStatusError` (401/403/500 provider) en `TicketProviderError`, comme `create_ticket` le faisait déjà : la promesse best-effort de `refresh_ticket_statuses` (« pas d'exception propagée ») tient donc sur tous les cas, un 401 provider ne produit plus de 500 sur `POST /api/tracking/sync`. `health_check` ne lève jamais (toute erreur httpx = `False`). Couvert par `tests/test_ticket_providers.py` (statuts succès/404/erreur HTTP pour les deux providers) et `tests/test_ticket_service.py` (refresh best-effort).

## Lancer les tests

```bash
cd backend
pytest tests/test_ticket_providers.py tests/test_ticket_service.py tests/test_session_service_tickets.py -v
```

## Points d'incertitude

1. **Mapping story ↔ scénario Gherkin** — cf. simplification #1 ci-dessus.
2. **Pas de retry automatique par ticket** — un échec de création laisse la story sans ticket ; seul un nouvel appel `create_tickets` (retry manuel) retente.
3. **`MockTicketProvider` garde son état en mémoire process** (dict `external_id -> created_at`) — suffisant en mono-instance, perdu au redémarrage du backend.
