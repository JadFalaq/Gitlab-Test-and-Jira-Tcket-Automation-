# Plan d'action : de prototype local à projet phare exploitable

> Projet : Jira Ticket Automation (ex "Gitlab & Jira Ticket Automation")
> Périmètre : **Jira uniquement** (GitLab supprimé), RAG léger et mesuré
> Durée estimée : 6 semaines à temps partiel (≈ 15 h/semaine), hors délais IT

---

## 0. Objectif et critères de réussite

**Objectif :** une plateforme utilisée réellement par une équipe, qui transforme une demande en langage naturel en spécification, scénarios Gherkin et tickets Jira, avec validation humaine, et dont la qualité est **mesurée**.

**Définition de "terminé" (tous les critères doivent être vrais) :**

| # | Critère | Preuve |
|---|---|---|
| 1 | `docker compose up` démarre tout depuis zéro | Vidéo ou README testé sur machine vierge |
| 2 | Aucun mock accessible en prod, démarrage refusé si config incomplète | Test automatisé |
| 3 | Tickets créés sur une vraie instance Jira | Capture + test d'intégration |
| 4 | Jeu d'évaluation de 30 à 50 cas, métriques en CI, blocage du merge en cas de régression | Pipeline CI vert |
| 5 | Traçage de chaque appel LLM (Langfuse) | Dashboard |
| 6 | Pilote avec 3 à 5 ingénieurs pendant 2 à 4 semaines | Page de résultats chiffrés |
| 7 | Apport du RAG mesuré (avec / sans) | Tableau dans le README |
| 8 | Dépôt public propre, données synthétiques, démo | README + vidéo 2 min |

---

## 1. Décisions d'architecture (à figer dès le début)

| Sujet | Décision | Raison |
|---|---|---|
| Tickets | **Jira Cloud uniquement**, suppression de GitLab | Simplifie le code, les tests, la doc |
| Orchestration | **LangGraph** avec checkpointing PostgreSQL et `interrupt` pour les validations humaines | Remplace la machine à états maison, reprend les sessions après redémarrage |
| LLM | Interface `OpenAICompatibleProvider` + **LiteLLM** (optionnel) ; un modèle par étape | Interchangeable : Ollama, vLLM, Azure OpenAI |
| Sorties structurées | Structured outputs natifs si le modèle les supporte, sinon JSON + validation Pydantic + relance | Moins de relances correctives |
| Base | **PostgreSQL + pgvector + Alembic** | Remplace SQLite, RAG en SQL, migrations |
| Tâches | **arq + Redis**, verrou par session | Plus de tâches perdues, plus de double clic |
| RAG | **Léger** : pgvector + recherche hybride (BM25 + vecteur), top-k petit, seuil recalibré, désactivable | Voir §6 |
| Observabilité | **Langfuse** | Prompts, latence, tokens, coût, lien avec la décision humaine |
| Jira | Compte de service + token, serveur **MCP Jira** (lecture + création) | Réutilisable et moderne |
| Front | Garder React, remplacer le polling par SSE | Un seul mécanisme temps réel |

**À ne pas faire :** multi-agents autonomes, réécriture du front, nouvelles fonctionnalités avant l'évaluation.

---

## 2. Planning global

| Semaine | Thème | Livrable principal |
|---|---|---|
| 1 | Nettoyage, sécurité, Jira seul, Docker | Application déployable en local sans mock |
| 2 | Évaluation et Langfuse | Jeu d'éval + baseline chiffrée |
| 3 | PostgreSQL, worker, LangGraph | Workflow reprenable, sans sessions bloquées |
| 4 | RAG léger, agent de vérification, MCP Jira | RAG mesuré, détection de doublons |
| 5 | CI/CD, durcissement, déploiement | Pipeline complet, instance déployée |
| 6 | Pilote, mesures, documentation | Résultats chiffrés, README, démo |

---

## 3. Semaine 1 : nettoyage, sécurité, Jira uniquement, Docker

