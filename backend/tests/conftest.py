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
from app.models.session import Base


@pytest.fixture(autouse=True)
def _disable_dlp_masking_in_tests(monkeypatch):
    """
    DLP (Presidio + spaCy, cf. app/agents/dlp_masking.py) est désactivé dans
    toute la suite de tests : les assertions basées sur des mots-clés
    (MockProvider.complete cherche "paiement"/"requirement analyzer agent"
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
def _disable_real_ticket_providers_in_tests(monkeypatch):
    """
    Force Jira/GitLab à "non configuré" pour toute la suite de tests, quoi
    que contienne le backend/.env réel du poste de développement.

    Sans ce garde-fou, dès que backend/.env contient de vrais identifiants
    Jira/GitLab (cas réel : un ingénieur configure le prototype contre son
    propre compte pour l'utiliser), deux choses se produisent silencieusement :
    1. app/integrations/factory.py::_jira_configured() (et son équivalent
       GitLab) devient vrai, donc get_ticket_provider_by_name("jira") renvoie
       le VRAI JiraProvider au lieu de MockTicketProvider dans les tests qui
       passent par create_tickets(providers=["jira"]) — appels réseau réels
       pendant `pytest`, contraire à la règle "jamais d'appel réseau réel
       dans un test" (CLAUDE.md).
    2. JiraProvider(base_url=None, ...) (construction directe, cf.
       test_create_ticket_missing_config_raises) retombe sur
       `base_url or settings.JIRA_BASE_URL` — si settings.JIRA_BASE_URL est
       renseigné, passer explicitement None au constructeur ne suffit plus à
       simuler une config absente.
    Découvert le 21/07/2026 en configurant un vrai compte Jira sandbox pour
    l'utilisateur : 2 tests ont silencieusement tapé la vraie API Jira.
    """
    for name in (
        "JIRA_BASE_URL",
        "JIRA_API_TOKEN",
        "JIRA_USER_EMAIL",
        "GITLAB_BASE_URL",
        "GITLAB_PRIVATE_TOKEN",
        "GITLAB_PROJECT_ID",
    ):
        monkeypatch.setattr(settings, name, None)


@pytest.fixture(autouse=True)
def _disable_real_sso_in_tests(monkeypatch):
    """
    Force AUTH_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET à None pour toute la
    suite de tests, quoi que contienne le backend/.env réel du poste de
    développement — même garde-fou que _disable_real_ticket_providers_in_tests
    ci-dessus (découvert avec Jira le 21/07/2026) : sans ça, un poste où le
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
