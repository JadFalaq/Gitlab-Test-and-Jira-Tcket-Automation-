# Backend — Agent de Génération de Tests & Tickets Jira

FastAPI (Python). Chaque module du pipeline est développé et testé isolément — voir `docs/` pour la documentation détaillée de chaque module.

## Démarrage rapide

```bash
cd backend
..\..\venv\Scripts\activate   # venv partagé, situé à la racine de Projets/
pip install -r requirements-dev.txt         # production (requirements.txt) + outils de test
python -m spacy download fr_core_news_lg   # modèle DLP (module 2), une seule fois
ollama pull nomic-embed-text               # modèle d'embedding RAG (module 2), une seule fois
ollama pull moondream                      # modèle vision pour les pièces jointes (module 2), une seule fois
cp .env.example .env          # Ajuster si nécessaire
uvicorn app.main:app --reload
pytest -v                     # Lance les tests de tous les modules
```

## Modules

| # | Module | Statut | Doc |
|---|--------|--------|-----|
| 1 | LLM Abstraction Layer | ✅ Terminé | [docs/module_1_llm_layer.md](docs/module_1_llm_layer.md) |
| 2 | Requirement Analyzer Agent | ✅ Terminé | [docs/module_2_requirement_analyzer.md](docs/module_2_requirement_analyzer.md) |
| 3 | Test Design Agent | ✅ Terminé | [docs/module_3_test_design_agent.md](docs/module_3_test_design_agent.md) |
| 4 | Interface de validation (minimale) | ✅ Terminé | [docs/module_4_validation_interface.md](docs/module_4_validation_interface.md) |
| 5 | Intégration Jira | ✅ Terminé (GitLab retiré) | [docs/module_5_jira_integration.md](docs/module_5_jira_integration.md) |
| 6 | Suivi temps réel | ✅ Terminé | [docs/module_6_realtime_tracking.md](docs/module_6_realtime_tracking.md) |
| 7 | Analytics | ✅ Terminé | [docs/module_7_analytics.md](docs/module_7_analytics.md) |
| 8 | Authentification SSO & multi-utilisateur | ✅ Terminé (SSO réel non testé, cf. doc) | [docs/module_8_auth.md](docs/module_8_auth.md) |
| 9 | Chat de clarification | ✅ Terminé (jamais testé avec un vrai LLM, cf. doc) | [docs/module_9_chat.md](docs/module_9_chat.md) |

## Données

⚠️ Toutes les données utilisées sont **fictives et anonymisées**. Aucune clé API réelle n'est commitée.