### 3.1 Fuite de données (à faire en premier)
- [x] `git rm` des `test_automation.db.bak-pre-auth` et `.bak-pre-chat`
- [ ] Purge de l'historique avec `git filter-repo`, puis force-push — **en attente de ton accord** (réécrit l'historique distant)
- [ ] Considérer l'email et les demandes comme exposés si le dépôt a été public, prévenir la personne concernée — **à faire par toi** ; révoquer aussi le token GitLab présent dans `backend/.env`
- [x] Réécrire `.gitignore` (ajouter `*.db.bak*`, supprimer les doublons, ne plus ignorer `.env.example`)
- [x] Ajouter un scan de secrets en pre-commit (gitleaks) — `.pre-commit-config.yaml` ; activer avec `pre-commit install`

### 3.2 Suppression de GitLab
- [x] Supprimer `integrations/gitlab_provider.py` et ses tests
- [x] Retirer `GITLAB_*` de `config.py`, `.env.example`, docs
- [x] Simplifier `factory.py` : un seul provider (`JiraProvider`), plus de sélection multi-provider
- [x] Adapter `ticket_service.py` (`_build_drafts`) : ne plus produire de brouillon GitLab
- [x] Adapter le schéma `tickets` des sessions et le front (`TicketPanel`, tracking, analytics) : retirer le choix de provider
- [x] Migration des données existantes si besoin

### 3.3 Sortir les mocks de la production
- [x] Ajouter `APP_ENV` (`dev` / `test` / `prod`) dans `config.py`
- [x] Supprimer le repli silencieux vers `MockTicketProvider`
- [x] Supprimer la valeur par défaut `"mock"` de `LLM_PROVIDER`
- [x] Déplacer `MockProvider`, `MockTicketProvider` et les helpers `_*_for_tests` dans `tests/fakes/`
- [x] `dev-login` accessible seulement si `APP_ENV=dev`
- [x] Validation au démarrage en prod : secret JWT non par défaut (≥ 32 octets), provider LLM, Jira et SSO configurés, sinon **le démarrage échoue**
- [x] Test automatisé qui vérifie ce refus de démarrer

