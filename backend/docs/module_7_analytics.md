# Module 7 — Analytics

## Rôle

Vue d'ensemble du pipeline complet (modules 2 à 5) : volumes, taux d'acceptation, régénérations, couverture de test, tickets créés. Lecture seule, calculée à la volée depuis la table `sessions`.

## Architecture

```
Backend
  app/schemas/analytics.py       — AnalyticsSummary + sous-schémas de breakdown
  app/services/analytics_service.py — compute_summary() (pur, testable sans DB) + get_summary() (DB)
  app/routers/analytics.py       — GET /api/analytics/summary

Frontend
  src/components/AnalyticsDashboard.tsx — cartes de stats + barres de répartition (pas de lib de chart)
```

## Métriques exposées (`AnalyticsSummary`)

- `total_sessions`, répartition par statut / type / priorité
- `average_confidence_score` — moyenne de `confidence_score` (module 2)
- `average_scenarios_per_session` — moyenne de `test_summary.total_scenarios` (module 3)
- `total_requirement_regenerations` / `total_test_regenerations` — compteurs incrémentés par `session_service.regenerate_requirement`/`regenerate_tests` (module 4, étendu)
- `completed_sessions` (`validated` + `tickets_created`) / `rejected_sessions` (`requirement_rejected` + `tests_rejected`) / `acceptance_rate`
- `total_tickets_created`, répartition par provider (module 5)
- `average_pipeline_duration_seconds` — moyenne de `completed_at - created_at` pour les sessions terminées (`completed_at` figé à la validation, cf. Choix techniques #6)

## Choix techniques

1. **Pas de table dédiée** — tout est recalculé à la volée depuis `sessions` à chaque appel de `GET /api/analytics/summary`. Cohérent avec l'échelle prototype (mono-utilisateur, volume de sessions faible) ; à revoir avec une table d'agrégats pré-calculés si le volume grandit (cf. absence de pagination déjà notée au module 4).
2. **Compteurs de régénération stockés en DB** (`SessionModel.requirement_regenerations`/`test_regenerations`) plutôt que recalculés — l'historique des régénérations n'est conservé nulle part ailleurs (`regenerate` écrase l'analyse/les tests précédents, module 4).
3. **Taux en `Optional[float]`** — `None` distingue explicitement "pas encore de donnée" (dénominateur nul) de "0%", pour que le frontend affiche "—" plutôt qu'un pourcentage trompeur.
4. **Pas de librairie de graphique côté frontend** — barres de répartition en CSS pur (`div` avec `width` en %), cohérent avec le principe "un fichier, dépendances minimales" du reste du frontend (`package.json` ne liste que React/Tailwind).
5. **`compute_summary` est une fonction pure** (`list[SessionModel] -> AnalyticsSummary`), séparée de l'accès DB (`get_summary`) — testable sans base de données, cf. `tests/test_analytics_service.py`.
6. **`completed_at` figé à la validation** (module 4, `accept_tests`) — corrige la dérive de `average_pipeline_duration_seconds` : `updated_at` continue de changer à chaque sync de tickets (module 6), `completed_at` non. Sessions sans `completed_at` (créées avant l'ajout du champ) exclues proprement de la moyenne plutôt que de crasher.

## Lancer les tests

```bash
cd backend
pytest tests/test_analytics_service.py -v
```

## Points d'incertitude

1. ~~**`average_pipeline_duration_seconds` imprécis après création de tickets**~~ — **résolu (21/07/2026)** : `SessionModel.completed_at` est désormais figé une seule fois par `accept_tests`, indépendant des syncs de tickets ultérieurs. Voir `tests/test_analytics_service.py::test_pipeline_duration_ignores_updated_at_drift`.
2. **Pas de fenêtre temporelle** — `AnalyticsSummary` porte sur l'historique complet, pas de filtre "cette semaine"/"ce mois". À ajouter si le volume de sessions grandit.
3. **`GenerationModel` (module 4) non encore exploité en analytics** — le taux d'acceptation par génération (`accepted`/`rejected`/`failed`, distinct de `acceptance_rate` qui porte sur les sessions) pourrait devenir une métrique utile une fois le RAG/DPO branché dessus.
