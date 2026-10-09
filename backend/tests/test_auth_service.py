"""
Tests unitaires — Module 8 : persistance utilisateur + cookie de session
(app/services/auth_service.py)

Toutes les données sont fictives.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.oidc import AuthenticatedUser
from app.config import settings
from app.services import auth_service


class TestUpsertUser:
    @pytest.mark.asyncio
    async def test_creates_new_user(self, db_session):
        authenticated = AuthenticatedUser(
            email="assignee@example.com", display_name="[FICTIF] Assignée", subject="sub-1"
        )

        user = await auth_service.upsert_user(db_session, authenticated)

        assert user.id is not None
        assert user.email == "assignee@example.com"
        assert user.display_name == "[FICTIF] Assignée"
        assert user.oidc_subject == "sub-1"

    @pytest.mark.asyncio
    async def test_second_login_updates_existing_user_not_duplicates(self, db_session):
        first = AuthenticatedUser(email="assignee@example.com", display_name="Ancien Nom", subject="sub-1")
        second = AuthenticatedUser(email="assignee@example.com", display_name="Nouveau Nom", subject="sub-1")

        user1 = await auth_service.upsert_user(db_session, first)
        user2 = await auth_service.upsert_user(db_session, second)

        assert user1.id == user2.id  # même compte, pas de doublon
        assert user2.display_name == "Nouveau Nom"

    @pytest.mark.asyncio
    async def test_last_login_at_is_updated(self, db_session):
        authenticated = AuthenticatedUser(email="assignee@example.com", display_name="A", subject="sub-1")
        user1 = await auth_service.upsert_user(db_session, authenticated)
        first_login = user1.last_login_at

        user2 = await auth_service.upsert_user(db_session, authenticated)

        assert user2.last_login_at >= first_login


class TestSessionToken:
    def test_round_trip(self):
        token = auth_service.create_session_token("fictional-user-id")

        user_id = auth_service.decode_session_token(token)

        assert user_id == "fictional-user-id"

    def test_invalid_signature_raises(self):
        token = jwt.encode(
            {"sub": "fictional-user-id", "exp": datetime.now(timezone.utc) + timedelta(days=1)},
            "wrong-secret-but-still-long-enough-for-hmac",
            algorithm="HS256",
        )

        with pytest.raises(auth_service.SessionTokenError):
            auth_service.decode_session_token(token)

    def test_expired_token_raises(self):
        token = jwt.encode(
            {
                "sub": "fictional-user-id",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
            settings.AUTH_SESSION_SECRET,
            algorithm="HS256",
        )

        with pytest.raises(auth_service.SessionTokenError):
            auth_service.decode_session_token(token)

    def test_missing_subject_raises(self):
        token = jwt.encode(
            {"exp": datetime.now(timezone.utc) + timedelta(days=1)},
            settings.AUTH_SESSION_SECRET,
            algorithm="HS256",
        )

        with pytest.raises(auth_service.SessionTokenError):
            auth_service.decode_session_token(token)

    def test_garbage_token_raises(self):
        with pytest.raises(auth_service.SessionTokenError):
            auth_service.decode_session_token("pas-un-jwt-valide")
