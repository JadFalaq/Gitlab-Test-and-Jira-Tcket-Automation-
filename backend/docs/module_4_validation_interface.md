# Module 4 — Interface de validation (minimale)

## Rôle

Fait vivre une demande d'ingénieur à travers le pipeline module 2 (analyse) → module 3 (conception de tests) → validation humaine, avec accept/reject/regenerate à chaque étape. Pas d'édition ciblée par champ dans cette version (conforme à l'ordre de dev du projet) — `regenerate` relance entièrement le module concerné.

Couvre backend (persistance + orchestration + API REST) et frontend (React + Tailwind v3 + Vite) qui la consomme.

## Architecture

```
Backend
  app/models/session.py        — table `sessions` (SQLAlchemy async)
  app/database.py               — engine + get_db() + init_db()
  app/schemas/session.py        — SessionCreateRequest, SessionResponse, SessionSummary
  app/services/session_service.py — machine à états (voir ci-dessous)
  app/routers/sessions.py       — 9 endpoints REST
  app/dependencies.py           — factories d'agents injectables (overridables en test)

Frontend (frontend/)
  src/api.ts                    — client HTTP (1 fonction par endpoint)
  src/types.ts                  — types miroir des schémas Pydantic
  src/App.tsx                   — orchestration : formulaire → panneaux de validation
  src/components/
    RequirementPanel.tsx        — affiche RequirementAnalysis + accept/reject/regenerate
    TestDesignPanel.tsx         — affiche TestDesignOutput + accept/reject/regenerate
    GherkinFeatureView.tsx      — rendu texte Gherkin côté client (présentationnel)
    SessionHistory.tsx          — liste des sessions (GET /api/sessions)
    ReviewActions.tsx           — barre de boutons accept/reject/regenerate
```

## Machine à états (`SessionModel.status`)

```
(create) --> chatting --[finalize_chat]--> analyzing --[analyse OK]--> pending_requirement_review
                                                  |--[analyse échoue]--> requirement_failed (retry via regenerate -> analyzing)

pending_requirement_review --accept--> designing_tests --[design OK]--> pending_test_review
        |--reject--> requirement_rejected      |--[design échoue]--> test_design_failed (retry via regenerate -> designing_tests)

pending_test_review --accept--> validated
        |--reject--> tests_rejected
```

`reject` est **terminal** : pas de résurrection, une nouvelle session est créée pour réessayer. Les échecs LLM (`requirement_failed`/`test_design_failed`) sont visibles et relançables via `regenerate`, jamais silencieux.

**`chatting` (module 9, 22/07/2026)** : phase de clarification conversationnelle libre, tout premier statut d'une session — `create_session` ne lance plus le module 2 automatiquement. Voir `docs/module_9_chat.md` pour le détail complet ; résumé : `POST .../chat` (tour de conversation, reste en `chatting`) et `POST .../chat/finalize` (seule sortie vers `analyzing`, transcript complet injecté comme contexte du module 2).

**Traitement asynchrone (modules 2/3)** — `analyzing` et `designing_tests` sont des statuts transitoires : la route répond immédiatement (le kickoff synchrone valide juste l'état et committe), l'appel LLM tourne en tâche de fond (FastAPI `BackgroundTasks`), et le client doit re-interroger `GET /api/sessions/{id}` jusqu'à en sortir. Voir `app/services/session_service.py` (docstring de tête) pour le détail kickoff/run, et `app/database.py::get_session_factory` pour l'ouverture d'une `AsyncSession` dédiée à la tâche de fond.

## Historique des générations (`GenerationModel`, table `generations`)

Extension du modèle (`app/models/session.py`) qui persiste **chaque** exécution du module 2/3 (initiale ou via `regenerate_*`), avec la décision humaine associée :

```
run_requirement_analysis / run_test_design
        --> ligne 'pending' (ou 'failed' directement si l'appel LLM échoue)

accept_requirement / accept_tests  --> dernière ligne 'pending' -> 'accepted'
reject_requirement  / reject_tests --> dernière ligne 'pending' -> 'rejected'
regenerate_requirement / regenerate_tests
        --> dernière ligne 'pending' -> 'rejected' (implicite : régénérer = renoncer),
            PUIS une nouvelle ligne 'pending' sera créée par le run_* suivant
```

Objectif : disposer nativement de paires **(rejected, accepted)** sur un même `session_id`/`stage` — exploitables pour du few-shot RAG (récupérer les générations `accepted` récentes comme exemples) ou un futur fine-tuning par préférence (DPO). **Consommée en lecture depuis le 21/07/2026** par `app/services/rag_service.py`, d'abord pour le module 2 seul puis étendu au module 3 le 23/07/2026 (cf. `docs/module_2_requirement_analyzer.md` et `docs/module_3_test_design_agent.md`) — les paires (rejected, accepted) pour un futur DPO restent, elles, encore non exploitées.

Colonnes clés : `session_id`, `stage` (`"requirement"` | `"test_design"`), `attempt_no` (aligné sur `requirement_regenerations`/`test_regenerations`), `content` (snapshot JSON), `decision`, `error_message`, `decided_at`, `embedding` (vecteur RAG, stocké pour `stage='requirement'` ET `stage='test_design'` depuis le 23/07/2026). Voir tests : `tests/test_session_service.py::TestGenerationHistory`, `TestRAGFewShotIntegration` (module 2) et `TestRAGFewShotIntegrationTestDesign` (module 3).

## Endpoints

| Méthode | Route | Action |
|---|---|---|
| `POST` | `/api/sessions` | `multipart/form-data` (`user_input`, `context?`, `files[]?`) — crée la session en `chatting` (+ pièces jointes, module 2 extension) |
| `GET` | `/api/sessions` | Liste (id, statut, titre, dates) |
| `GET` | `/api/sessions/{id}` | Détail complet |
| `POST` | `/api/sessions/{id}/chat` | Module 9 — tour de conversation (`{"message": "..."}`), reste en `chatting` |
| `POST` | `/api/sessions/{id}/chat/finalize` | Module 9 — sort de `chatting`, lance le module 2 |
| `POST` | `/api/sessions/{id}/requirement/accept` | Valide l'analyse → lance le module 3 |
| `POST` | `/api/sessions/{id}/requirement/reject` | Rejette (terminal) |
| `POST` | `/api/sessions/{id}/requirement/regenerate` | Relance le module 2 |
| `POST` | `/api/sessions/{id}/tests/accept` | Valide les tests → `validated` |
| `POST` | `/api/sessions/{id}/tests/reject` | Rejette (terminal) |
| `POST` | `/api/sessions/{id}/tests/regenerate` | Relance le module 3 |

Erreurs : `SessionNotFoundError` → 404, `InvalidSessionTransitionError` → 409 (gestionnaires enregistrés dans `app/main.py`).

## Choix techniques

1. **SQLite + SQLAlchemy async** (validé) — déjà câblé dans `config.py`/`requirements.txt`, cohérent avec le reste de la stack async.
2. **Traitement asynchrone (tâche de fond)** — remplace le traitement synchrone initial. Un test réel avec Ollama (19/07/2026) a mesuré jusqu'à 268s pour le module 3, à 30s du timeout par défaut (`OLLAMA_TIMEOUT_SECONDS=300`) : garder l'appel LLM dans la requête HTTP aurait bloqué l'UI plusieurs minutes et risqué le timeout. Chaque route qui déclenche un appel LLM (`create_session`, `requirement/accept`, `requirement/regenerate`, `tests/regenerate`) est scindée en un kickoff synchrone (valide l'état, positionne `analyzing`/`designing_tests`, committe, répond) et une tâche de fond (`FastAPI BackgroundTasks`, voir `session_service.run_*_task`) qui exécute l'appel LLM avec sa propre `AsyncSession` (`app/database.py::get_session_factory`, overridable en test comme `get_db`). Le frontend fait du polling (`GET /api/sessions/{id}` toutes les 2s, cf. `App.tsx::pollUntilSettled`) jusqu'à sortir de l'état transitoire.
3. **`reject` terminal** (validé) — historique propre, pas d'ambiguïté avec `regenerate`.
4. **Agents injectés via FastAPI `Depends`** (`app/dependencies.py`) plutôt que construits en dur dans le service — permet aux tests de surcharger `app.dependency_overrides` avec des `FakeLLMProvider` (`tests/fakes/llm.py`) sans toucher au code métier.
5. **Frontend en TypeScript** — décision prise sans validation explicite (à confirmer), pour bénéficier du typage miroir des schémas Pydantic. Migration vers JS pur possible si préféré.
6. **Rendu Gherkin unique, côté backend** — `SessionResponse.gherkin_feature_files` contient le texte `.feature` de chaque feature, produit par `render_gherkin_feature` (la fonction utilisée pour la validation Cucumber et pour les tickets Jira). `GherkinFeatureView.tsx` l'affiche tel quel : plus de seconde implémentation côté frontend qui pourrait dériver.

## Bug corrigé en cours de route (module 1)

Le `MockProvider` ne reconnaissait que les mots-clés `analyze_requirement`/`generate_tests`, absents des vrais system prompts des modules 2/3 — toute saisie libre sans `custom_responses` tombait donc sur la réponse par défaut (texte non-JSON) et échouait systématiquement. Corrigé : nouvelles clés `"requirement analyzer agent"`/`"test design agent"` qui matchent les system prompts réels, `custom_responses` toujours prioritaire sur les défauts. Voir `docs/module_1_llm_layer.md`.

## Bug de sécurité corrigé en cours de route (config)

Une variable d'environnement globale du poste (`DATABASE_URL`, pointant vers un vrai Supabase Postgres) écrasait silencieusement la config du projet via `pydantic-settings` (qui priorise l'environnement système sur `.env`). Corrigé dans `app/config.py` via `settings_customise_sources` : ce projet n'utilise plus que son propre `.env`, jamais l'environnement système.

