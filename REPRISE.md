# Reprise du projet sur un nouveau poste

> Rédigé le 09/10/2026, au moment de quitter l'ancien PC. À lire avant de reprendre le travail (toi, ou une nouvelle session Claude Code).

## 1. Ce que contient le dépôt (privé)

Le dépôt contient **tout** ce qu'il faut pour continuer, y compris des fichiers qui ne devraient pas être versionnés dans un dépôt public :

| Fichier | Contenu sensible |
|---|---|
| `backend/.env` | Vrai token API Jira (compte Atlassian) et config locale |
| `backend/test_automation.db` + `test_automation.db.bak-*` | Sessions de développement, dont un email professionnel et de vraies demandes |
| `backend/uploads/` | Pièces jointes envoyées pendant le développement |
| `backend/*.log`, `frontend/*.log` | Logs des anciens lancements |
| `.claude/` | Réglages Claude Code du projet |

Seuls `node_modules/`, `venv/`, `dist/` et les caches Python ne sont pas versionnés : ils se recréent (étape 2).

**Avant de rendre le dépôt public** (semaine 6 du plan) : retirer ces fichiers **de tout l'historique** (`git filter-repo --invert-paths --path backend/.env --path-glob 'backend/*.db*' --path backend/uploads`), révoquer le token Jira, remettre un `.gitignore` strict (voir l'historique git de `.gitignore`, commit §3.1) et vérifier avec l'entreprise ce qui peut être publié.

## 2. Installer sur le nouveau poste

Prérequis : Git, Python 3.11, Node.js 24 (même npm que le `package-lock.json`), Docker Desktop, [Ollama](https://ollama.com).

```bash
git clone https://github.com/JadFalaq/Gitlab-Test-and-Jira-Tcket-Automation-.git Test_Automation
cd Test_Automation

# Backend
python -m venv .venv
.venv\Scripts\activate            # Windows (Linux/Mac : source .venv/bin/activate)
pip install -r backend/requirements-dev.txt
python -m spacy download fr_core_news_lg

# Frontend
cd frontend && npm ci && cd ..

# Modèles Ollama
ollama pull llama3.2:3b
ollama pull nomic-embed-text
ollama pull moondream

# Vérifier
cd backend && python -m pytest -q   # attendu : 369 passed
cd ../frontend && npx tsc -b && npx oxlint
```

Lancer l'application :

- **Docker** : `docker compose up --build` à la racine, puis http://localhost:8080 (PostgreSQL neuf, la base SQLite n'est pas reprise).
- **Sans Docker** : `cd backend && uvicorn app.main:app --reload` + `cd frontend && npm run dev`, puis http://localhost:5173 (utilise `backend/test_automation.db` et `backend/.env`).

Scan de secrets avant chaque commit (optionnel tant que `.env` est versionné, car gitleaks le signalera) : `pip install pre-commit && pre-commit install`.

## 3. Ce qui n'est PAS dans le dépôt

| Élément (ancien PC) | Que faire |
|---|---|
| `Projets/venv` (venv partagé) | Recréé à l'étape 2 (`.venv` dans le projet) |
| `Projets/depot-backup.git` (sauvegarde avant purge) | Inutile : contient l'ancien historique avec l'email. Ne pas reprendre |
| Volumes Docker (PostgreSQL de test) | Une seule session de démonstration : rien à reprendre |
| Modèles Ollama | Re-télécharger (étape 2) |
| `Projets/Test_Automation - Copie` | Dossier non examiné : à vérifier sur l'ancien PC s'il contient autre chose |

## 4. Où en est le projet

Le plan complet est dans [PLAN_ACTION.md](PLAN_ACTION.md) (cases cochées = fait), l'audit initial dans [ANALYSE_PROJET.md](ANALYSE_PROJET.md).

**Semaine 1 : terminée** (6 commits §3.1 à §3.6, fusionnés dans `main`) :
- GitLab supprimé, Jira uniquement ; aucun mock dans l'application (faux LLM et faux Jira dans `backend/tests/fakes/`).
- `APP_ENV=prod` refuse de démarrer si la config est incomplète (`backend/tests/test_config.py`).
- Sécurité : id_token OIDC vérifié, cookies `secure` en prod, type réel des pièces jointes vérifié, suivi et SSE filtrés par utilisateur (`backend/tests/test_ownership.py`).
- Docker : `docker-compose.yml` (proxy Nginx, backend, PostgreSQL, Redis), testé de bout en bout.
- Historique git purgé des anciennes bases (09/10/2026), token GitLab révoqué via l'API.

Reste de la semaine 1, côté humain : prévenir les personnes qui avaient cloné le dépôt (historique réécrit le 09/10/2026).

**Semaine 2 : prochaine étape** (jeu d'évaluation + Langfuse). Format décidé pour le jeu d'évaluation :
- un fichier par cas dans `eval/cases/` : demande, contexte, pièces jointes éventuelles, attentes minimales (champs requis, nombre minimal de scénarios négatifs et de cas limites, ambiguïtés attendues) ;
- schéma Pydantic pour valider chaque cas au chargement ;
- `eval/run_eval.py` : rejoue le jeu, produit un rapport JSON + tableau Markdown.

Points connus, reportés volontairement : Redis pas encore utilisé (worker arq en semaine 3), vulnérabilités `npm audit` (semaine 5), qualité réelle des réponses du LLM jamais mesurée (objet de la semaine 2).
