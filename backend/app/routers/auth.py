"""
Module 8 — Authentification SSO : routes HTTP.

Flow standard "Authorization Code" :
    GET /login    -> redirige vers le fournisseur SSO (état anti-CSRF en cookie)
    GET /callback -> échange le code, upsert l'utilisateur, pose le cookie
                     de session applicatif, redirige vers le frontend
    GET /me       -> identité de l'utilisateur connecté (garde de route)
    POST /logout  -> efface le cookie de session

Erreurs de logique métier : pas de try/except ici pour la persistance
(mêmes règles que app/routers/sessions.py) ; les erreurs du fournisseur SSO
(AuthProviderError) sont, elles, gérées explicitement plus bas — l'appelant
arrive par redirection navigateur depuis un site externe, pas par un appel
API, donc l'erreur doit renvoyer vers le frontend plutôt qu'un JSON brut.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import AuthenticatedUser, AuthProviderError, BaseAuthProvider
from app.config import settings
from app.database import get_db
from app.dependencies import SESSION_COOKIE_NAME, get_auth_provider_dependency, get_current_user
from app.models.session import UserModel
from app.schemas.user import DevLoginRequest, UserResponse
from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "oauth_state"


def _cookie_flags() -> dict:
    """Attributs communs à tous les cookies d'auth : httponly + samesite=lax
    partout, `secure` en production (HTTPS obligatoire, le cookie n'est
    jamais envoyé en clair)."""
    return {"httponly": True, "samesite": "lax", "secure": settings.is_production}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=settings.AUTH_SESSION_MAX_AGE_SECONDS, path="/", **_cookie_flags()
    )


@router.get("/config")
async def auth_config() -> dict:
    """
    Capacité publique (pas de garde d'authentification, rien de sensible) :
    indique au frontend si un vrai SSO est configuré, pour afficher soit le
    bouton SSO soit directement la connexion de développement en premier
    plan plutôt que de faire cliquer sur un bouton voué à échouer (503) —
    cf. LoginScreen.tsx.
    """
    return {
        "sso_configured": bool(settings.AUTH_OIDC_ISSUER),
        "dev_login_enabled": settings.dev_login_enabled,
    }


@router.get("/login")
async def login(provider: BaseAuthProvider = Depends(get_auth_provider_dependency)) -> RedirectResponse:
    """Démarre le login SSO : génère un état anti-CSRF, le pose en cookie
    httponly de courte durée, redirige vers le fournisseur."""
    state = secrets.token_urlsafe(24)
    try:
        authorization_url = await provider.get_authorization_url(state, settings.AUTH_REDIRECT_URI)
    except AuthProviderError as exc:
        logger.warning("auth: login impossible : %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(_OAUTH_STATE_COOKIE, state, max_age=600, path="/api/auth", **_cookie_flags())
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
    provider: BaseAuthProvider = Depends(get_auth_provider_dependency),
) -> RedirectResponse:
    """
    Reçoit la redirection du fournisseur SSO. Toute erreur (état invalide,
    échec d'échange de code, fournisseur injoignable) redirige vers le
    frontend avec `?auth_error=...` plutôt que de renvoyer un JSON brut —
    l'utilisateur arrive ici par navigation de navigateur, pas par un appel API.
    """
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not expected_state or expected_state != state:
        logger.warning("auth: état OAuth invalide ou absent (CSRF potentiel)")
        return _redirect_with_error("Session de connexion expirée, réessaie.")

    try:
        authenticated = await provider.fetch_user(code, settings.AUTH_REDIRECT_URI)
    except AuthProviderError as exc:
        logger.warning("auth: échec du login SSO : %s", exc)
        return _redirect_with_error(str(exc))

    user = await auth_service.upsert_user(db, authenticated)
    token = auth_service.create_session_token(user.id)

    response = RedirectResponse(settings.FRONTEND_URL, status_code=302)
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/auth", **_cookie_flags())
    _set_session_cookie(response, token)
    return response


def _redirect_with_error(message: str) -> RedirectResponse:
    query = urlencode({"auth_error": message})
    return RedirectResponse(f"{settings.FRONTEND_URL}/?{query}", status_code=302)


@router.post("/dev-login")
async def dev_login(payload: DevLoginRequest, db: AsyncSession = Depends(get_db)) -> Response:
    """
    Connexion de secours SANS SSO réel — uniquement en APP_ENV=dev/test et
    tant qu'aucun fournisseur OIDC n'est configuré (cf.
    settings.dev_login_enabled, docs/module_8_auth.md). Répond 404 partout
    ailleurs, en particulier toujours en production.

    ⚠️ Aucune vérification d'identité : n'importe qui atteignant ce endpoint
    peut se connecter comme n'importe quel email.
    """
    if not settings.dev_login_enabled:
        raise HTTPException(status_code=404, detail="Connexion de développement désactivée")

    authenticated = AuthenticatedUser(
        email=payload.email,
        display_name=payload.display_name or payload.email,
        subject=f"dev:{payload.email}",
    )
    user = await auth_service.upsert_user(db, authenticated)
    token = auth_service.create_session_token(user.id)

    response = JSONResponse(UserResponse.model_validate(user).model_dump(mode="json"))
    _set_session_cookie(response, token)
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: UserModel = Depends(get_current_user)) -> UserModel:
    return user


@router.post("/logout")
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", **_cookie_flags())
    return response
