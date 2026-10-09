# Module 5 — Intégration Jira

## Rôle

Transforme une session validée (module 4 : `TestDesignOutput`) en tickets Jira Cloud, un ticket par user story.

GitLab a été retiré (plan d'action, semaine 1) : le projet ne cible plus que Jira.

## Architecture

```
Backend
  app/schemas/ticket.py          — TicketDraft (pivot), CreatedTicket, TicketCreationResult, TrackedTicket
  app/integrations/base.py       — BaseTicketProvider (ABC), TicketProviderError, TicketNotFoundError,
                                   TicketProviderNotConfiguredError
  app/integrations/jira_provider.py — Jira Cloud REST API v3 (issue réelle, Basic Auth)
  app/integrations/factory.py    — get_ticket_provider() : JiraProvider, ou erreur si non configuré
  app/services/ticket_service.py — construit les drafts, crée les tickets, rafraîchit leur statut
  app/services/session_service.py — create_tickets() / refresh_tickets()
  app/routers/tickets.py         — POST /api/sessions/{id}/tickets/create, /tickets/refresh

Tests
  tests/fakes/tickets.py         — FakeJiraProvider (en mémoire), branché par tests/conftest.py

Frontend
  src/components/TicketPanel.tsx — bouton de création + liste des tickets créés (lien + statut)
```

## Machine à états (extension du module 4)

```
validated --create_tickets--> tickets_created
        |--create_tickets (échec total)--> ticket_creation_failed (retry via create_tickets)
```

## Pas de faux tickets

`get_ticket_provider()` retourne le `JiraProvider` si `JIRA_BASE_URL`, `JIRA_API_TOKEN` et `JIRA_USER_EMAIL` sont renseignés. Sinon il lève `TicketProviderNotConfiguredError` : la session passe en `ticket_creation_failed` avec le message « Jira n'est pas configuré », et rien n'est créé.

L'ancienne version basculait silencieusement vers un `MockTicketProvider` : l'interface affichait « ticket créé » alors qu'aucun ticket n'existait dans Jira. Ce provider n'existe plus dans l'application ; son équivalent de test est `tests/fakes/tickets.py`.

En `APP_ENV=prod`, l'application refuse de démarrer si Jira n'est pas configuré (cf. `app/config.py`).

## Configuration

Utiliser un **compte de service** Jira dédié, avec des droits limités au projet cible :

| Variable | Exemple |
|---|---|
| `JIRA_BASE_URL` | `https://votre-site.atlassian.net` |
| `JIRA_USER_EMAIL` | email du compte de service |
| `JIRA_API_TOKEN` | token créé sur https://id.atlassian.com/manage-profile/security/api-tokens |
| `JIRA_PROJECT_KEY` | clé du projet (ex. `PROTO`) |

## Endpoints

| Méthode | Route | Action |
|---|---|---|
| `POST` | `/api/sessions/{id}/tickets/create` | Crée un ticket Jira par user story |
| `POST` | `/api/sessions/{id}/tickets/refresh` | Rafraîchit le statut des tickets déjà créés pour cette session |

## Contenu d'un ticket

| Champ Jira | Valeur |
|---|---|
| `summary` | titre de la user story |
| `description` (ADF) | « En tant que… je veux… afin que… » + critères d'acceptation + scénarios Gherkin de la session |
| `issuetype` | `Story` |
| `labels` | `priority::<priorité>` |
| sprint | sprint actif du projet s'il existe (best-effort) |

## Choix techniques et simplifications connues

1. **Un ticket par user story** — la description embarque les critères d'acceptation de la story + le rendu Gherkin de **toutes** les features de la session (le schéma module 3 ne relie pas explicitement `UserStory` et `GherkinFeature` par ID). À revoir si un mapping story↔scénario plus fin est nécessaire.
2. **Best-effort par story** — l'échec d'une story n'empêche pas la création des suivantes. `ticket_creation_failed` n'est atteint que si **aucun** ticket n'a pu être créé ; les erreurs partielles restent visibles dans `error_message` même en cas de succès (`tickets_created`).
3. **Description en ADF** — l'API Jira v3 refuse le texte brut pour `description`, conversion minimale en Atlassian Document Format (`_text_to_adf`), un paragraphe par bloc séparé par une ligne vide.
4. **Contrat d'erreurs typées sur tous les chemins** — `create_ticket` et `get_ticket_status` convertissent les erreurs HTTP (401/403/500) en `TicketProviderError` : `refresh_ticket_statuses` ne propage jamais d'exception, un 401 Jira ne produit pas de 500 sur `POST /api/tracking/sync`.

## Migration des données existantes

`scripts/migrate_jira_only.py` retire des sessions les tickets GitLab et les faux tickets de l'ancien mock (URL `https://example-mock.invalid/…`). Une session qui n'a plus de ticket repasse en `validated`. Aperçu par défaut, `--apply` pour appliquer.

## Lancer les tests

```bash
cd backend
pytest tests/test_ticket_providers.py tests/test_ticket_service.py tests/test_session_service_tickets.py -v
```

## Points d'incertitude

1. **Mapping story ↔ scénario Gherkin** — cf. simplification #1 ci-dessus.
2. **Pas de retry automatique par ticket** — un échec de création laisse la story sans ticket ; seul un nouvel appel `create_tickets` (retry manuel) retente.
3. **Jamais testé en CI contre une vraie instance Jira** — à ajouter (test `@integration`, semaine 5 du plan d'action).
