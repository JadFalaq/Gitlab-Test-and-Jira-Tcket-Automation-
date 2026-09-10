# Module 2 — Requirement Analyzer Agent

## Rôle

Comprend la demande brute d'un ingénieur (texte libre / chat) et la transforme en JSON structuré exploitable par le module 3 (Test Design Agent). S'appuie sur le module 1 (LLM Abstraction Layer) — aucun appel LLM direct.

## Architecture

```
RequirementAnalyzerAgent
    .analyze(RequirementAnalysisRequest, examples=[...]) -> RequirementAnalysis
        0. DLP : masque user_input/context (app/agents/dlp_masking.py)
        1. Construit un LLMRequest (system prompt + exemples few-shot RAG + demande masquée)
        2. Appelle provider.complete() (module 1)
        3. Parse + valide le JSON contre RequirementAnalysis
        4. Si invalide : retry avec message correctif (jusqu'à LLM_MAX_RETRIES fois)
        5. Si toujours invalide : lève RequirementAnalysisError
```

`examples` est fourni par `session_service.run_requirement_analysis` (cf. `app/services/rag_service.py`) — cet agent n'accède jamais lui-même à la DB ou à un modèle d'embedding, il se contente d'injecter ce qu'on lui passe dans le prompt (séparation services/orchestration ↔ agents/exécution LLM).

## Schémas d'interface

| Schéma | Direction | Champs principaux |
|--------|-----------|-------------------|
| `RequirementAnalysisRequest` | Input | `user_input` (texte libre, requis), `context` (optionnel) |
| `RequirementAnalysis` | Output | `title`, `type` (feature/bug/improvement/other), `description`, `user_personas`, `functional_criteria`, `non_functional_criteria`, `priority`, `dependencies`, `ambiguities`, `confidence_score` |

Ce schéma reprend exactement les champs de l'exemple fictif déjà défini dans `MockProvider.DEFAULT_RESPONSES["analyze_requirement"]` (module 1), pour rester cohérent avec ce qui existait déjà.

## Choix techniques

