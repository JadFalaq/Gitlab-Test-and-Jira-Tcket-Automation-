# Gitlab & Jira Ticket Automation

Un outil qui lit une demande écrite en langage normal (« je veux ajouter un paiement par QR code ») et qui en ressort, tout seul :

1. une fiche propre qui décrit le besoin (titre, description, critères...),
2. des scénarios de test au format Gherkin (`Given / When / Then`) + des user stories,
3. de vrais tickets créés sur Jira et/ou GitLab.

À chaque étape, un humain doit relire et cliquer sur **Valider**, **Rejeter** ou **Régénérer** — l'outil ne publie jamais rien tout seul.

Le tout tourne avec un modèle d'IA **100 % local** (via [Ollama](https://ollama.com)) : aucune donnée n'est envoyée à une API externe.

---

## En une image

```
Toi : "Je veux ajouter un paiement par QR code"
        │
        ▼
  💬 Chat de clarification (l'IA pose des questions si besoin)
        │
        ▼
  📋 Fiche de besoin structurée  ──► tu valides / tu régénères
        │
        ▼
  🧪 Scénarios de test Gherkin + user stories ──► tu valides / tu régénères
        │
        ▼
  🎫 Tickets créés automatiquement sur Jira et GitLab
        │
        ▼
  📡 Suivi en direct du statut des tickets + tableau de statistiques
```

---

## Le projet est coupé en deux grandes parties

```
backend/    → le "cerveau" : reçoit les demandes, parle à l'IA, crée les tickets
frontend/   → l'écran que tu vois et avec lequel tu cliques
```

### `backend/` — le cerveau (Python)

C'est un serveur qui fait tout le travail. Il est lui-même découpé en petites pièces, chacune avec un seul travail précis :

| Dossier / fichier | À quoi ça sert, en simple |
|---|---|
| `app/agents/` | Les "personnages" qui parlent à l'IA : un qui discute avec toi (le chat), un qui transforme ta demande en fiche structurée, un qui invente les scénarios de test, un qui décrit une image que tu uploades, un qui anonymise les infos perso avant de les envoyer à l'IA. |
| `app/schemas/` | Les "moules" qui décrivent à quoi doit ressembler chaque donnée (une fiche de besoin, un scénario de test, un ticket...) — ça évite que l'IA renvoie n'importe quoi. |
| `app/services/` | Le chef d'orchestre : il fait avancer une demande étape par étape (brouillon → analyse → tests → tickets), et sait quoi faire si une étape rate. |
| `app/routers/` | La "réception" : les adresses (URLs) que le site web appelle pour parler au serveur (créer une demande, valider une étape, voir les tickets...). |
| `app/integrations/` | Les branchements vers Jira et GitLab (et une version "factice" utilisée pour les tests, qui ne contacte jamais les vrais sites). |
| `app/models/` | Comment tout ça est rangé dans la base de données. |
| `docs/` | Un carnet de bord détaillé, un fichier par grande fonctionnalité : comment ça marche, pourquoi ce choix technique, ce qui a été testé en vrai, et ce qui reste fragile. |
| `scripts/` | Des petits outils à lancer à la main, par exemple pour "nourrir" la mémoire de l'IA avec d'anciens tickets/documents. |
| `tests/` | Des centaines de mini-vérifications automatiques qui prouvent que chaque pièce fonctionne encore après chaque changement. |

### `frontend/` — l'écran (React + TypeScript)

| Dossier / fichier | À quoi ça sert, en simple |
|---|---|
| `src/App.tsx` | La page principale, qui décide quoi afficher selon où tu en es. |
| `src/components/` | Chaque bloc visuel : la fenêtre de chat (`ChatThread`), la fiche de besoin (`RequirementPanel`), les scénarios générés (`TestDesignPanel`, `GherkinFeatureView`), les boutons valider/rejeter (`ReviewActions`), la liste des tickets (`TicketPanel`), le tableau en direct (`TrackingDashboard`), les statistiques (`AnalyticsDashboard`), l'historique de tes demandes (`SessionHistory`), l'écran de connexion (`LoginScreen`). |
| `src/api.ts` | Le "téléphone" qui appelle le backend. |
| `src/types.ts` | La version TypeScript des mêmes "moules" que côté backend, pour que les deux se comprennent. |

---

## Le voyage complet d'une demande

1. **Tu écris ta demande** dans le chat. L'IA peut te poser des questions pour clarifier avant de continuer — elle ne se précipite pas sur un résultat.
2. **Anonymisation automatique** : si ton texte contient un nom, un email, un téléphone..., c'est masqué avant d'être envoyé à l'IA (protection des données personnelles).
3. **Analyse du besoin** : l'IA transforme ta demande en fiche structurée (titre, description, critères, priorité). Si un point n'est pas clair, elle le note comme "ambigu" au lieu d'inventer une réponse.
4. **Tu valides** cette fiche (ou tu demandes une nouvelle version).
5. **Génération des tests** : l'IA écrit des user stories et des scénarios Gherkin (cas normal, cas d'erreur, cas limite), vérifiés avec un vrai outil de validation Gherkin — jamais de format inventé.
6. **Tu valides** ces tests.
7. **Création des tickets** : un ticket Jira et/ou GitLab est créé automatiquement pour chaque user story, avec les scénarios de test dedans.
8. **Suivi en direct** : un tableau affiche l'évolution des tickets sans avoir à recharger la page.
9. **Mémoire qui s'améliore avec le temps** : chaque fois que tu valides quelque chose, ça sert d'exemple pour les prochaines demandes similaires (l'IA s'améliore avec l'usage, sans jamais copier le contenu d'une ancienne demande dans une nouvelle).

---

## Ce qui a été construit

- Un pipeline complet en 9 étapes (chat → analyse → tests → validation → tickets → suivi → statistiques → connexion par compte → mémoire des anciennes demandes).
- Une IA qui tourne entièrement en local (aucune donnée envoyée à une API cloud).
- Une couche qui anonymise automatiquement les données personnelles avant tout envoi à l'IA.
- La possibilité d'uploader des documents (PDF, Word, Excel, images...) pour enrichir une demande.
- La création réelle de tickets sur Jira et GitLab (pas juste une simulation).
- Un système de connexion par compte, pour que chaque personne ne voie que ses propres demandes.
- Une suite de centaines de tests automatiques qui vérifient que rien ne casse à chaque changement.

## Comment le lancer chez toi

### Prérequis
- Python 3.11+
- Node.js 18+
- [Ollama](https://ollama.com) installé, avec un modèle téléchargé (ex : `ollama pull llama3.2:3b`)

### Backend
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Note sur les données

Toutes les données d'exemple utilisées pendant le développement sont **fictives**. Aucune vraie clé API n'est présente dans ce dépôt.
