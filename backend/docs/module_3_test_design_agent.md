# Module 3 — Test Design Agent

## Rôle

Transforme un `RequirementAnalysis` (sortie du module 2) en artefacts de test : user stories, features Gherkin (`.feature`), résumé de couverture. S'appuie sur le module 1 (LLM Abstraction Layer) — aucun appel LLM direct.

## Architecture

```
TestDesignAgent
    .design(RequirementAnalysis) -> TestDesignOutput
        1. Construit un LLMRequest (system prompt + RequirementAnalysis sérialisé)
        2. Appelle provider.complete() (module 1)
        3. Parse + valide le JSON contre TestDesignOutput
        4. Rend chaque GherkinFeature en texte .feature standard et valide
           sa syntaxe avec le parseur officiel Cucumber (gherkin_renderer.py)
        5. Si JSON invalide OU Gherkin syntaxiquement invalide : retry avec
           message correctif (jusqu'à LLM_MAX_RETRIES fois)
        6. Si toujours invalide : lève TestDesignError
```

**Point clé** : le LLM ne génère jamais de texte Gherkin brut. Il produit du JSON structuré (`GherkinFeature`/`GherkinScenario`/`GherkinStep`, avec `keyword` contraint à `Given`/`When`/`Then`/`And`/`But`), que `gherkin_renderer.py` rend ensuite en syntaxe Gherkin standard de façon déterministe. Ça respecte la règle du projet « ne jamais supposer un format Gherkin custom » — le rendu est fixé par le code, pas par le LLM.

## Schémas d'interface

| Schéma | Direction | Champs principaux |
|--------|-----------|-------------------|
| `RequirementAnalysis` (module 2) | Input | réutilisé tel quel, pas d'enveloppe supplémentaire |
| `TestDesignOutput` | Output | `user_stories`, `gherkin_features`, `test_summary` |
| `UserStory` | Sub | `title`, `as_a`, `i_want`, `so_that`, `acceptance_criteria`, `priority` |
| `GherkinFeature` | Sub | `feature_name`, `description`, `tags`, `scenarios` |
| `GherkinScenario` | Sub | `name`, `tags`, `steps` |
| `GherkinStep` | Sub | `keyword` (Given/When/Then/And/But), `text` |
| `TestSummary` | Sub | `total_scenarios`, `coverage_notes`, `suggested_test_types` |

## Choix techniques

1. **JSON structuré, jamais de Gherkin brut généré par le LLM** — évite toute dérive de syntaxe ; le rendu textuel est 100% déterministe côté code.
2. **Validation avec le parseur officiel Cucumber** (`gherkin-official`, PyPI) — pas de validation "maison" : on utilise directement le même parseur que Cucumber/Gherkin, garantissant une conformité réelle à la spec plutôt qu'une approximation regex.
3. **Retry unifié JSON + Gherkin** — la même boucle de retry (héritée du pattern du module 2) couvre à la fois un JSON invalide et un Gherkin syntaxiquement invalide (ex: un step dont le texte contient un saut de ligne), avec le même message correctif renvoyé au LLM.
4. **Pas d'endpoint HTTP pour l'instant** — comme les modules 1 et 2, testable isolément via pytest ; le routage arrive avec le module 4.

## Limite connue du validateur

Le parseur Gherkin valide la **syntaxe** (structure Feature/Scenario/Step), pas la **sémantique**. Un scénario dont le premier step est `And` au lieu de `Given` est syntaxiquement valide pour le parseur, même si ce n'est pas l'usage recommandé. Le system prompt du LLM guide vers l'ordre `Given → When → Then` mais rien ne l'impose techniquement — au premier test réel (19/07/2026, `llama3.1:8b`, 3 scénarios), l'ordre a été respecté sur tous les scénarios ; à re-surveiller sur des demandes plus complexes.

## Exemple d'exécution