1. **Anti-hallucination dans le prompt** — le system prompt interdit explicitement d'inventer une information absente du texte fourni ; tout ce qui manque doit apparaître dans `ambiguities`.
2. **Ambiguïtés non-bloquantes** — l'agent ne refuse jamais de produire une analyse ; c'est le module 4 (validation humaine) qui arbitre les points flous avec l'ingénieur.
3. **Retry avec message correctif** — si le LLM renvoie un JSON invalide ou qui ne respecte pas le schéma, l'agent lui renvoie l'erreur et redemande, jusqu'à `settings.LLM_MAX_RETRIES` fois (config déjà présente dans le module 1).
4. **Pas d'endpoint HTTP pour l'instant** — comme le module 1, ce module est testable isolément via pytest, sans passer par l'API. Le routage sera ajouté avec le module 4 (interface de validation).
5. **DLP en amont de l'appel LLM** (ajouté 21/07/2026) — `user_input`/`context` sont masqués par `app/agents/dlp_masking.py` (Presidio + spaCy `fr_core_news_lg`, 100% local) AVANT toute construction de message LLM. C'est le seul point d'entrée du texte brut de l'ingénieur dans le pipeline : le masquage se propage naturellement au module 3 (qui ne travaille que sur le `RequirementAnalysis` déjà masqué) puis aux tickets Jira/GitLab (module 5), sans logique dupliquée à chaque sortie. `session.raw_input` en base N'EST PAS masqué (aucune fuite externe associée au stockage local). Piloté par `settings.DLP_MASK_ENABLED` (`True` par défaut) — désactivé de force dans toute la suite de tests (`tests/conftest.py`) pour rester déterministe.
6. **RAG few-shot** (ajouté 21/07/2026) — avant chaque analyse, `session_service.run_requirement_analysis` retrouve jusqu'à `RAG_TOP_K` générations `requirement` passées **acceptées** par un humain, sémantiquement proches (similarité cosinus ≥ `RAG_MIN_SIMILARITY`) de la demande masquée courante (cf. `app/services/rag_service.py`), et les injecte dans le prompt comme exemples de format/niveau de détail. Embeddings calculés via Ollama (`nomic-embed-text`, `app/agents/embedding_layer.py`), stockés sur `GenerationModel.embedding` **pour toute génération réussie** (pas seulement les acceptées — le filtre `decision='accepted'` s'applique à la lecture, pas à l'écriture). Recherche vectorielle "brute force" en Python (similarité cosinus sur des listes JSON), volontairement sans sqlite-vec/pgvector — acceptable au volume d'un prototype. Best-effort total : Ollama down ou modèle d'embedding non tiré (`ollama pull nomic-embed-text`) → poursuite sans exemples, jamais d'échec de l'analyse pour cette raison. Piloté par `settings.RAG_ENABLED` (`True` par défaut) — désactivé de force en tests (`tests/conftest.py`). **Depuis le 23/07/2026, `rag_service.py` est générique sur `stage` et sert aussi le module 3** (cf. `docs/module_3_test_design_agent.md`, section "RAG few-shot étendu au module 3") — ce fichier ne change pas, seul l'appelant (`run_test_design`) diffère.
7. **Pièces jointes** (ajouté 21/07/2026) — l'ingénieur peut joindre des photos et documents à la création d'une session (`POST /api/sessions` en `multipart/form-data`, cf. `app/routers/sessions.py`). Chaque fichier est écrit sur disque immédiatement (kickoff rapide, `app/services/attachment_service.py`), puis traité dans `run_requirement_analysis` (tâche de fond, avant l'appel LLM) : extraction de texte pour les documents (PDF via `pypdf`, Word via `python-docx`, Excel via `openpyxl`, PowerPoint via `python-pptx`, texte/markdown en lecture directe), description factuelle via un modèle vision Ollama (`moondream` par défaut, `app/agents/vision_layer.py`) pour les images. Le texte obtenu est concaténé au `context` de la session **pour cet appel uniquement** (`session.context` en base n'est jamais modifié) puis passe par le DLP exactement comme le `context` saisi par l'ingénieur — même champ, même masquage, aucune logique dupliquée. Une pièce jointe déjà traitée (texte extrait ou erreur déjà connue) n'est jamais retraitée à un `regenerate_requirement` suivant. Entièrement best-effort : fichier corrompu, format non reconnu ou modèle vision indisponible → `extraction_error` renseigné sur la pièce jointe concernée, l'analyse continue normalement avec les autres. Nom de fichier sur disque toujours dérivé de l'id (UUID) de la pièce jointe, jamais du nom original (`filename`, conservé en métadonnée d'affichage uniquement) — protection contre la traversée de répertoire. Piloté par `settings.VISION_ENABLED` pour la partie image (`True` par défaut, désactivé de force en tests) ; l'extraction de documents n'a pas d'interrupteur (aucun modèle requis, toujours active).

## RAG — vérification manuelle (21/07/2026, Ollama + nomic-embed-text réels)

Génération acceptée en base : *"Exporter les tâches terminées en PDF"* (session sur l'export de tâches TaskFlow). Similarités cosinus mesurées contre cette génération pour 3 requêtes :

| Requête | Similarité | Résultat (seuil 0.72) |
|---|---|---|
| Texte identique à la génération d'origine | 1.0000 | retenu |
| *"Générer un rapport PDF de mon espace de travail"* (même thème, formulation différente) | 0.7876 | retenu |
| *"Ajouter un mode sombre à l'interface"* (thème sans rapport) | 0.6312 | exclu |

**Point d'incertitude découvert en le testant** : `nomic-embed-text` a un plancher de similarité élevé sur des phrases courtes en français — le seuil initial (0.5) ne discriminait rien (0.63 pour une requête sans rapport passait déjà). Recalibré à 0.72 sur cette seule mesure ; à réajuster avec l'usage réel une fois plus de générations `accepted` accumulées (une distribution à un seul point n'est pas une calibration statistique fiable).

## RAG — kill switch (`RAG_ENABLED=False`), vérifié (23/07/2026)

**Question posée** : `RAG_ENABLED=True` par défaut, jamais testé explicitement en sens inverse — que se passe-t-il si on le désactive en urgence (Ollama down, embeddings corrompus, résultat few-shot non pertinent...) ? Le pipeline revient-il proprement au zero-shot ?

**Réponse vérifiée par test** (`tests/test_session_service.py::TestRAGKillSwitch`, 2 tests — module 2 et module 3) : oui, sur les trois points qui comptent.
1. **Aucune exception** : le pipeline termine normalement (`pending_requirement_review`/`pending_test_review`), pas de statut `_failed`.
2. **Prompt strictement identique à avant l'ajout du RAG** : `examples=[]`, aucun bloc "Exemples..." injecté.
3. **L'embedder n'est plus sollicité DU TOUT** — pas juste ignoré. Vérifié avec un embedder factice qui lève une exception au moindre appel (`_ExplodingEmbedder`) : les deux tests passent, donc `embedder.embed()` n'est jamais invoqué quand `RAG_ENABLED=False`, ni pour la récupération d'exemples ni pour le stockage. Important en pratique : un embedder qui plante (Ollama down, modèle absent) est justement une raison plausible de couper le RAG — le kill switch garantit qu'on arrête de le solliciter, pas seulement qu'on ignore ce qu'il renvoie. Ceci vient du fait que `run_requirement_analysis`/`run_test_design` gardent CHAQUE appel à `rag_service` (récupération ET stockage) derrière `if settings.RAG_ENABLED:` — pas de logique côté `rag_service.py` lui-même, tout le court-circuit est côté appelant.
4. Les embeddings déjà stockés en base (d'une période où RAG était actif) restent inertes : jamais relus, jamais un problème, puisque `get_few_shot_examples` n'est simplement jamais appelée.

**Limite opérationnelle réelle, à connaître avant un vrai incident** : `RAG_ENABLED` est lu depuis `.env` via `pydantic-settings`, chargé **une seule fois au démarrage du process** (`Settings()` singleton, cf. `app/config.py`) — éditer `.env` ne suffit pas, **il faut redémarrer le backend** pour que le changement prenne effet (déjà documenté pour `GITLAB_*`/`OLLAMA_MODEL`, même contrainte ici). En urgence réelle, ce n'est donc pas un vrai "flip" instantané : c'est édition `.env` + redémarrage (quelques secondes avec `--reload`, mais pas zéro). Aucun mécanisme de rechargement à chaud n'existe actuellement — à envisager seulement si ce délai devient un vrai problème opérationnel (hors scope tant que ce reste un prototype).

## RAG — amorçage (backfill) depuis des documents historiques (23/07/2026)

**Demande** : le RAG ne se remplit qu'au fil de l'usage normal (accept_requirement/accept_tests) — l'ingénieur veut aussi pouvoir l'amorcer avec des documents existants (exports Confluence en PDF, anciens tickets Jira, user stories archivées) plutôt que d'attendre que l'historique s'accumule organiquement.

**Solution** : `backend/scripts/seed_rag_from_documents.py` — script autonome (pas d'endpoint HTTP, opération d'administration hors ligne, cohérent avec la règle "routers HTTP minces" de CLAUDE.md). Pour chaque fichier d'un dossier donné (`.txt`/`.md`/`.pdf`/`.docx`/`.xlsx`/`.pptx`, réutilise l'extraction déjà existante d'`attachment_service.py` — voir `extract_document_text`, extrait de `process_attachment` pour être appelable hors du cycle de vie d'une pièce jointe) :

1. Extraction de texte, puis appel **réel** à `RequirementAnalyzerAgent.analyze()` (module 2) pour structurer le document en `RequirementAnalysis`.
2. Par défaut (`--stage full`), enchaîne avec `TestDesignAgent.design()` (module 3) sur le résultat pour aussi peupler le pool `test_design`.
3. Chaque résultat est stocké directement en `decision="accepted"` (un ticket/user story historique déjà livré vaut acceptation — pas de revue étape par étape ici) avec son embedding, via les mêmes fonctions que le pipeline live (`rag_service.store_embedding`, aucune logique dupliquée).

**Décision de conception notable** : pas de vraie `SessionModel` créée par document importé. `GenerationModel.session_id` n'est volontairement pas une clé étrangère SQLAlchemy dans ce projet (cf. `app/models/session.py`), et `rag_service` ne fait jamais de jointure vers `SessionModel` — un identifiant synthétique (`seed-<uuid>`) suffit comme repère traçable dans les logs. En créer une vraie aurait silencieusement faussé les analytics (module 7, `analytics_service.get_summary` lit **toutes** les sessions sans filtre) : `total_sessions`, `acceptance_rate`, et surtout `average_pipeline_duration_seconds` (quasi nul pour une acceptation immédiate) auraient été pollués sans qu'aucun test existant ne le détecte.

**Limites assumées** (best-effort, jamais bloquant, cf. docstring du script pour le détail) : un fichier = un document (pas de parseur pour un export CSV/JSON Jira multi-tickets) ; un document très volumineux peut dépasser la fenêtre de contexte du LLM local ou timeout — échoue proprement, fichier suivant, jamais d'arrêt du lot entier ; coût réel de 1 à 2 appels LLM locaux par fichier (`--stage full`), donc plusieurs minutes pour quelques dizaines de documents avec `llama3.2:3b`.

**Tests** : `tests/test_seed_rag_script.py` (5 tests, providers factices/DB en mémoire — succès complet, mode `requirement` seul, échec d'extraction, échec d'analyse module 2, échec partiel module 3 avec conservation du `requirement` déjà réussi).

**Vérification réelle (23/07/2026)** : voir `docs/module_3_test_design_agent.md`, section correspondante, pour le résultat du smoke-test sur 2 documents fictifs (une page Confluence et un ticket Jira).

## DLP — vérification manuelle (21/07/2026, Ollama réel)

Entrée : *"Suite au retour de Marie Dupont (marie.dupont@exemple-fictif.fr, 06 12 34 56 78) chez [FICTIF] Client X SA à Lyon, je veux ajouter un export PDF..."*

Texte réellement envoyé au LLM (masqué) : *"Suite au retour de `<PERSON>` (`<EMAIL_ADDRESS>`, `<PHONE_NUMBER>`) chez [FICTIF] Client X SA à `<LOCATION>`, je veux..."*

Résultat : le `RequirementAnalysis` généré ne contient ni le nom, ni l'email, ni le téléphone, ni "Lyon" — confirmé en inspectant le JSON complet, pas seulement les champs affichés. **Limite observée** : "Client X SA" (nom d'organisation stylisé, avec un "X" en guise de placeholder) n'a **pas** été détecté comme `ORGANIZATION` par le NER français — reste en clair. Le DLP par NER a un rappel imparfait, surtout sur des noms d'entreprise atypiques ou tronqués ; à traiter comme une couche de défense en profondeur, pas une garantie absolue (ne remplace pas une revue humaine avant d'envoyer des données réellement sensibles).

## Pièces jointes — vérification manuelle (21/07/2026, Ollama + moondream réels)

Session créée avec 2 pièces jointes réelles : une image PNG fictive imitant une capture d'écran d'erreur (« [FICTIF] Erreur 500 : Le paiement a échoué », un bouton « Réessayer ») et un PDF fictif (« Cahier des charges - FICTIF / Le bouton export doit générer un fichier CSV »).

- **Extraction PDF** : exacte, texte intégralement récupéré (`pypdf`).
- **Description d'image (`moondream`)** : reconnaît la nature de l'image (capture d'écran, message d'erreur en rouge/blanc, présence d'un bouton, texte en français) mais **ne transcrit pas le texte précis affiché** (pas d'OCR fiable — n'a pas restitué "Erreur 500" ni le code de transaction). Limite attendue d'un si petit modèle vision (~1,7 Go, choisi pour tenir sur un GPU 4 Go) : bon pour une description de scène générale, pas pour lire du texte à l'écran.
- **Effet sur l'analyse** : le contenu du PDF a concrètement influencé le `RequirementAnalysis` généré (le "bouton export" et la "génération de CSV" apparaissent dans `functional_criteria`/`description`) — preuve que le contexte enrichi atteint bien le LLM. La description d'image, plus vague, a apporté moins de signal exploitable dans ce cas précis.

**Recommandation d'usage découverte en testant** : pour un bug avec un message d'erreur précis à l'écran, décrire aussi ce message dans le texte de la demande (`user_input`) plutôt que de compter uniquement sur la description d'image — `moondream` situe le contexte visuel mais ne remplace pas une transcription humaine du texte affiché.

## Hypothèses

- Le format de sortie reprend celui déjà esquissé dans le mock du module 1 (voir ci-dessus) — **à confirmer** si un format différent est attendu côté Jira/GitLab.
- `confidence_score` est un signal indicatif pour prioriser la relecture humaine, pas une garantie de qualité.
- Pas de gestion d'historique de conversation multi-tours pour l'instant : `context` est un champ texte libre optionnel, pas une vraie mémoire de session (à revoir au module 4 si le chat doit se souvenir des échanges précédents).

## Exemple d'exécution

```python
import asyncio
from app.agents.llm_layer import get_llm_provider
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.schemas.requirement import RequirementAnalysisRequest

async def demo():
    # MockProvider : pas besoin d'Ollama pour cette démo
    provider = get_llm_provider("mock")
    agent = RequirementAnalyzerAgent(llm_provider=provider)

    request = RequirementAnalysisRequest(
        user_input="Ajouter un paiement QR code dans l'app [FICTIF] ShopEasy",
    )
    analysis = await agent.analyze(request)

    print(f"Titre: {analysis.title}")
    print(f"Type: {analysis.type}")
    print(f"Priorité: {analysis.priority}")
    print(f"Ambiguïtés: {analysis.ambiguities}")
    print(f"Confiance: {analysis.confidence_score}")

asyncio.run(demo())
```

## Lancer les tests

```bash
cd backend
..\..\venv\Scripts\activate   # venv partagé, situé à la racine de Projets/
pytest tests/test_requirement_analyzer.py -v
```

13 tests : schémas Pydantic, 5 demandes fictives (feature, bug, improvement, ambiguïté), gestion de contexte, retry sur JSON invalide, échec après épuisement des tentatives.

## Module 8 — instruction libre de régénération ("guidance")

Depuis le 22/07/2026, `POST /requirement/regenerate` accepte un corps optionnel `{"guidance": "..."}` (cf. `ReviewActions.tsx` côté frontend, bouton "Préciser avant de régénérer"). Le texte est concaténé au `context` envoyé pour CET appel uniquement (`session_service.run_requirement_analysis(..., guidance=...)`), donc masqué par le DLP au même titre que le reste — **jamais stocké** sur `session.context`. Voir `tests/test_session_service.py::TestRegenerationGuidance`.

## Module 9 — transcript de conversation

Depuis le 22/07/2026, une session passe par une phase de clarification conversationnelle (statut `chatting`, cf. `docs/module_9_chat.md`) AVANT le tout premier appel à ce module. Si l'ingénieur a discuté avant de cliquer "Générer l'analyse" (`POST .../chat/finalize`), le transcript complet est concaténé au `context` envoyé ici — même mécanisme que le `guidance` ci-dessus, un seul point d'entrée masqué par le DLP.

## Points d'incertitude

1. **Format de sortie** : repris du mock du module 1 par cohérence — à valider si le format cible pour Jira/GitLab diffère (ex: champs supplémentaires attendus par l'Integration Layer du module 5).
2. **Type `"other"`** : catégorie fourre-tout ajoutée pour les demandes qui ne sont ni feature, ni bug, ni improvement (ex: idée vague). À affiner si une autre taxonomie est souhaitée.
3. **Stratégie de retry** : le nombre de tentatives (`LLM_MAX_RETRIES`, actuellement 2) est celui déjà configuré dans le module 1. Testé avec un vrai LLM le 19/07/2026 (Ollama, `llama3.1:8b`) : analyse d'une feature 2FA en ~60 s, JSON valide du premier coup (aucun retry nécessaire), sortie fidèle au texte (points flous bien dans `ambiguities`, pas d'invention). La branche retry elle-même n'a donc pas encore été exercée contre un vrai LLM — seulement en test avec des providers fictifs.
4. **Description d'image imprécise sur le texte affiché** — `moondream` (modèle vision par défaut) décrit correctement la nature d'une image mais ne transcrit pas fiablement du texte à l'écran (pas d'OCR robuste). Un modèle plus gros (`llava`) donnerait de meilleures descriptions mais risquerait la même instabilité GPU que `llama3.1:8b` avant son remplacement par `llama3.2:3b` (cf. `docs/module_1_llm_layer.md`). À réévaluer si le GPU disponible change.
5. **Pas de retraitement des pièces jointes sur regenerate** — une pièce jointe déjà extraite/décrite garde son résultat même si l'ingénieur régénère l'analyse plusieurs fois (évite de rappeler le modèle vision inutilement) ; si l'extraction avait échoué une première fois pour une raison transitoire (Ollama temporairement down), elle ne sera pas retentée automatiquement. À revoir si ce cas s'avère fréquent en usage réel.
