# Analyse du projet & plan de migration vers une solution déployable

> Projet : **Gitlab & Jira Ticket Automation** (dossier `Test_Automation`)
> Date de l'audit : 09/10/2026
> État audité : commit `b705e4a` (branche `main`)

---

## Sommaire

1. [Résumé](#1-résumé)
2. [Ce que fait le projet](#2-ce-que-fait-le-projet)
3. [Architecture et composants](#3-architecture-et-composants)
4. [Les agents IA](#4-les-agents-ia)
5. [État actuel du projet (vérifié)](#5-état-actuel-du-projet-vérifié)
6. [Le vrai problème : mocks et exécution locale](#6-le-vrai-problème--mocks-et-exécution-locale)
7. [Ce qu'il faut garder / supprimer / modifier](#7-ce-quil-faut-garder--supprimer--modifier)
8. [Architecture cible déployée](#8-architecture-cible-déployée)
9. [Plan de migration étape par étape](#9-plan-de-migration-étape-par-étape)
10. [Checklist avant mise en production](#10-checklist-avant-mise-en-production)

---

## 1. Résumé

| | |
|---|---|
| **But** | Transformer une demande en langage naturel en spécification structurée, puis en scénarios de test Gherkin, puis en tickets Jira/GitLab, avec validation humaine à chaque étape. |
| **Stack** | FastAPI + SQLAlchemy async + SQLite (backend), React 19 + TypeScript + Vite + Tailwind (frontend), Ollama (LLM local). |
| **Taille** | ~2 000 lignes de code backend (hors tests), ~2 200 lignes frontend, 299 tests backend. |
| **Qualité du code** | Bonne : architecture en couches propre, interfaces abstraites partout, erreurs typées, documentation détaillée par module. |
| **Maturité** | **Prototype local.** Fonctionne sur un seul poste avec un GPU. Aucun élément de déploiement (Docker, CI, migrations, HTTPS). |
| **Problème principal** | Les **mocks sont des solutions de repli silencieuses dans le code de production**. Sans configuration, l'application *semble* fonctionner (faux LLM, faux tickets) alors qu'elle ne fait rien de réel. Le tout dépend d'un Ollama sur le poste du développeur. |
| **Verdict** | Le cœur métier (agents, validation, machine à états, intégrations Jira/GitLab réelles) est **solide et à garder**. Il faut **enlever les mocks du chemin de production**, **rendre le LLM interchangeable** (local ou serveur) et **ajouter la couche de déploiement**. Pas besoin de réécrire. |

---

## 2. Ce que fait le projet

### Le parcours d'une demande

```
 Ingénieur : "Je veux ajouter un paiement par QR code" (+ pièces jointes éventuelles)
     │
     ▼
 ① CHAT DE CLARIFICATION ........................... statut: chatting
    L'IA pose 1-2 questions ("QR statique ou dynamique ?").
    Aucun résultat structuré tant que l'ingénieur ne clique pas "Générer l'analyse".
     │
     ▼
 ② DLP : masquage des données personnelles (noms, emails, tél, IBAN)
     │
     ▼
 ③ ANALYSE DU BESOIN ............................... statut: analyzing → pending_requirement_review
    JSON structuré : titre, type, description, personas, critères fonctionnels /
    non fonctionnels, priorité, dépendances, AMBIGUÏTÉS, score de confiance.
     │
     ▼  👤 Valider / Rejeter / Régénérer (avec consigne libre)
     │
 ④ CONCEPTION DES TESTS ............................ statut: designing_tests → pending_test_review
    User stories (En tant que / je veux / afin que) + scénarios Gherkin
    (nominal, erreur, cas limite), validés par le parseur officiel Cucumber.
     │
     ▼  👤 Valider / Rejeter / Régénérer
     │                                               statut: validated
 ⑤ CRÉATION DES TICKETS ............................ statut: tickets_created
    1 ticket par user story sur Jira et/ou GitLab :
    titre = story · description = story + critères + Gherkin · label = priorité
     │
     ▼
 ⑥ SUIVI EN DIRECT (SSE) + ⑦ STATISTIQUES
     │
     ▼
 ⑧ MÉMOIRE : chaque génération acceptée sert d'exemple aux suivantes (RAG)
```

### Machine à états d'une session

```
chatting ──finalize──► analyzing ──► pending_requirement_review ──accept──► designing_tests
                          ▲  │                │        │                         │
                 regenerate  └► requirement_failed     └reject► requirement_rejected (terminal)
                                                                                 ▼
 tickets_created ◄── validated ◄──accept── pending_test_review ◄─────────────────┘
       ▲   │                                   │      │
       │   └► ticket_creation_failed           │      └reject► tests_rejected (terminal)
       └──────────── (retry) ─────────────────-┘ regenerate ► designing_tests
```

Source : [backend/app/services/session_service.py](backend/app/services/session_service.py), [backend/app/schemas/session.py](backend/app/schemas/session.py).

### Contenu d'un ticket créé

Construit dans [ticket_service.py](backend/app/services/ticket_service.py) (`_build_drafts`) :

| Champ | Jira (`POST /rest/api/3/issue`) | GitLab (API v4 issues) |
|---|---|---|
| Titre | `summary` = titre de la user story | `title` |
| Description | Phrase de story + critères d'acceptation + **tous** les scénarios Gherkin de la session, au format ADF | Markdown |
| Type | `issuetype = Story` | label `type::story` |
| Priorité | label `priority::high` | label `priority::high` |
| Projet | `JIRA_PROJECT_KEY` | `GITLAB_PROJECT_ID` |

---

## 3. Architecture et composants

### Vue d'ensemble

```
frontend/ (React)                         backend/ (FastAPI)
┌──────────────────────┐   HTTP + cookie  ┌─────────────────────────────────────────────┐
│ App.tsx (polling 2s) │ ───────────────► │ routers/   → endpoints REST + SSE            │
│ components/*         │ ◄── SSE ──────── │ services/  → orchestration, machine à états  │
│ api.ts               │                  │ agents/    → appels IA (LLM, DLP, vision…)   │
└──────────────────────┘                  │ integrations/ → Jira, GitLab, (mock)         │
                                          │ models/    → tables SQLAlchemy               │
                                          │ schemas/   → contrats Pydantic               │
                                          └──────┬───────────┬──────────────┬────────────┘
                                                 │           │              │
                                           SQLite + uploads/  Ollama (local)  Jira / GitLab / SSO
```

### Backend : fichier par fichier

| Couche | Fichier | Rôle |
|---|---|---|
| **Entrée** | `app/main.py` | App FastAPI, CORS, gestion d'erreurs, `/health` |
| | `app/config.py` | Configuration (lit **uniquement** `.env`, ignore volontairement les variables d'environnement) |
| | `app/database.py` | Moteur SQLAlchemy async, `create_all` au démarrage (pas de migrations) |
| | `app/dependencies.py` | Injection des agents + `get_current_user` (lecture du cookie JWT) |
| **Routers** | `routers/sessions.py` | Création de session, chat, accept/reject/regenerate, pièces jointes |
| | `routers/tickets.py` | Création et rafraîchissement des tickets |
| | `routers/tracking.py` | Liste des tickets, synchronisation, flux SSE |
| | `routers/analytics.py` | Statistiques globales |
| | `routers/auth.py` | Login SSO OIDC, callback, dev-login, logout |
| **Services** | `services/session_service.py` | **Cœur du système** : machine à états, tâches de fond, historique des générations |
| | `services/ticket_service.py` | Construction des tickets et envoi aux providers |
| | `services/tracking_service.py` | Agrégation des tickets de toutes les sessions |
| | `services/analytics_service.py` | Calcul des métriques |
| | `services/rag_service.py` | Recherche des exemples passés par similarité cosinus |
| | `services/attachment_service.py` | Stockage des fichiers + extraction de texte (PDF, DOCX, XLSX, PPTX, TXT) |
| | `services/auth_service.py` | Création/lecture du JWT, upsert utilisateur |
| **Intégrations** | `integrations/base.py` | Interface `BaseTicketProvider` |
| | `integrations/jira_provider.py` | Client Jira Cloud réel (API v3, ADF) |
| | `integrations/gitlab_provider.py` | Client GitLab réel (API v4) |
| | `integrations/mock_provider.py` | Faux tickets en mémoire |
| | `integrations/factory.py` | Choisit réel ou mock selon `.env` (**repli silencieux vers le mock**) |
| **Données** | `models/session.py` | 4 tables : `users`, `sessions`, `generations`, `attachments` |
| | `schemas/*.py` | Contrats Pydantic (validation stricte des sorties LLM) |
| **Outils** | `scripts/seed_rag_from_documents.py` | Amorce la mémoire RAG depuis d'anciens documents (Confluence, Jira exportés) |
| | `docs/module_*.md` | Documentation détaillée des 9 modules, avec leurs points d'incertitude |

### Frontend

| Fichier | Rôle |
|---|---|
| `App.tsx` | Page principale, enchaînement des étapes, **polling toutes les 2 s** pendant les générations |
| `api.ts` | Appels au backend (`VITE_API_BASE_URL`) |
| `types.ts` | Miroir TypeScript des schémas Pydantic |
| `components/ChatThread.tsx` | Conversation de clarification |
| `components/RequirementPanel.tsx` | Affichage de l'analyse |
| `components/TestDesignPanel.tsx` + `GherkinFeatureView.tsx` | Stories et scénarios (rendu Gherkin **dupliqué** du backend) |
| `components/ReviewActions.tsx` | Boutons Valider / Rejeter / Régénérer |
| `components/TicketPanel.tsx` | Tickets créés |
| `components/TrackingDashboard.tsx` | Suivi en direct (EventSource / SSE) |
| `components/AnalyticsDashboard.tsx` | Statistiques |
| `components/SessionHistory.tsx` | Historique des demandes de l'utilisateur |
| `components/LoginScreen.tsx` | Connexion SSO ou dev-login |
| `components/AttachmentPreviewModal.tsx` + `pdfThumbnail.ts` | Aperçu des pièces jointes |

### Modèle de données

| Table | Contenu | Remarque |
|---|---|---|
| `users` | email, nom, `oidc_subject` | Aucun mot de passe (SSO) |
| `sessions` | statut, demande brute, chat, analyse (JSON), tests (JSON), tickets (JSON), compteurs | Une ligne par demande |
| `generations` | chaque sortie LLM + décision humaine + embedding | Sert au RAG ; paires accepté/rejeté utilisables plus tard pour du fine-tuning (DPO) |
| `attachments` | métadonnées des fichiers joints, texte extrait | Les fichiers sont sur disque (`uploads/`) |

Pas de clés étrangères, pas de migrations : le schéma est recréé par `create_all`.

---

## 4. Les agents IA

Il n'y a **pas de framework d'agents** (ni LangChain, ni LangGraph, ni MCP). Ce sont des classes Python qui appellent un LLM et valident sa sortie. L'enchaînement est **déterministe** : c'est le code et l'humain qui décident de l'étape suivante, jamais le LLM. Le LLM ne déclenche aucune action (pas de tool calling).

| Agent / couche | Fichier | Modèle | Entrée → Sortie | Garde-fous |
|---|---|---|---|---|
| **LLM Layer** | `agents/llm_layer.py` | `llama3.2:3b` (Ollama) | `LLMRequest` → `LLMResponse` | Interface abstraite, erreurs typées (timeout, connexion, modèle absent) |
| **Chat Agent** | `agents/chat_agent.py` | idem | historique de conversation → texte | Interdiction de produire du JSON ou un résultat final ; DLP sur les messages utilisateur |
| **Requirement Analyzer** | `agents/requirement_analyzer.py` | idem | demande + contexte + exemples RAG → `RequirementAnalysis` | Mode JSON, validation Pydantic, **relance corrective** (2 max), règle « n'invente rien, mets-le dans `ambiguities` » |
| **Test Design Agent** | `agents/test_design_agent.py` | idem | analyse validée → `TestDesignOutput` | Validation Pydantic + **parseur Cucumber officiel** + contrôle de couverture (cas négatifs et limites) avec relance |
| **DLP Masking** | `agents/dlp_masking.py` | Presidio + spaCy `fr_core_news_lg` | texte → texte masqué | Activé par défaut |
| **Embedding** | `agents/embedding_layer.py` | `nomic-embed-text` (Ollama) | texte → vecteur | Best-effort : en cas d'échec, pas d'exemples RAG |
| **Vision** | `agents/vision_layer.py` | `moondream` (Ollama) | image → description textuelle | Best-effort ; qualité faible (pas d'OCR fiable) |
| **Gherkin Renderer** | `agents/gherkin_renderer.py` | (pas d'IA) | JSON → texte `.feature` + validation | Parseur Cucumber |
| **Auth Layer** | `agents/auth_layer.py` | (pas d'IA) | code OIDC → utilisateur | Mal rangé dans `agents/` : ce n'est pas un agent |

**Limites connues des agents (documentées dans `backend/docs/`) :**
- Le modèle 3B ne produit pas de façon fiable les scénarios négatifs et limites, et s'effondre sur les documents longs (JSON aux champs vides).
- Le seuil RAG (0,72) a été calibré sur **un seul** point de mesure ; une demande sans rapport a déjà obtenu 0,735, donc il peut injecter des exemples hors sujet.
- Le Chat Agent n'a **jamais été testé avec un vrai LLM**.
- Un seul modèle pour tous les agents (`OLLAMA_MODEL` global).
- Aucune évaluation de la qualité des sorties : tous les tests utilisent un faux LLM, ils testent la tuyauterie, pas la qualité.

---

## 5. État actuel du projet (vérifié)

### Ce qui fonctionne

| Vérification | Résultat |
|---|---|
| Tests backend | ✅ **299 réussis, couverture 92 %** (~110 s) |
| Type-check frontend (`tsc`) | ✅ aucune erreur |
| Lint frontend (`oxlint`) | ⚠️ 1 avertissement : dépendance manquante dans un `useEffect`, `App.tsx:191` |
| Tests frontend | ❌ aucun |
| Pipeline complet avec un vrai LLM | ✅ testé manuellement le 19/07/2026 (analyse ~60 s, tests ~268 s) |
| Jira / GitLab réels | ⚠️ code réel présent et testé avec des faux serveurs HTTP ; pas de trace de test sur une vraie instance |
| SSO réel (Entra ID / Google) | ❌ jamais testé contre un vrai fournisseur |

### Bugs et problèmes trouvés

| # | Gravité | Problème | Emplacement |
|---|---|---|---|
| 1 | 🔴 Critique | **Fuite de données sur GitHub** : `test_automation.db.bak-pre-auth` et `.bak-pre-chat` sont commités et contiennent un email professionnel réel et le texte de vraies demandes. Le motif `*.db` du `.gitignore` ne couvre pas `.db.bak-*`. | `backend/*.db.bak-*` |
| 2 | 🔴 Bug | **Toutes les pièces jointes sont enregistrées sous le nom `None.<ext>`** : l'`id` SQLAlchemy n'existe qu'après le flush. Deux fichiers de même extension dans une session s'écrasent. | `services/attachment_service.py:99` |
| 3 | 🟠 Déploiement | `config.py` **ignore les variables d'environnement** : seul `.env` est lu, ce qui est incompatible avec Docker, Kubernetes et la plupart des PaaS. | `app/config.py` (`settings_customise_sources`) |
| 4 | 🟠 Sécurité | Cookie de session sans `secure=True`. | `routers/auth.py` |
| 5 | 🟠 Sécurité | Démarrage possible avec le secret JWT par défaut (cookie falsifiable). | `app/config.py` |
| 6 | 🟠 Sécurité | `dev-login` permet de se connecter avec **n'importe quel email** tant que le SSO n'est pas configuré. | `routers/auth.py` |
| 7 | 🟠 Sécurité | Signature du `id_token` non vérifiée ; logout sans révocation (JWT valide 7 jours). | `agents/auth_layer.py`, `routers/auth.py` |
| 8 | 🟡 Fiabilité | Pas de verrou sur les tâches de fond : un double clic sur « Régénérer » lance deux générations concurrentes. | `services/session_service.py` |
| 9 | 🟡 Fiabilité | Les tâches LLM tournent dans le process web (`BackgroundTasks`) : un redémarrage les perd et la session reste bloquée en `analyzing`. | `routers/sessions.py` |
| 10 | 🟡 Perf | Le RAG charge **tous** les embeddings en mémoire Python à chaque requête. | `services/rag_service.py` |
| 11 | 🟡 Perf | Le tracking et les analytics chargent **toutes** les sessions ; pas de pagination. | `services/tracking_service.py`, `analytics_service.py` |
| 12 | 🟢 Hygiène | `.gitignore` désordonné (doublons, ignore `.env.example` alors qu'il est suivi). | `.gitignore` |

---

## 6. Le vrai problème : mocks et exécution locale

### 6.1 Les mocks : utiles en test, dangereux en production

Les mocks ne sont pas absurdes en soi : ce sont des **doubles de test**, et ils permettent les 299 tests rapides et déterministes. Le problème, c'est **où ils sont et comment ils s'activent** :

| Mock | Où | Comment il s'active | Risque en production |
|---|---|---|---|
| `MockProvider` (faux LLM) | `app/agents/llm_layer.py` | **Valeur par défaut** de `LLM_PROVIDER` = `"mock"` | Si `.env` est oublié, l'app renvoie toujours la même analyse « QR code » quelle que soit la demande |
| `MockTicketProvider` (faux tickets) | `app/integrations/mock_provider.py` | **Automatiquement** dès qu'une variable Jira/GitLab manque | L'utilisateur voit « ticket créé » alors qu'**aucun ticket n'existe**. Repli silencieux, le pire scénario. |
| `NoOpMaskingProvider` | `app/agents/dlp_masking.py` | `DLP_MASK_ENABLED=false` | Les données personnelles partent au LLM sans masquage |
| `NoOpEmbeddingProvider` / `NoOpVisionProvider` | `agents/embedding_layer.py`, `vision_layer.py` | Flags `RAG_ENABLED` / `VISION_ENABLED` | Acceptable (fonctionnalités optionnelles) |
| Helpers de test (`_reset_mock_store_for_tests`, `_backdate_for_tests`) | `app/integrations/mock_provider.py` | Code de test **dans le code de production** | Pollution du code |

**Principe à appliquer :** en production, **un service non configuré doit faire échouer le démarrage**, pas basculer silencieusement vers un faux. Les mocks restent, mais dans `tests/`, ou activables uniquement par un `APP_ENV=dev` explicite.

### 6.2 L'exécution locale : ce qui l'impose

| Dépendance locale | Pourquoi c'est bloquant | Solution |
|---|---|---|
| **Ollama sur le poste du développeur** (`localhost:11434`) | Pas de GPU = pas d'app ; un seul utilisateur à la fois | LLM sur un serveur dédié, **ou** API compatible OpenAI (voir §8) |
| **SQLite** (fichier local) | Un seul process en écriture, pas de sauvegarde, pas de migrations | PostgreSQL + Alembic |
| **`uploads/`** sur disque local | Perdu si le conteneur est recréé | Volume persistant, ou stockage objet (S3/MinIO/Azure Blob) |
| **`BackgroundTasks`** en mémoire | Tâches perdues au redémarrage ; un seul worker possible | File de tâches (arq/Celery + Redis) |
| **`localhost` codé en dur** (CORS, `FRONTEND_URL`, `AUTH_REDIRECT_URI`, `VITE_API_BASE_URL`) | Ne marche que sur le poste | Variables d'environnement, même domaine derrière un reverse proxy |
| **venv partagé** `..\..\venv` (README backend) | Non reproductible | Image Docker |
| **Pas de Docker, pas de CI** | Aucun moyen de livrer | Dockerfiles + docker-compose + GitHub Actions |

---

## 7. Ce qu'il faut garder / supprimer / modifier

### ✅ À GARDER (le cœur de valeur)

| Élément | Pourquoi |
|---|---|
| `schemas/` (Pydantic) | Contrat strict des sorties LLM : c'est ce qui rend le système fiable |
| `agents/requirement_analyzer.py`, `test_design_agent.py`, `chat_agent.py` | Prompts travaillés, relances correctives, validation : la vraie valeur métier |
| `agents/gherkin_renderer.py` | Validation par le parseur Cucumber officiel : argument fort |
| `agents/dlp_masking.py` (Presidio) | Indispensable dès que des données d'entreprise passent par un LLM |
| `services/session_service.py` (machine à états) | Workflow « humain dans la boucle » bien conçu |
| `integrations/jira_provider.py`, `gitlab_provider.py`, `base.py` | Clients réels, conversion ADF, erreurs typées |
| `agents/auth_layer.py` + `services/auth_service.py` (OIDC) | SSO générique Entra ID / Google, à durcir mais bonne base |
| Table `generations` | Données précieuses : historique des décisions humaines (évaluation, RAG, fine-tuning futur) |
| `services/attachment_service.py` (extraction de documents) | Utile, simple, sans modèle (après correction du bug n° 2) |
| `scripts/seed_rag_from_documents.py` | Permet d'amorcer la mémoire avec l'historique Jira/Confluence de l'entreprise |
| `tests/` (299 tests) | Filet de sécurité pour la migration |
| `docs/` | Documentation honnête des limites |
| Frontend (tous les composants) | Fonctionnel et typé |

### ❌ À SUPPRIMER

| Élément | Action | Raison |
|---|---|---|
| `backend/test_automation.db.bak-pre-auth` et `.bak-pre-chat` | `git rm` **+ purge de l'historique** (`git filter-repo`), puis force-push | Fuite de données personnelles |
| Repli automatique vers `MockTicketProvider` dans `integrations/factory.py` | Supprimer le repli ; lever une erreur claire si Jira/GitLab est demandé mais non configuré | Faux tickets présentés comme réels |
| `MockProvider` comme **valeur par défaut** de `LLM_PROVIDER` | Retirer le défaut `"mock"` (obligatoire en prod) | Faux LLM silencieux |
| `_reset_mock_store_for_tests`, `_backdate_for_tests` | Déplacer dans `tests/` | Code de test dans la prod |
| `POST /api/auth/dev-login` en production | Garder seulement derrière `APP_ENV=dev` (refusé sinon) | Connexion sans authentification |
| `frontend/src/assets/hero.png`, `react.svg`, `vite.svg`, `frontend/public/icons.svg` | Supprimer | Restes du template Vite, référencés nulle part |
| `frontend/README.md` | Remplacer | C'est le README par défaut du template Vite |
| Fichiers locaux (`*.log`, `uploads/`, `test_automation.db`) | Nettoyer le poste (déjà ignorés par git) | Données de dev |
| Doublons du `.gitignore` | Réécrire proprement, ajouter `*.db.bak*` | Hygiène |

### 🔧 À MODIFIER

| Élément | Modification |
|---|---|
| `integrations/mock_provider.py`, `MockProvider` | Déplacer dans `tests/fakes/` (ou n'enregistrer dans le registre que si `APP_ENV=dev`) |
| `app/config.py` | Lire **aussi** les variables d'environnement ; ajouter `APP_ENV` (`dev`/`prod`) ; **refuser de démarrer** en prod si le secret JWT est celui par défaut, si `LLM_PROVIDER=mock` ou si aucun provider de tickets n'est configuré |
| `agents/llm_layer.py` | Ajouter un **`OpenAICompatibleProvider`** (une seule classe compatible Ollama `/v1`, vLLM, Azure OpenAI, Mistral, etc.) ; permettre un **modèle par agent** (`TEST_DESIGN_MODEL=llama3.1:8b`…) |
| `services/attachment_service.py:88` | Générer l'id explicitement : `AttachmentModel(id=_new_id(), …)` |
| `routers/auth.py` | `secure=True` sur les cookies quand `APP_ENV=prod` |
| `database.py` | Passer à PostgreSQL (`asyncpg`) + **Alembic** pour les migrations |
| Tâches de fond | `BackgroundTasks` → file de tâches (arq + Redis), avec verrou par session |
| `services/rag_service.py` | PostgreSQL + **pgvector** (recherche vectorielle en SQL) ; seuil à recalibrer sur un vrai échantillon, ou RAG désactivé par défaut d'ici là |
| `agents/vision_layer.py` | `VISION_ENABLED=false` par défaut (qualité faible, coût GPU) ; à réactiver avec un meilleur modèle |
| `agents/auth_layer.py` | Déplacer dans `app/auth/` (ce n'est pas un agent) ; vérifier la signature de l'`id_token` |
| Frontend : polling 2 s | Remplacer par le SSE déjà présent pour le tracking (un seul mécanisme) |
| Frontend : rendu Gherkin dupliqué | Faire renvoyer le texte `.feature` par le backend, supprimer `renderGherkinFeature` côté front |
| `tracking` / `analytics` | Pagination + filtres (période, utilisateur) |

---

## 8. Architecture cible déployée

### 8.1 Choisir où tourne le LLM (décision principale)

| Option | Description | Confidentialité | Coût | Qualité | Recommandation |
|---|---|---|---|---|---|
| **A. Serveur GPU interne** | Ollama ou **vLLM** sur un serveur de l'entreprise | ✅ Données internes | Matériel | Moyenne (modèles 8B-14B) | ✅ **Si les données sont sensibles** (cas probable en entreprise) |
| **B. LLM managé dans le cloud de l'entreprise** | Azure OpenAI / AWS Bedrock dans le tenant de l'entreprise | ✅ Contractuellement protégé | À l'usage | Élevée | ✅ **Le plus simple à exploiter** si la politique de sécurité l'autorise |
| **C. API publique** | OpenAI, Anthropic, Mistral directement | ⚠️ Données hors entreprise | À l'usage | Élevée | Seulement pour des données non sensibles, avec le DLP actif |

Grâce à l'abstraction `BaseLLMProvider`, **le choix se fait par configuration** dès que l'`OpenAICompatibleProvider` est ajouté : Ollama, vLLM et Azure OpenAI exposent tous une API compatible OpenAI.

Dimensionnement si option A :

| Modèle | VRAM | Qualité observée |
|---|---|---|
| `llama3.2:3b` | ~4 Go | Insuffisante pour les tests (couverture incomplète, échecs sur documents longs) |
| `llama3.1:8b` / `qwen2.5:7b` | ~8 Go | Correcte |
| `qwen2.5:14b` / `mistral-small` | ~16-24 Go | Bonne |

Ollama traite les requêtes **une par une** ; avec plusieurs utilisateurs, **vLLM** gère bien mieux la concurrence.

### 8.2 Schéma cible

```
                         Utilisateurs (navigateur)
                                   │ HTTPS
                                   ▼
                    ┌─────────────────────────────┐
                    │  Reverse proxy (Nginx/Traefik) │  un seul domaine :
                    │  /      → frontend statique   │  test-agent.entreprise.com
                    │  /api/* → backend             │
                    └───────┬───────────────────────┘
                            │
             ┌──────────────┴──────────────┐
             ▼                             ▼
   ┌───────────────────┐         ┌───────────────────┐
   │ backend (FastAPI) │ ──────► │ worker (arq)      │  générations LLM
   │ API + SSE         │  Redis  │ analyse / tests   │  hors du process web
   └───┬──────────┬────┘         └───┬──────────┬────┘
       │          │                  │          │
       ▼          ▼                  ▼          ▼
  PostgreSQL   Stockage         LLM (option A/B/C)   Jira / GitLab
  + pgvector   fichiers         vLLM, Ollama ou       (compte de service)
               (volume/S3)      Azure OpenAI
                                                      SSO : Entra ID
```

### 8.3 Services du `docker-compose`

| Service | Image | Rôle |
|---|---|---|
| `proxy` | nginx + build du frontend | HTTPS, fichiers statiques, routage `/api` |
| `backend` | Python 3.11 + dépendances + modèle spaCy | API FastAPI |
| `worker` | même image que `backend` | Tâches LLM en arrière-plan |
| `postgres` | `pgvector/pgvector:pg16` | Données + embeddings |
| `redis` | `redis:7` | File de tâches |
| `llm` (option A) | `ollama/ollama` ou `vllm/vllm-openai` | Modèle sur GPU |

---

## 9. Plan de migration étape par étape

Chaque phase laisse l'application fonctionnelle et les tests au vert.

### Phase 0 : nettoyage et sécurité (½ journée), **à faire en premier**
- [ ] Retirer les `.db.bak-*` du dépôt et **purger l'historique git**, puis force-push
- [ ] Réécrire `.gitignore` (ajouter `*.db.bak*`, supprimer les doublons)
- [ ] Corriger le bug des pièces jointes `None.<ext>`
- [ ] Supprimer les assets inutilisés du template Vite, remplacer `frontend/README.md`
- [ ] Corriger l'avertissement lint de `App.tsx:191`

### Phase 1 : sortir les mocks de la production (1 jour)
- [ ] Ajouter `APP_ENV` (`dev` / `test` / `prod`) dans `config.py`
- [ ] Supprimer le repli automatique vers `MockTicketProvider` ; erreur explicite si non configuré
- [ ] Retirer `"mock"` comme valeur par défaut de `LLM_PROVIDER`
- [ ] Déplacer `MockProvider`, `MockTicketProvider` et les helpers `_*_for_tests` dans `tests/fakes/`
- [ ] Garder `dev-login` uniquement en `APP_ENV=dev`
- [ ] Validation au démarrage en prod : secret JWT, provider LLM, provider de tickets et SSO obligatoires
- [ ] Faire tourner les 299 tests (ils utilisent les fakes déplacés)

### Phase 2 : rendre le LLM interchangeable (1 jour)
- [ ] Ajouter `OpenAICompatibleProvider` dans `llm_layer.py` (`base_url`, `api_key`, `model`)
- [ ] Modèle configurable **par agent** (`CHAT_MODEL`, `ANALYZER_MODEL`, `TEST_DESIGN_MODEL`)
- [ ] Même chose pour les embeddings (API `/v1/embeddings`)
- [ ] Choisir l'option A, B ou C (§8.1) avec l'équipe sécurité

### Phase 3 : configuration et conteneurisation (1-2 jours)
- [ ] `config.py` : lire les variables d'environnement (retirer l'exclusion de `env_settings`)
- [ ] `backend/Dockerfile` (avec `python -m spacy download fr_core_news_lg` dans l'image)
- [ ] `frontend/Dockerfile` (build Vite puis Nginx) avec `VITE_API_BASE_URL=""` (même domaine)
- [ ] `docker-compose.yml` + `.env.production.example`
- [ ] Cookies `secure=True` en prod
- [ ] Healthcheck Docker sur `/health`

### Phase 4 : base de données et tâches de fond (2-3 jours)
- [ ] PostgreSQL (`asyncpg`) + **Alembic** (migration initiale depuis les modèles actuels)
- [ ] `pgvector` pour la colonne `embedding` ; recherche RAG en SQL
- [ ] File de tâches **arq + Redis** à la place de `BackgroundTasks`, avec verrou par session (corrige le double clic)
- [ ] Tâche de récupération : remettre en `*_failed` les sessions bloquées en `analyzing`/`designing_tests` depuis plus de N minutes
- [ ] Stockage des pièces jointes sur volume persistant (ou S3/MinIO)

### Phase 5 : intégrations réelles et SSO (1-2 jours, dépend de l'IT)
- [ ] Créer un **compte de service** Jira + token, et un token de projet GitLab
- [ ] Tester la création réelle de tickets sur un projet Jira de test
- [ ] Enregistrer l'application dans **Entra ID** (procédure : `backend/docs/module_8_auth.md`)
- [ ] Vérifier la signature de l'`id_token` ; tester le flux SSO de bout en bout

### Phase 6 : CI/CD et observabilité (1-2 jours)
- [ ] GitHub Actions : `pytest`, `tsc`, `oxlint`, build des images Docker
- [ ] Logs JSON structurés ; traçage des appels LLM (prompt, latence, tokens), par exemple avec **Langfuse**
- [ ] Sauvegardes automatiques de PostgreSQL

### Phase 7 : qualité de l'IA (continu)
- [ ] Constituer un **jeu d'évaluation** (20-50 demandes réelles anonymisées avec les sorties attendues)
- [ ] Mesurer : taux de JSON valide, couverture des scénarios (négatif, limite), taux d'acceptation au premier essai, nombre de régénérations
- [ ] Comparer les modèles (3B, 8B, 14B, API) sur ce jeu avant de choisir
- [ ] Recalibrer le seuil RAG sur un vrai échantillon
- [ ] Limiter et résumer `chat_history` (les longues conversations dépassent le contexte du modèle)

**Estimation totale : environ 2 à 3 semaines pour une personne**, hors délais de l'IT (GPU, SSO, comptes de service).

---

## 10. Checklist avant mise en production

**Sécurité**
- [ ] Aucune donnée réelle dans le dépôt git (historique purgé)
- [ ] `AUTH_SESSION_SECRET` aléatoire (≥ 32 octets), stocké dans un coffre de secrets
- [ ] SSO configuré ; `dev-login` inaccessible
- [ ] HTTPS ; cookies `secure` + `httponly` + `samesite=lax`
- [ ] Tokens Jira/GitLab de **compte de service**, droits minimaux, jamais commités
- [ ] DLP activé (`DLP_MASK_ENABLED=true`) et testé sur des exemples réels
- [ ] Taille et type des pièces jointes limités (déjà présent : 15 Mo)

**Fiabilité**
- [ ] Aucun mock accessible en `APP_ENV=prod` ; le démarrage échoue si la config est incomplète
- [ ] PostgreSQL avec sauvegardes ; migrations Alembic
- [ ] Tâches LLM dans un worker séparé, avec verrou par session et récupération des sessions bloquées
- [ ] Timeouts LLM alignés entre le backend et le frontend

**Exploitation**
- [ ] `docker compose up` démarre tout depuis zéro
- [ ] CI au vert (tests backend, type-check et lint frontend)
- [ ] Logs centralisés et traçage des appels LLM
- [ ] Healthcheck surveillé (`/health` vérifie déjà la disponibilité du LLM)

**Qualité IA**
- [ ] Modèle choisi sur un jeu d'évaluation, pas au ressenti
- [ ] Premier tour avec un petit groupe d'ingénieurs pilotes ; suivi du taux d'acceptation dans les analytics