### 3.4 Corrections de bugs et sécurité
- [x] Bug pièces jointes `None.<ext>` : générer l'id avant le flush (`AttachmentModel(id=_new_id(), ...)`), ajouter un test avec deux fichiers de même extension
- [x] `config.py` : lire aussi les variables d'environnement
- [x] Cookies `secure=True` en prod, `httponly`, `samesite=lax`
- [x] Vérifier la signature de l'`id_token` OIDC
- [x] **Contrôle de propriété** sur chaque endpoint (sessions, pièces jointes, SSE) : un utilisateur ne lit que ses sessions, avec un test dédié
- [x] Limiter le type réel des pièces jointes (pas seulement l'extension) et la taille
- [x] Corriger l'avertissement lint `App.tsx:191`

### 3.5 Hygiène
- [x] Supprimer `hero.png`, `react.svg`, `vite.svg`, `icons.svg`, remplacer `frontend/README.md`
- [x] Déplacer `auth_layer.py` dans `app/auth/` (devenu `app/auth/oidc.py`)
- [x] Faire renvoyer le `.feature` par le backend et supprimer `renderGherkinFeature` du front

### 3.6 Docker de base
- [x] `backend/Dockerfile` (avec `python -m spacy download fr_core_news_lg`)
- [x] `frontend/Dockerfile` (build Vite puis Nginx), `VITE_API_BASE_URL=""`
- [x] `docker-compose.yml` : proxy, backend, postgres, redis
- [x] `.env.production.example`, healthcheck sur `/health`

**Sortie de semaine 1 :** les 299 tests (moins ceux de GitLab) passent, l'app tourne en conteneurs, aucun mock en prod.

---

## 4. Semaine 2 : évaluation et observabilité (le cœur du projet)

### 4.1 Jeu d'évaluation
- [ ] Collecter 30 à 50 demandes **réelles anonymisées** (ou synthétiques réalistes si confidentialité)
- [ ] Répartir : simples, ambiguës, longues, avec pièces jointes, cas limites
- [ ] Pour chaque cas : sortie attendue minimale (champs requis, nombre de scénarios négatifs et limites, ambiguïtés attendues)
- [ ] Annoter à la main 15 à 20 sorties pour calibrer le juge LLM

### 4.2 Métriques
| Métrique | Définition | Type |
|---|---|---|
| JSON valide au premier essai | sortie conforme au schéma sans relance | automatique |
| Gherkin valide | accepté par le parseur Cucumber | automatique |
| Couverture des scénarios | présence de nominal, erreur, limite | automatique |
| Fidélité | rien d'inventé par rapport à la demande | LLM-as-judge |
| Qualité des critères d'acceptation | testables, non ambigus | LLM-as-judge |
| Taux d'acceptation au premier essai | décisions humaines (pilote) | produit |
| Nombre de régénérations | moyenne par session | produit |
| Latence et coût | par étape | Langfuse |

- [ ] Script `eval/run_eval.py` : rejoue le jeu, produit un rapport JSON + tableau Markdown
- [ ] Juge LLM avec grille de notation fixe, corrélation mesurée avec tes annotations humaines

### 4.3 Baseline et comparaison de modèles
- [ ] Mesurer `llama3.2:3b` (baseline actuelle), un 7-8B, un 14B, et un modèle managé
- [ ] Mesurer 2 à 3 versions de prompt
- [ ] **Noter le résultat dans un tableau** : c'est la base de tous les chiffres du CV

### 4.4 Langfuse
- [ ] Déployer Langfuse dans le compose
- [ ] Tracer chaque appel : prompt, version, modèle, latence, tokens, coût, session
- [ ] Remonter la décision humaine (accepté / rejeté / régénéré) comme score Langfuse

**Sortie de semaine 2 :** un tableau "modèle × prompt × métriques" et un dashboard Langfuse.

---

## 5. Semaine 3 : PostgreSQL, worker, LangGraph

### 5.1 Base de données
- [ ] PostgreSQL via `asyncpg`
- [ ] Alembic : migration initiale depuis les modèles actuels
- [ ] Ajouter les clés étrangères et les index manquants
- [ ] Pagination et filtres (période, utilisateur) sur tracking et analytics

### 5.2 Tâches de fond
- [ ] arq + Redis, service `worker` dans le compose
- [ ] Verrou par session (empêche deux régénérations concurrentes)
- [ ] Tâche de récupération : sessions bloquées en `analyzing` / `designing_tests` depuis plus de N minutes remises en `*_failed`
- [ ] Timeouts LLM alignés entre backend et front

### 5.3 LangGraph
Graphe cible :

```
clarify → [interrupt: finalize] → analyze → [interrupt: validate requirement]
   → design_tests → verify → [interrupt: validate tests] → create_jira_tickets → done
```

- [ ] Checkpointer PostgreSQL : reprise après redémarrage
- [ ] `interrupt` pour chaque validation humaine, `Command(resume=...)` pour accepter / rejeter / régénérer avec consigne
- [ ] Conserver `services/session_service.py` comme façade (API inchangée) pour ne pas casser le front
- [ ] Garder le déterminisme : le LLM ne décide pas de l'étape suivante
- [ ] Tests : transitions, reprise après crash simulé, régénération avec consigne

### 5.4 Chat de clarification
- [ ] Évaluer le Chat Agent avec un vrai LLM sur 10 conversations
- [ ] Sinon le simplifier en formulaire de clarification guidé (3 questions ciblées générées par le LLM)

**Sortie de semaine 3 :** workflow reprenable, plus de session bloquée, pas de double génération.

---

## 6. Semaine 4 : RAG léger, vérification, MCP Jira

### 6.1 RAG léger (volontairement simple et mesuré)
Principe : **le RAG n'est gardé que s'il prouve son utilité.**
- [ ] Source des exemples : générations **acceptées** de la table `generations` + export Jira/Confluence de l'équipe (script `seed_rag_from_documents.py` existant)
- [ ] Stockage : colonne `vector` avec **pgvector**, recherche en SQL (fin du chargement en mémoire Python)
- [ ] Recherche hybride : BM25 (`tsvector` Postgres) + similarité vectorielle, fusion par RRF
- [ ] Top-k = 2 ou 3, seuil de similarité recalibré sur le jeu d'éval (pas sur un seul point)
- [ ] Filtre par projet Jira et par type de demande pour éviter les exemples hors sujet
- [ ] Les exemples injectés sont affichés à l'utilisateur ("inspiré de PROJ-123"), ce qui renforce la confiance
- [ ] **Expérience A/B** sur le jeu d'éval : sans RAG vs avec RAG, mêmes métriques
- [ ] Décision documentée : RAG activé si gain mesurable, sinon désactivé par défaut avec explication dans le README
- [ ] Protéger contre l'empoisonnement : seules les générations acceptées et masquées par le DLP entrent dans l'index

### 6.2 Agent de vérification (la seule vraie touche agentique)
- [ ] Étape `verify` après `design_tests` : relit chaque scénario Gherkin contre les critères d'acceptation, signale les critères non couverts
- [ ] Détection de **doublons** : avant création, recherche dans Jira (JQL + similarité) des tickets proches, proposition à l'utilisateur de lier ou ignorer
- [ ] Les alertes s'affichent dans l'écran de validation, jamais de blocage automatique
- [ ] Mesurer son apport sur le jeu d'éval

### 6.3 Serveur MCP Jira
- [ ] Exposer 4 outils : `search_issues`, `get_issue`, `create_issue`, `link_issues`
- [ ] Réutiliser `jira_provider.py`, avec droits minimaux
- [ ] Documenter l'usage depuis un client MCP (démonstration dans le README)
- [ ] Le workflow principal peut utiliser la recherche pour la détection de doublons

### 6.4 Sécurité LLM
- [ ] Test d'injection de prompt dans une pièce jointe (instruction cachée dans un PDF) : le résultat doit rester conforme au schéma et ne pas suivre l'instruction
- [ ] DLP : tester sur des exemples réels français (noms, IBAN, téléphones marocains)

**Sortie de semaine 4 :** RAG mesuré avec décision chiffrée, vérification et doublons, serveur MCP.

---

## 7. Semaine 5 : CI/CD, tests, déploiement

### 7.1 CI GitHub Actions
- [ ] Jobs : `pytest` (avec couverture), `tsc`, `oxlint`, build des images Docker
- [ ] Job **évaluation** : rejoue le jeu d'éval sur un sous-ensemble, compare à la baseline, **échoue si une métrique régresse** au-delà d'un seuil
- [ ] Job sécurité : gitleaks, `pip-audit`, `npm audit`

### 7.2 Tests manquants
- [ ] Tests frontend (Vitest + Testing Library) sur les écrans de validation
- [ ] Test d'intégration Jira réel sur un projet de test (marqué `@integration`, hors CI par défaut)
- [ ] Test SSO réel contre Entra ID ou Google
- [ ] Test de charge léger (5 utilisateurs concurrents) pour dimensionner le LLM

### 7.3 Exploitation
- [ ] Rate limiting par utilisateur
- [ ] Logs JSON structurés
- [ ] Sauvegardes PostgreSQL automatiques
- [ ] Politique de rétention des données (durée de conservation, suppression sur demande), conformité loi 09-08 / RGPD selon le contexte
- [ ] UX de progression pendant les 1 à 5 minutes de génération (étapes affichées via SSE)

### 7.4 Déploiement
- [ ] Choisir l'option LLM avec l'équipe sécurité : serveur GPU interne, LLM managé dans le tenant de l'entreprise, ou API publique avec DLP actif
- [ ] Déployer sur une VM ou un PaaS, HTTPS, un seul domaine

**Sortie de semaine 5 :** instance accessible à la team, CI complète.

---

## 8. Semaine 6 : pilote, mesures, documentation

### 8.1 Pilote
- [ ] 3 à 5 ingénieurs, 2 à 4 semaines d'usage réel
- [ ] Mesure de référence : temps moyen pour écrire un ticket + scénarios à la main (chronométrer 10 tickets avant)
- [ ] Mesures pendant le pilote : sessions, tickets créés, taux d'acceptation au premier essai, régénérations, temps par ticket, nombre de rejets et pourquoi
- [ ] Questionnaire court en fin de pilote (utilité, confiance, ce qui manque)

### 8.2 Calcul des chiffres du CV
| Chiffre | Calcul |
|---|---|
| Taux d'acceptation X % → Y % | baseline du jeu d'éval vs version finale, puis pilote |
| Heures économisées Z | (temps manuel moyen − temps avec l'outil) × nombre de tickets |
| N ingénieurs | utilisateurs actifs du pilote |
| Cas d'éval / métriques | taille du jeu et nombre de métriques |

**Règle :** n'écrire aucun chiffre qui ne vienne pas d'une mesure que tu peux montrer.

### 8.3 Documentation et vitrine
- [ ] README : problème, démo GIF, architecture, décisions et alternatives écartées, résultats de l'évaluation, résultats du pilote, limites connues
- [ ] Schéma d'architecture et schéma du graphe LangGraph
- [ ] Vidéo de démonstration de 2 minutes
- [ ] Dépôt public propre : données synthétiques, aucune donnée d'entreprise, historique git purgé
- [ ] Article ou post technique : "Comment j'ai mesuré la qualité d'un workflow LLM" (le sujet le plus valorisé)
- [ ] Vérifier avec l'entreprise ce qui peut être publié, publier les chiffres du pilote en agrégé

---

## 9. Backlog optionnel (seulement si le temps le permet)

- Fine-tuning ou DPO d'un modèle 7-8B sur les paires accepté/rejeté de `generations`, comparé à la base sur le jeu d'éval (uniquement avec assez de données)
- Remplacement de `moondream` par un vrai modèle multimodal ou un OCR (désactivé par défaut d'ici là)
- Pré-remplissage de champs Jira personnalisés (composants, sprint, epic)
- Retour de statut : mise à jour de la session quand le ticket Jira change (webhook)

---

## 10. Risques et parades

| Risque | Parade |
|---|---|
| Pas assez de demandes réelles pour l'éval | Compléter avec des cas synthétiques validés à la main |
| GPU ou accès LLM managé indisponibles | Commencer avec l'option C (API) et le DLP actif sur données non sensibles |
| Délais IT (SSO, compte de service Jira) | Les demander dès la semaine 1 |
| Pilote trop court ou trop petit | Annoncer honnêtement la taille de l'échantillon dans les résultats |
| LangGraph complexifie trop | Garder la façade `session_service` : retour possible à l'ancienne machine à états |
| RAG sans gain | Le désactiver et documenter le résultat, c'est un résultat valide |
| Confidentialité | Données synthétiques en public, chiffres agrégés |

---

## 11. Formulation CV (à remplir avec tes mesures)

> **Jira Ticket Automation** (projet d'équipe en production). Plateforme transformant une demande en langage naturel en spécification, scénarios Gherkin et tickets Jira, avec validation humaine à chaque étape. Orchestration **LangGraph** (checkpointing, interruptions humaines), **RAG hybride pgvector** mesuré en A/B, évaluation automatisée en CI (**X cas, Y métriques**), observabilité **Langfuse**, passerelle multi-modèles, serveur **MCP Jira**. Taux d'acceptation au premier essai de **X % à Y %**, utilisé par **N ingénieurs**, **Z heures** économisées. Stack : Python, FastAPI, LangGraph, PostgreSQL/pgvector, Redis, Docker, React, GitHub Actions.
