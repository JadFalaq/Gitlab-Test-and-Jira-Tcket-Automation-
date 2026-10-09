# Module 8 — Authentification SSO & multi-utilisateur

## Rôle

Fait passer le prototype d'un usage mono-poste (une seule base locale, aucune notion de compte) à un usage multi-assignés : chaque personne se connecte via le SSO de l'entreprise (Google Workspace ou Microsoft Entra ID) et ne voit/n'actionne que ses propres sessions (module 4). Tous les comptes partagent le même compte de service Jira (module 5, `settings.JIRA_*`) — il n'existe aucun lien compte SSO ↔ compte Jira individuel.

## Architecture

```
Backend
  app/models/session.py          — UserModel (nouvelle table `users`), SessionModel.user_id
  app/auth/oidc.py               — BaseAuthProvider (ABC), OidcAuthProvider (générique), AuthenticatedUser
  app/services/auth_service.py   — upsert_user (persistance), create_session_token/decode_session_token (JWT)
  app/routers/auth.py            — GET /login, GET /callback, GET /me, POST /logout
  app/dependencies.py            — get_current_user (garde de route), get_auth_provider_dependency

Frontend
  src/api.ts                     — loginUrl()/me()/logout(), credentials:"include" sur chaque appel
  src/App.tsx                    — écran de login si non authentifié, sinon interface normale
```

## Flow (Authorization Code OIDC)

```
1. GET /api/auth/login
   -> génère un état anti-CSRF, le pose en cookie httponly (10 min), redirige vers le fournisseur SSO

2. Le fournisseur authentifie l'utilisateur, redirige vers AUTH_REDIRECT_URI avec ?code&state

3. GET /api/auth/callback?code&state
   -> vérifie state == cookie oauth_state (sinon redirection frontend avec ?auth_error=...)
   -> échange le code contre un access_token (POST token_endpoint)
   -> récupère l'identité via userinfo_endpoint (email, name, sub)
   -> upsert UserModel par email
   -> pose un cookie `session_token` (JWT, HS256, settings.AUTH_SESSION_SECRET, 7 jours par défaut)
   -> redirige vers settings.FRONTEND_URL

4. Chaque requête API suivante envoie le cookie `session_token` (credentials: "include" côté
   frontend) -> get_current_user() le décode, charge UserModel, l'injecte dans les routers protégés
```

Le document de découverte `{issuer}/.well-known/openid-configuration` est le même mécanisme standard chez Google Workspace et Microsoft Entra ID : **une seule implémentation** (`OidcAuthProvider`) suffit, pilotée uniquement par `AUTH_OIDC_ISSUER`/`AUTH_OIDC_CLIENT_ID`/`AUTH_OIDC_CLIENT_SECRET` (`.env`) — pas de code spécifique à un fournisseur.

## Ownership des sessions

`session_service._get_or_404(db, session_id, user_id)` filtre désormais `WHERE id = ? AND user_id = ?` : une session existante mais appartenant à un autre assigné lève `SessionNotFoundError` (404), jamais un 403 — pour ne pas révéler son existence, même logique que `attachment_service.get_attachment` pour les pièces jointes cross-session (module 2).

Les fonctions exécutées en tâche de fond (`run_requirement_analysis`, `run_test_design`) utilisent `_get_by_id_or_404` (sans filtre) : l'ownership a déjà été vérifiée une fois, par la requête HTTP de kickoff qui a programmé la tâche — inutile de la repropager jusque dans `BackgroundTasks`.

**Le suivi est filtré aussi** : `/api/tracking/tickets`, `/api/tracking/sync` et le flux SSE `/api/tracking/stream` (module 6) ne renvoient que les tickets des sessions de l'utilisateur connecté.

**Seule exception : `/api/analytics/summary`** (module 7) reste global à l'équipe. Il ne renvoie que des compteurs agrégés (nombre de sessions, taux d'acceptation...), aucun contenu de session.

Tout cela est vérifié de bout en bout par `tests/test_ownership.py` : deux comptes réels (cookies de session), et le second reçoit 404 ou une liste vide sur chaque endpoint de session, de pièce jointe, de tickets et de suivi.

## Endpoints

| Méthode | Route | Action |
|---|---|---|
| `GET` | `/api/auth/login` | Redirige vers le fournisseur SSO |
| `GET` | `/api/auth/callback` | Reçoit le retour du fournisseur, pose le cookie de session |
| `GET` | `/api/auth/me` | Identité de l'utilisateur connecté (401 si non authentifié) |
| `POST` | `/api/auth/logout` | Efface le cookie de session |
| `POST` | `/api/auth/dev-login` | Connexion de secours sans SSO réel (voir ci-dessous) |

