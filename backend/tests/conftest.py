"""
Fixtures partagées — DB SQLite en mémoire pour les tests du module 4.

Une base par test (StaticPool + connexion partagée) pour isoler les tests
entre eux sans toucher au fichier configuré dans .env.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.integrations import factory
from app.models.session import Base
from tests.fakes.tickets import FakeJiraProvider


@pytest.fixture(autouse=True)
def _disable_dlp_masking_in_tests(monkeypatch):
    """
    DLP (Presidio + spaCy, cf. app/agents/dlp_masking.py) est désactivé dans
    toute la suite de tests : les assertions basées sur des mots-clés
    (FakeLLMProvider.complete cherche "paiement"/"requirement analyzer agent"
    dans le texte) doivent rester déterministes, et le chargement du modèle
    spaCy (~1-2s) ne doit pas alourdir chaque run. Le comportement réel
    (DLP_MASK_ENABLED=true par défaut, cf. app/config.py) est couvert par
    des tests dédiés qui instancient PresidioMaskingProvider directement
    (tests/test_dlp_masking.py), en dehors de ce garde-fou.
    """
    monkeypatch.setattr(settings, "DLP_MASK_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_rag_in_tests(monkeypatch):
    """
    RAG (embeddings Ollama, cf. app/agents/embedding_layer.py) est désactivé
    dans toute la suite de tests : aucun test ne doit dépendre d'un Ollama
    réel avec le modèle d'embedding tiré. Le comportement réel
    (RAG_ENABLED=true par défaut) est couvert par des tests dédiés qui
    injectent un embedder déterministe (tests/test_rag_service.py), en
    dehors de ce garde-fou.
    """
    monkeypatch.setattr(settings, "RAG_ENABLED", False)


@pytest.fixture(autouse=True)
def _disable_vision_in_tests(monkeypatch):
    """
    Vision (description d'image, cf. app/agents/vision_layer.py) est
    désactivée dans toute la suite de tests : aucun test ne doit dépendre
    d'un Ollama réel avec le modèle vision (moondream) tiré. Le
    comportement réel (VISION_ENABLED=true par défaut) est couvert par des
    tests dédiés qui instancient OllamaVisionProvider directement via
    httpx.MockTransport (tests/test_vision_layer.py), en dehors de ce
    garde-fou.
    """
    monkeypatch.setattr(settings, "VISION_ENABLED", False)


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch):
    """APP_ENV=test pour toute la suite, quoi que contienne backend/.env."""
    monkeypatch.setattr(settings, "APP_ENV", "test")


@pytest.fixture(autouse=True)
def _disable_real_jira_in_tests(monkeypatch):
    """
    Efface la config Jira pour toute la suite de tests, quoi que contienne le
    backend/.env réel du poste de développement : aucun test ne doit
    contacter le vrai Jira (incident du 21/07/2026 : 2 tests avaient tapé la
    vraie API Jira après configuration d'un compte sandbox). Les tests de
    JiraProvider passent leurs paramètres explicitement et utilisent
    httpx.MockTransport.
    """
    for name in ("JIRA_BASE_URL", "JIRA_API_TOKEN", "JIRA_USER_EMAIL"):
        monkeypatch.setattr(settings, name, None)


@pytest.fixture(autouse=True)
def fake_jira(monkeypatch) -> FakeJiraProvider:
    """
    Remplace le provider de tickets par un faux Jira en mémoire (une
    instance neuve par test). Les tests le reçoivent en paramètre pour le
    régler (`configured`, `fail_creation`) ou inspecter ce qui a été créé.
    """
    provider = FakeJiraProvider()
    monkeypatch.setattr(factory, "get_ticket_provider", provider.factory)
    return provider


@pytest.fixture(autouse=True)
def _disable_real_sso_in_tests(monkeypatch):
    """
    Force AUTH_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET à None pour toute la
    suite de tests, quoi que contienne le backend/.env réel du poste de
    développement — même garde-fou que _disable_real_jira_in_tests
    (découvert avec Jira le 21/07/2026) : sans ça, un poste où le
    SSO a été configuré verrait POST /api/auth/dev-login se désactiver (404)
    de façon non-déterministe selon la machine qui lance `pytest`.
    """
    for name in ("AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET"):
        monkeypatch.setattr(settings, name, None)


@pytest_asyncio.fixture
async def db_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_sessionmaker):
    async with db_sessionmaker() as session:
        yield session
