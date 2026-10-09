"""
Dépendances FastAPI injectables — factories par défaut pour les agents.

Séparées du reste pour que les tests puissent surcharger
`app.dependency_overrides[get_requirement_analyzer]` / `[get_test_design_agent]`
avec des agents utilisant un faux LLM (tests/fakes/llm.py), sans toucher au code des routers.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import BaseAuthProvider, get_auth_provider
from app.agents.chat_agent import ChatAgent
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.database import get_db
from app.models.session import UserModel
from app.services import auth_service

SESSION_COOKIE_NAME = "session_token"


def get_requirement_analyzer() -> RequirementAnalyzerAgent:
    return RequirementAnalyzerAgent()


def get_test_design_agent() -> TestDesignAgent:
    return TestDesignAgent()


def get_chat_agent() -> ChatAgent:
    return ChatAgent()


def get_auth_provider_dependency() -> BaseAuthProvider:
    """Ré-exporte get_auth_provider (app/auth/oidc.py) comme
    dépendance FastAPI — surchargeable en test comme les deux ci-dessus,
    sans toucher au code de app/routers/auth.py."""
    return get_auth_provider()


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> UserModel:
    """
    Module 8 — dépendance de protection des routes multi-utilisateur : lit
    le cookie de session applicatif (JWT, cf. auth_service.py), le valide et
    charge l'utilisateur correspondant. 401 si absent/invalide/expiré ou si
    le compte a été supprimé depuis l'émission du cookie.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Non authentifié — connecte-toi via /api/auth/login")

    try:
        user_id = auth_service.decode_session_token(token)
    except auth_service.SessionTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Compte introuvable — reconnecte-toi")
    return user