Toutes les routes de `app/routers/sessions.py`, `tickets.py`, `tracking.py`, `analytics.py` exigent désormais `Depends(get_current_user)` (401 sans cookie valide).

## Configuration réelle — Microsoft Entra ID (Microsoft 365)

L'entreprise utilise Microsoft 365 : le fournisseur est Microsoft Entra ID. Ce qui suit est **100% côté portail Microsoft** (aucune de ces étapes n'est du code) — l'app est déjà prête à recevoir les 3 valeurs une fois obtenues.

### 1. Enregistrer l'application (donne les 3 valeurs pour `.env`)

1. [entra.microsoft.com](https://entra.microsoft.com) (ou portail Azure) → **Identity > Applications > App registrations > New registration**.
2. Nom : ex. `Test Automation Agent`.
3. **Supported account types** : "Comptes dans cet annuaire organisationnel uniquement" (single tenant — réservé à l'entreprise, pas de comptes personnels/externes).
4. **Redirect URI** : plateforme **Web** (pas SPA — ce backend échange le code lui-même avec un client secret, flow confidentiel), valeur `http://localhost:8000/api/auth/callback` (+ l'URL réelle de prod le jour où elle existe).
5. Une fois créée, note sur la page "Overview" :
   - **Application (client) ID** → `AUTH_OIDC_CLIENT_ID`
   - **Directory (tenant) ID** → sert à construire `AUTH_OIDC_ISSUER` : `https://login.microsoftonline.com/<tenant-id>/v2.0`
6. **Certificates & secrets > New client secret** → copie la **valeur** immédiatement (affichée une seule fois) → `AUTH_OIDC_CLIENT_SECRET`.
7. **Token configuration > Add optional claim** → type ID, coche `email` — sans ça, certains comptes ne renvoient pas d'email exploitable et `fetch_user()` lèvera `AuthProviderError("...aucun email...")` (cf. `app/auth/oidc.py`). API permissions par défaut (`User.Read`, `openid`, `profile`, `email`) suffisent, pas de consentement admin supplémentaire nécessaire pour une simple connexion.

Une fois les 3 valeurs en main, les renseigner dans `backend/.env` (jamais commitées) : `POST /api/auth/dev-login` se désactive automatiquement dès que `AUTH_OIDC_ISSUER` est renseigné (et n'existe jamais en `APP_ENV=prod`).

### 2. Rendre l'Authenticator obligatoire, sans mot de passe

C'est une politique **au niveau du tenant** (Microsoft 365 entier), pas propre à cette app — Entra ne laisse pas une application individuelle dicter la méthode de connexion, mais une Conditional Access policy peut être **scopée à cette seule app** si tu ne veux pas l'imposer partout tout de suite.

1. **Entra admin center > Protection > Authentication methods > Policies** → active "Microsoft Authenticator", puis dans ses paramètres active spécifiquement **"Passwordless sign-in"** (notification push depuis le téléphone, sans mot de passe) pour les utilisateurs/groupes concernés.
2. Pour **interdire le mot de passe en repli** (pas juste "autoriser l'authenticator en plus") : **Protection > Conditional Access > New policy** →
   - Assignments > Target resources : soit "All cloud apps" (tout le tenant), soit **cette app précisément** (recherche `Test Automation Agent` dans la liste, une fois enregistrée à l'étape 1) — plus prudent pour commencer sans impacter le reste de Microsoft 365.
   - Grant : **"Require authentication strength"** → choisir la valeur pré-définie **"Passwordless MFA"** (n'accepte que Authenticator passwordless / clé de sécurité / Windows Hello — jamais mot de passe seul).
3. Chaque utilisateur doit avoir l'app Microsoft Authenticator installée et enregistrée sur son téléphone (**Mon compte > Sécurité > Ajouter une méthode**) avant sa première connexion — sinon Entra le forcera à l'enrôlement au premier login (normal, à prévoir dans la communication à l'équipe).

Aucune de ces deux étapes ne nécessite de changement dans ce dépôt — l'app délègue entièrement l'expérience de connexion au tenant via OIDC (`get_authorization_url`, cf. `app/auth/oidc.py`) : quoi que le tenant impose, cette app le respecte telle quelle.

## Connexion de développement sans SSO réel

`POST /api/auth/dev-login` (`{"email": "...", "display_name": "..."}`) crée/authentifie un compte sans passer par un vrai fournisseur SSO — ajoutée le 22/07/2026 car sans elle, l'application est totalement inaccessible tant qu'aucun tenant Google Workspace/Entra n'est configuré (le multi-utilisateur ne peut littéralement pas être essayé autrement). L'écran de login l'affiche quand `GET /api/auth/config` renvoie `dev_login_enabled: true`.

**Disponible uniquement si `APP_ENV` vaut `dev` (ou `test` pour pytest) ET qu'`AUTH_OIDC_ISSUER` est vide** (`settings.dev_login_enabled`). Sinon elle répond 404 — en particulier **toujours en `APP_ENV=prod`**, où le SSO est de toute façon obligatoire au démarrage. ⚠️ Aucune vérification d'identité tant qu'elle est active : n'importe qui atteignant le backend peut se connecter comme n'importe quel email.

## Choix techniques et simplifications connues

1. **OIDC générique plutôt que SDK propriétaire Google/Microsoft** — une seule implémentation via le document de découverte standard, pas de dépendance ajoutée (`httpx` suffit, déjà utilisé pour Ollama/Jira).
2. **`id_token` toujours vérifié** — signature RS256 avec les clés publiques publiées par le fournisseur (`jwks_uri` du document de découverte), `iss` = issuer découvert, `aud` = notre `client_id`, `exp`/`iat` requis (60 s de tolérance d'horloge). Un token non signé (`alg=none`), signé par une autre clé, destiné à une autre application ou expiré est refusé. Le `sub` vérifié fait foi ; `/userinfo` complète l'email et le nom (Entra ID ne met pas toujours l'email dans l'`id_token`) et doit renvoyer le même `sub`. Couvert par `tests/test_auth_oidc.py::TestIdTokenVerification`.
3. **Cookie de session signé maison (PyJWT + `AUTH_SESSION_SECRET`)**, pas de session store côté serveur (Redis, etc.) — cohérent avec le reste du prototype (SQLite, pas d'infra supplémentaire). Les cookies sont `httponly` + `samesite=lax`, et `secure` en `APP_ENV=prod` (HTTPS obligatoire).
4. **`AUTH_SESSION_SECRET` a une valeur par défaut de dev** (`dev-insecure-secret-change-me-...`) — **doit être changée en `.env`** avant tout déploiement partagé, sinon n'importe qui peut forger un cookie de session valide. Pas de garde-fou automatique qui bloquerait un démarrage avec la valeur par défaut (à ajouter si ce prototype passe en production réelle).
5. **Upsert par email** (pas par `sub` OIDC) — plus intuitif si l'entreprise change un jour de fournisseur SSO en gardant les mêmes adresses email ; `oidc_subject` est conservé en repère secondaire mais n'est pas la clé d'upsert.
6. **Pas de rôles/permissions** — tout utilisateur authentifié a les mêmes droits (ses propres sessions + les vues d'équipe partagées). Pas de notion d'admin.
7. **Rupture de compatibilité DB** — `SessionModel.user_id` est une colonne `NOT NULL` sur une table déjà existante ; `Base.metadata.create_all` (module 4) ne l'ajoute pas rétroactivement. `test_automation.db` a été sauvegardé (`test_automation.db.bak-pre-auth`) puis recréé vide — toute session créée avant le 22/07/2026 a été perdue localement (prototype, pas de vraie donnée).

## Lancer les tests

```bash
cd backend
pytest tests/test_auth_oidc.py tests/test_auth_service.py tests/test_auth_router.py -v
```

`OidcAuthProvider` est testé via `httpx.MockTransport` (même pattern que `test_vision_layer.py`/`test_ticket_providers.py`) — aucun appel réseau réel, aucune dépendance à un vrai tenant Google/Microsoft.

## Points d'incertitude

1. **Jamais testé contre un vrai tenant Google Workspace ou Microsoft Entra ID** — la couverture est exhaustive côté logique (discovery/échange de code/userinfo/erreurs), mais aucune vérification end-to-end avec un vrai fournisseur n'a pu être faite (pas de tenant d'entreprise disponible dans cet environnement). À faire dès qu'un compte SSO d'entreprise est configuré : renseigner `AUTH_OIDC_ISSUER`/`AUTH_OIDC_CLIENT_ID`/`AUTH_OIDC_CLIENT_SECRET` dans `backend/.env`, enregistrer `http://localhost:8000/api/auth/callback` comme URI de redirection côté fournisseur, puis suivre le flow réel dans un navigateur.
2. **PKCE non implémenté** — flow "Authorization Code" simple, adapté à un client confidentiel (secret côté serveur). Standard recommandé pour des clients publics (SPA sans backend), pas strictement nécessaire ici puisque le frontend ne voit jamais le code d'autorisation ni le secret.
3. **Pas de révocation de session côté serveur** — `logout()` efface seulement le cookie côté client ; un token JWT déjà émis reste valide jusqu'à son expiration (7 jours par défaut) même après logout. Acceptable pour un prototype, à revoir (liste de révocation, ou sessions plus courtes + refresh) avant un usage sensible.
4. **`AUTH_SESSION_MAX_AGE_SECONDS` = 7 jours par défaut** — pas de renouvellement automatique (pas de refresh token géré) : l'utilisateur doit se reconnecter après expiration.