## Test manuel effectué

Flux complet vérifié dans le navigateur (Vite dev server + backend local) : création de session → analyse affichée → validation → tests affichés (Gherkin rendu correctement) → validation → écran "validated" ; navigation dans l'historique (y compris rechargement d'une session en échec) ; sidebar mise à jour en temps réel après chaque action.

## Lancer les tests

```bash
cd backend
..\..\venv\Scripts\activate
pytest tests/test_session_service.py tests/test_sessions_router.py -v
```

32 tests : machine à états complète (accept/reject/regenerate à chaque étape, transitions invalides, échecs LLM, kickoff/run des tâches de fond, filet de sécurité anti-blocage), câblage HTTP (dependency injection, codes 404/409/422, contrat de polling analyzing/designing_tests).

## Lancer l'app complète

```bash
# Backend
cd backend && ..\..\venv\Scripts\activate && uvicorn app.main:app --reload

# Frontend (autre terminal)
cd frontend && npm install && npm run dev
# puis ouvrir http://localhost:5173
```

## Points d'incertitude

1. **TypeScript non validé explicitement** — décision prise pour matcher le typage backend, à confirmer ou à défaire.
2. **Polling à intervalle fixe (2s), pas de push** — le frontend re-interroge `GET /api/sessions/{id}` en boucle plutôt que d'être notifié (SSE/WebSocket). Simple et suffisant en prototype mono-utilisateur ; générerait un trafic inutile à plus grande échelle (à comparer avec le flux SSE déjà existant du module 6 si on unifie un jour les deux).
3. **Pas de déduplication des tâches de fond** — si le client perd la connexion et relance l'action (ex: double-clic sur "régénérer"), rien n'empêche deux tâches de fond concurrentes sur la même session (la seconde écraserait le résultat de la première). Pas de verrou applicatif. Risque faible en usage normal (le bouton est désactivé pendant `busy`), mais pas garanti contre un retry réseau.
4. **Timeout de polling client (~5 min) découplé du timeout serveur** — `POLL_MAX_ATTEMPTS` dans `App.tsx` est calé sur `OLLAMA_TIMEOUT_SECONDS` par défaut (300s) mais les deux valeurs ne sont pas partagées : si l'un est changé sans l'autre, le polling peut abandonner avant (faux "traitement anormalement long") ou après (attente inutile) l'échec réel côté serveur.
5. **Pas de pagination sur `GET /api/sessions`** — tout l'historique est renvoyé à chaque fois. Suffisant en prototype, à revoir si le volume de sessions grandit.
6. ~~**Rendu Gherkin dupliqué**~~ — **résolu** : le backend fournit le texte rendu (`gherkin_feature_files`), cf. choix techniques #6.