```python
import asyncio
from app.agents.llm_layer import get_llm_provider
from app.agents.test_design_agent import TestDesignAgent
from app.agents.gherkin_renderer import render_gherkin_feature
from app.schemas.requirement import RequirementAnalysis

async def demo():
    provider = get_llm_provider("mock")
    agent = TestDesignAgent(llm_provider=provider)

    requirement = RequirementAnalysis(
        title="[FICTIF] Paiement par QR Code",
        type="feature",
        description="Ajout d'un paiement par scan de QR code dans l'app ShopEasy.",
        functional_criteria=["Scanner un QR code", "Afficher le montant avant confirmation"],
        priority="high",
        confidence_score=0.82,
    )

    output = await agent.design(requirement)

    print(f"{len(output.user_stories)} user story(ies)")
    print(f"{len(output.gherkin_features)} feature(s)")
    for feature in output.gherkin_features:
        print(render_gherkin_feature(feature))

asyncio.run(demo())
```

## Lancer les tests

```bash
cd backend
..\..\venv\Scripts\activate   # venv partagé, situé à la racine de Projets/
pytest tests/test_gherkin_renderer.py tests/test_test_design_agent.py -v
```

16 tests initiaux + 7 ajoutés le 22/07/2026 (règles de couverture du system prompt, plafond de tokens dédié, relance corrective sur couverture mince — déclenchée une fois puis acceptée telle quelle, jamais bloquante) + 3 ajoutés le 23/07/2026 (`TestFewShotExamples` — injection RAG, cf. section dédiée) : schémas Pydantic, rendu Gherkin (avec/sans tags), validation syntaxique (valide, invalide, step cassé), cas nominal de l'agent, retry sur JSON invalide, retry sur Gherkin invalide, échec après épuisement des tentatives, couverture, exemples few-shot.

## Module 8 — instruction libre de régénération ("guidance")

Depuis le 22/07/2026, `TestDesignAgent.design(requirement, guidance=...)` accepte une instruction libre optionnelle (cf. `POST /tests/regenerate` avec `{"guidance": "..."}`), injectée dans le message utilisateur envoyé au LLM et rappelée dans le system prompt (règle 4 : "applique-la en priorité... mais reste dans le périmètre de l'analyse fournie"). Jamais stockée — propre à l'appel en cours. Voir `tests/test_test_design_agent.py::TestTestDesignAgentNominal::test_design_with_guidance_includes_it_in_the_prompt`.

## Couverture renforcée + relance corrective (22/07/2026)

**Demande** : améliorer la qualité/couverture des tests générés (pas seulement le chemin nominal).

**Changements** :
1. System prompt étendu (règle 4/5) : pour chaque critère fonctionnel qui s'y prête, générer un scénario `@happy-path`, un `@negative` (mode d'échec identifiable) et un `@edge-case` (valeur limite, action répétée...) ; les critères non-fonctionnels vérifiables deviennent des scénarios `@non-functional`. Échappatoire anti-hallucination explicite : ne jamais forcer un cas creux/artificiel, l'expliquer dans `coverage_notes` à la place.
2. `TestDesignAgent._coverage_feedback` : après une réponse JSON/Gherkin valide, si AUCUN scénario ne porte l'un des tags `@negative`/`@edge-case`/`@non-functional`, **une seule** relance corrective est envoyée au LLM avant d'accepter le résultat — jamais bloquant (contrairement à un JSON invalide), best-effort.
3. `TEST_DESIGN_MAX_TOKENS` (6144, séparé de `LLM_DEFAULT_MAX_TOKENS`) : une sortie plus riche a besoin de plus de tokens.

**Vérification réelle (22/07/2026, `llama3.2:3b`)** — résultat honnête, pas entièrement concluant :
- 1er appel (`requirement/accept`) : 2 scénarios générés, **aucun tag de couverture** (`@payment`, `@qr_code`, `@transaction`, `@user` — tags libres, pas ceux demandés). La relance corrective ne s'est pas déclenchée ici car le test manuel a été fait avant le déploiement du correctif ci-dessous.
- 2e appel (`tests/regenerate`, correctif actif) : la relance corrective s'est bien déclenchée (temps de génération allongé, `coverage_notes` a changé pour mentionner explicitement "les scénarios de test doivent couvrir les cas d'erreur") — **mais le modèle a reformulé la note plutôt que de générer un scénario réellement tagué `@negative`**. Toujours aucun tag de couverture dans le résultat final.

