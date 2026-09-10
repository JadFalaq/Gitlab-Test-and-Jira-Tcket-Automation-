"""Module 8 — Authentification SSO : schémas de réponse/requête utilisateur."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    """Identité de l'utilisateur connecté — renvoyée par GET /api/auth/me."""

    id: str
    email: str
    display_name: str
    last_login_at: datetime

    model_config = {"from_attributes": True}


class DevLoginRequest(BaseModel):
    """
    Corps de POST /api/auth/dev-login — connexion de secours SANS SSO réel,
    utilisable uniquement tant qu'aucun fournisseur OIDC n'est configuré
    (cf. app/routers/auth.py, se désactive automatiquement sinon).
    """

    email: str = Field(..., min_length=3, description="Pas de vérification de format — usage dev uniquement")
    display_name: Optional[str] = Field(default=None, description="Défaut : l'email")


__all__ = ["UserResponse", "DevLoginRequest"]
