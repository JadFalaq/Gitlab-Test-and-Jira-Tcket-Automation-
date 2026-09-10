"""
Module 8 — Authentification SSO : persistance utilisateur + cookie de session.

Deux responsabilités distinctes, à ne pas confondre :
    - `upsert_user` : matérialise en base l'identité renvoyée par le
      fournisseur OIDC (app/agents/auth_layer.py) après un login réussi.
    - `create_session_token` / `decode_session_token` : le cookie de session
      *applicatif* (JWT signé avec settings.AUTH_SESSION_SECRET), distinct
      des tokens du fournisseur SSO lui-même (jamais stockés côté serveur
      au-delà de l'appel /userinfo, cf. auth_layer.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.auth_layer import AuthenticatedUser
from app.config import settings
from app.models.session import UserModel

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"


class SessionTokenError(Exception):
    """Cookie de session absent, expiré ou falsifié."""


async def upsert_user(db: AsyncSession, authenticated: AuthenticatedUser) -> UserModel:
    """
    Crée le compte au premier login, ou met à jour le nom affiché/la date de
    dernière connexion sinon. Upsert par email — cf. UserModel.oidc_subject
    pour la justification du repère secondaire.
    """
    result = await db.execute(select(UserModel).where(UserModel.email == authenticated.email))
    user = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if user is None:
        user = UserModel(
            email=authenticated.email,
            display_name=authenticated.display_name,
            oidc_subject=authenticated.subject,
            last_login_at=now,
        )
        db.add(user)
        logger.info("auth: nouveau compte créé pour %s", authenticated.email)
    else:
        user.display_name = authenticated.display_name
        user.oidc_subject = authenticated.subject
        user.last_login_at = now

    await db.commit()
    await db.refresh(user)
    return user


def create_session_token(user_id: str) -> str:
    """JWT signé (HS256) porté par le cookie `session_token` — payload minimal
    (subject + expiration), aucune donnée sensible embarquée."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=settings.AUTH_SESSION_MAX_AGE_SECONDS),
    }
    return jwt.encode(payload, settings.AUTH_SESSION_SECRET, algorithm=_JWT_ALGORITHM)


def decode_session_token(token: str) -> str:
    """Retourne le user_id porté par le token. Lève SessionTokenError si le
    cookie est absent/invalide/expiré (jamais d'exception jwt.* qui fuiterait
    hors de cette couche)."""
    try:
        payload = jwt.decode(token, settings.AUTH_SESSION_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise SessionTokenError(f"Cookie de session invalide : {exc}") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise SessionTokenError("Cookie de session sans identifiant utilisateur")
    return user_id


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[UserModel]:
    return await db.get(UserModel, user_id)


__all__ = [
    "SessionTokenError",
    "upsert_user",
    "create_session_token",
    "decode_session_token",
    "get_user_by_id",
]