**Conclusion honnête** : le mécanisme logiciel (prompt + relance corrective) fonctionne correctement — vérifié à la fois par tests unitaires (`TestCoverageRetry`, doubles déterministes) et en conditions réelles (la relance se déclenche bien). Mais `llama3.2:3b` (3B, local) ne respecte pas fiablement cette consigne même après relance : c'est une limite du modèle configuré, pas du code. Ne jamais annoncer une "meilleure couverture" comme acquise sans reformuler cette réserve. Piste non testée : `llama3.1:8b` (8B, déjà tiré localement) est probablement plus capable de suivre des règles multi-parties, mais présente un risque d'OOM documenté sur un GPU 4 Go (cf. `docs/module_1_llm_layer.md` / historique OLLAMA_MODEL).

## RAG few-shot étendu au module 3 (23/07/2026)

**Demande** : le RAG (module 2 uniquement jusqu'ici) devait aussi couvrir le module 3, pour que chaque conception de test **validée** par un ingénieur serve d'exemple few-shot aux générations futures — cf. `docs/module_2_requirement_analyzer.md` point 6 pour le mécanisme de base (générique, non dupliqué ici).

**Changements** :
1. `TestDesignAgent.design(requirement, guidance=None, examples=None)` — nouveau paramètre `examples` (liste de `TestDesignOutput` passés, sérialisés en dict), injecté par `_build_user_message` avec le même garde-fou anti-hallucination explicite que `RequirementAnalyzerAgent._build_user_message` ("n'en réutilise jamais le contenu"). Absent de `examples` → prompt strictement identique à avant (pas de régression sur les appels existants, cf. `TestFewShotExamples::test_no_examples_block_when_examples_empty`).
2. `session_service.run_test_design` — même câblage que `run_requirement_analysis` : avant génération, `rag_service.get_few_shot_examples(db, masked_query, embedder, stage="test_design")` ; après génération réussie, `rag_service.store_embedding(db, generation, masked_query, embedder)`. `rag_service.py` n'a pas changé (déjà générique sur `stage`).
3. **Texte de requête** : volontairement le `raw_input` de la session (masqué DLP), **pas** le `RequirementAnalysis` ni ses champs — identique à ce qui est déjà utilisé/masqué au module 2. Deux raisons : (a) pas de nouvelle logique de masquage à maintenir pour un type de texte différent ; (b) une conception de test validée reste indexée sur la demande d'origine, donc retrouvable par une future session dont la demande brute est similaire, indépendamment de la formulation exacte que le LLM a donnée à l'analyse structurée intermédiaire.
4. Génération 'acceptée' comme déclencheur de stockage utilisable au RAG : le stockage de l'embedding a lieu à CHAQUE génération réussie (comme au module 2, cf. point 6 du doc module 2 — le filtre `decision='accepted'` s'applique à la lecture dans `get_few_shot_examples`, pas à l'écriture) ; `accept_tests` (déjà existant, inchangé) est ce qui rend une génération éligible en lecture.

**Tests ajoutés** : `tests/test_test_design_agent.py::TestFewShotExamples` (3 tests, injection/absence/rétrocompatibilité — même structure que `test_requirement_analyzer.py::TestFewShotExamples`) et `tests/test_session_service.py::TestRAGFewShotIntegrationTestDesign` (2 tests bout en bout avec embedder factice déterministe — génération acceptée retrouvée, génération rejetée jamais retrouvée). Suite complète : 292/292 passés (0 régression).

**Vérification réelle (23/07/2026, Ollama réel — `llama3.2:3b` LLM + `nomic-embed-text` embeddings)** : pipeline complet exécuté hors mock (création → analyse → acceptation → conception de tests → acceptation) sur une session fictive ("réinitialiser mon mot de passe par email"). Résultat :
- Génération `test_design` acceptée, embedding réellement stocké en base (768 dimensions, cohérent avec `nomic-embed-text`).
- Similarité cosinus contre une reformulation proche ("pouvoir réinitialiser mon mot de passe si je l'ai oublié") : **0.9190** — au-dessus du seuil (0.72), la récupération fonctionnerait.
- Similarité cosinus contre une demande sans rapport ("exporter les statistiques de vente en PDF") : **0.7353** — **également au-dessus du seuil**, ce qui n'était pas souhaité.

**Constat honnête, pas encore résolu** : `RAG_MIN_SIMILARITY=0.72` (calibré au module 2 sur un point de mesure unique, cf. `config.py`) ne discrimine pas correctement ici — une demande complètement différente passe quand même le seuil. Cause probable : avec `nomic-embed-text`, des phrases courtes en français ont une similarité cosinus de base élevée entre elles (effet de "cône" documenté sur certains modèles d'embedding de phrases), donc un seuil unique et bas capture trop large. Conséquence concrète si le pool d'exemples grossit : le module 3 (et le module 2, qui partage le même mécanisme) risque d'injecter des exemples few-shot non pertinents, pas juste "aucun exemple" en cas d'échec — un risque plus insidieux que l'absence d'exemples, car silencieux. Cf. point d'incertitude #6 ci-dessous.

## Amorçage RAG depuis des documents historiques — vérification réelle (23/07/2026)

Voir `docs/module_2_requirement_analyzer.md`, section "RAG — amorçage (backfill) depuis des documents historiques", pour la conception de `scripts/seed_rag_from_documents.py`. Smoke-test réel (Ollama, `llama3.2:3b` + `nomic-embed-text`) sur 2 documents fictifs — une page Confluence ("[FICTIF] Export des factures au format PDF") et un ticket Jira ("[FICTIF] SCRUM-142 — Export des statistiques de vente en CSV"), `--stage full` :

- **Ticket Jira** : entièrement réussi — `requirement` ET `test_design` structurés et stockés (`decision="accepted"`, embedding 768 dimensions chacun). Vérifié directement en base (requête SQL sur `generations`, pas seulement les logs du script) et par relecture (`rag_service.get_few_shot_examples`) : une nouvelle requête similaire ("je veux pouvoir exporter mes statistiques de ventes en fichier CSV chaque mois") retrouve bien cet exemple aux deux stages.
- **Page Confluence** : **échec propre**, catégorisé `analysis_failed` — le script est passé au fichier suivant sans planter, aucune ligne partielle/corrompue écrite en base (le `db.commit()` n'a lieu qu'après un `analyzer.analyze()` réussi). Cause réelle observée dans les logs : `llama3.2:3b` a d'abord renvoyé un JSON tronqué (chaîne non terminée, `max_tokens` probablement atteint), puis — après 2 relances correctives — un JSON syntaxiquement valide mais avec **tous les champs requis vides** (`title`/`type`/`description`/`priority` = `""`), rejeté par la validation Pydantic de `RequirementAnalysis`. `RequirementAnalysisError` levée après épuisement des 3 tentatives, proprement remontée et catégorisée par le script.

**Conclusion honnête** : le mécanisme d'amorçage (extraction → DLP → agent → stockage) fonctionne correctement de bout en bout sur le cas qui réussit, et échoue proprement (jamais de corruption ni de crash du lot) sur le cas qui échoue. Mais ce deuxième cas est un nouveau mode d'échec réel de `llama3.2:3b`, distinct de ceux déjà documentés (couverture non taguée, tags libres non-Gherkin) : un JSON structurellement valide mais entièrement vide plutôt qu'une vraie tentative d'analyse. S'ajoute à la liste déjà connue des limites de ce modèle 3B local (cf. points d'incertitude #5 et #7 ci-dessous) — pas un problème du script ou du pipeline RAG lui-même.

## Module 8 — instruction libre de régénération ("guidance")

Depuis le 22/07/2026, `TestDesignAgent.design(requirement, guidance=...)` accepte une instruction libre optionnelle (cf. `POST /tests/regenerate` avec `{"guidance": "..."}`), injectée dans le message utilisateur envoyé au LLM et rappelée dans le system prompt (règle 4 : "applique-la en priorité... mais reste dans le périmètre de l'analyse fournie"). Jamais stockée — propre à l'appel en cours. Voir `tests/test_test_design_agent.py::TestTestDesignAgentNominal::test_design_with_guidance_includes_it_in_the_prompt`.

## Points d'incertitude

1. **Dépendance ajoutée** : `gherkin-official` (parseur Cucumber officiel) — pas mentionnée dans le brief initial, ajoutée pour satisfaire la règle « respecte la syntaxe Gherkin standard » de façon vérifiable plutôt qu'approximative. À valider que cette dépendance est acceptable.
2. **Ordre Given/When/Then non-imposé techniquement** (cf. section limite du validateur ci-dessus) — uniquement guidé par le prompt.
3. **Testé avec un vrai LLM le 19/07/2026** (Ollama, `llama3.1:8b`) : pipeline complet création → analyse (~60 s) → conception de tests (~268 s) → validation → tickets mock → tracking → analytics, sans erreur. ⚠️ La conception de tests a pris 268 s pour une feature simple, à 30 s du timeout (`OLLAMA_TIMEOUT_SECONDS=300`) — confirme l'urgence du passage des appels LLM en tâche de fond (cf. module 4, point d'incertitude #2) et le risque de timeout sur des demandes plus riches.
4. **Bug réel corrigé (22/07/2026)** : une session avec pièce jointe PDF volumineuse (module 2) a fait dériver `llama3.2:3b` — le JSON produit était structurellement valide, mais le champ `tags` d'une feature contenait du texte libre (fragment du contenu extrait du PDF, ex: `"PDF analyse extraite_informations"`) au lieu d'un tag Gherkin (`"@..."`). `gherkin_renderer.py` rend ce texte tel quel comme première ligne du fichier `.feature`, ce qui casse le parseur Cucumber avec une erreur peu exploitable (`expected: #FeatureLine, got 'PDF analyse'`) — épuisant les 3 tentatives sans que le petit modèle ne se corrige. Fix : `GherkinFeature.tags`/`GherkinScenario.tags` valident maintenant le format Gherkin (`^@\S+$`) au niveau Pydantic (cf. `app/schemas/test_design.py`), ce qui fait échouer le JSON plus tôt et alimente le retry existant avec un message ciblé au lieu d'atteindre le rendu `.feature`. À surveiller : le system prompt ne liste qu'un exemple (`"@string"`) pour illustrer le format attendu — si ce type de dérive redevient fréquent avec des contextes volumineux, renforcer explicitement la contrainte dans le prompt plutôt que de compter uniquement sur le retry.
5. **Couverture renforcée non fiablement suivie par `llama3.2:3b`** (cf. section dédiée ci-dessus) — le prompt + la relance corrective sont vérifiés fonctionnels mécaniquement, mais le modèle 3B actuellement configuré ne produit pas fiablement de scénarios `@negative`/`@edge-case`/`@non-functional` même après relance. Non résolu : soit accepter cette limite en prototype, soit tester `llama3.1:8b` pour ce module précisément (actuellement `OLLAMA_MODEL` est un réglage global partagé par tous les modules — un override par agent n'existe pas encore).
6. **`RAG_MIN_SIMILARITY=0.72` ne discrimine pas correctement avec `nomic-embed-text`** (cf. section "RAG few-shot étendu au module 3" ci-dessus, vérification réelle du 23/07/2026) — une demande sans rapport a mesuré 0.7353 de similarité cosinus contre une demande acceptée, au-dessus du seuil actuel. Ce seuil avait été calibré au module 2 sur un unique point de mesure (21/07/2026) ; il n'a jamais été validé avec un échantillon assez large pour distinguer fiablement "similaire" de "sans rapport". Risque concret : une fois le pool d'exemples `test_design`/`requirement` accepté suffisamment grand, le RAG pourrait injecter des exemples few-shot non pertinents plutôt que de simplement n'en fournir aucun — plus difficile à détecter qu'une absence d'exemples, car ça ne casse rien visiblement, ça dégrade juste silencieusement la qualité du prompt. Non résolu : nécessite soit de relever significativement le seuil (à calibrer sur un corpus réel de demandes similaires/différentes, pas un seul point), soit un score relatif (ex: garder seulement les candidats dans le top-K ET nettement au-dessus de la similarité moyenne du corpus) plutôt qu'un seuil absolu fixe.
7. **Nouveau mode d'échec réel de `llama3.2:3b` découvert en amorçant le RAG depuis un document historique** (cf. section "Amorçage RAG depuis des documents historiques" ci-dessus, 23/07/2026) : sur un document plus long/dense qu'une demande d'ingénieur typique, le modèle a produit un JSON structurellement valide mais avec TOUS les champs requis vides (`""`), après avoir d'abord tronqué sa première réponse. Rejeté proprement par la validation Pydantic existante (`RequirementAnalysisError` après 3 tentatives) — aucun risque de corruption de données, mais confirme qu'un document volumineux/dense reste un point faible réel de ce modèle 3B, pas juste une hypothèse théorique évoquée au point d'incertitude #3. Non résolu : aucune action prise (cohérent avec la limite déjà actée du modèle configuré) — à revoir si `llama3.1:8b` est un jour testé pour ce module.
