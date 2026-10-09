# Frontend — Jira Ticket Automation

Interface React + TypeScript (Vite, Tailwind) du projet : chat de clarification, validation des analyses et des scénarios Gherkin, création et suivi des tickets Jira, statistiques.

## Démarrage

```bash
npm install
npm run dev        # http://localhost:5173, backend attendu sur http://localhost:8000
```

Pour un backend ailleurs, copier `.env.example` en `.env.local` et ajuster `VITE_API_BASE_URL`. En déploiement derrière un reverse proxy (frontend et `/api` sur le même domaine), laisser `VITE_API_BASE_URL` vide.

## Scripts

| Commande | Rôle |
|---|---|
| `npm run dev` | Serveur de développement |
| `npm run build` | Type-check (`tsc -b`) puis build de production dans `dist/` |
| `npm run lint` | Lint (`oxlint`) |
| `npm run preview` | Sert le build de production localement |

## Structure

| Fichier | Rôle |
|---|---|
| `src/App.tsx` | Page principale : enchaînement des étapes d'une session |
| `src/api.ts` | Appels au backend (cookies de session inclus) |
| `src/types.ts` | Miroir TypeScript des schémas Pydantic du backend |
| `src/components/` | Écrans : chat, analyse, tests Gherkin, validation, tickets, suivi, statistiques, connexion |

Le texte Gherkin affiché est rendu par le backend (`SessionResponse.gherkin_feature_files`) : le frontend ne réimplémente aucune logique métier.
