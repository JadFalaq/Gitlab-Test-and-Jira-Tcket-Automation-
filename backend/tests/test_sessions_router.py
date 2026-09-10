"""
Tests d'intégration — Module 4 : routes HTTP (app/routers/sessions.py)

Vérifie le câblage FastAPI de bout en bout : dependency injection (db +
agents), sérialisation des réponses, codes HTTP d'erreur (404, 409).
Les agents sont surchargés via app.dependency_overrides avec des
MockProvider pour rester déterministe et rapide.

Toutes les données sont fictives.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.agents.chat_agent import ChatAgent
from app.agents.llm_layer import MockProvider
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.database import get_db, get_session_factory
from app.dependencies import get_chat_agent, get_current_user, get_requirement_analyzer, get_test_design_agent
from app.main import app
from app.models.session import UserModel
from tests.test_session_service import (
    FICTIONAL_USER_INPUT,
    INVALID_JSON,
    VALID_REQUIREMENT_JSON,
    VALID_TEST_DESIGN_JSON,
)

import pytest
import pytest_asyncio

# Module 8 : utilisateur factice pour tous les tests HTTP de ce fichier — la
# vraie authentification SSO (app/routers/auth.py) est testée séparément
# (tests/test_auth_router.py), jamais ici (cf. CLAUDE.md : jamais d'appel
# réseau réel dans un test).
_FAKE_USER = UserModel(id="fictional-user-id", email="test@example.com", display_name="Test User")


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    async def _override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # Même factory que get_db (StaticPool = connexion unique partagée) : les
    # tâches de fond (BackgroundTasks) doivent écrire dans la DB de test,
    # jamais dans le fichier configuré par DATABASE_URL (cf. app/database.py).
    app.dependency_overrides[get_session_factory] = lambda: db_sessionmaker
    app.dependency_overrides[get_requirement_analyzer] = lambda: RequirementAnalyzerAgent(
        llm_provider=MockProvider(custom_responses={"paiement": VALID_REQUIREMENT_JSON})
    )
    app.dependency_overrides[get_test_design_agent] = lambda: TestDesignAgent(
        llm_provider=MockProvider(custom_responses={"paiement": VALID_TEST_DESIGN_JSON})
    )
    # Module 9 : jamais le get_chat_agent() par défaut dans un test — il lirait
    # le vrai settings.LLM_PROVIDER du poste (peut valoir "ollama" en local),
    # même risque que documenté pour Jira dans conftest.py.
    app.dependency_overrides[get_chat_agent] = lambda: ChatAgent(llm_provider=MockProvider())
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def _finalize_and_wait(client, session_id: str):
    """Module 9 : passe une session de 'chatting' à 'pending_requirement_review'
    via POST /chat/finalize — avec ASGITransport, la tâche de fond du module 2
    s'exécute avant le retour de l'appel (in-process), donc le GET qui suit
    reflète déjà le résultat (contrat de polling exploité par le frontend)."""
    finalize_resp = await client.post(f"/api/sessions/{session_id}/chat/finalize")
    assert finalize_resp.status_code == 200
    assert finalize_resp.json()["status"] == "analyzing"
    return await client.get(f"/api/sessions/{session_id}")


class TestSessionsRouterHappyPath:
    @pytest.mark.asyncio
    async def test_create_session_returns_201_chatting(self, client):
        """Module 9 : la session s'ouvre sur la phase de clarification
        conversationnelle, aucun appel LLM implicite au kickoff (voir
        test_finalize_chat_background_task_completes_and_is_pollable)."""
        response = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "chatting"
        assert body["requirement_analysis"] is None
        assert body["chat_history"][0]["content"] == FICTIONAL_USER_INPUT

    @pytest.mark.asyncio
    async def test_finalize_chat_background_task_completes_and_is_pollable(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        get_resp = await _finalize_and_wait(client, session_id)

        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "pending_requirement_review"
        assert get_resp.json()["requirement_analysis"]["title"] == "[FICTIF] Paiement par QR Code"

    @pytest.mark.asyncio
    async def test_full_pipeline_to_validated(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "chatting"
        await _finalize_and_wait(client, session_id)

        accept_req_resp = await client.post(f"/api/sessions/{session_id}/requirement/accept")
        assert accept_req_resp.status_code == 200
        assert accept_req_resp.json()["status"] == "designing_tests"

        # tests/accept exige 'pending_test_review' en DB : ne peut réussir que
        # si la tâche de fond du module 3 a bien tourné et committé avant ce
        # POST — ce test vérifie donc implicitement la bonne complétion.
        accept_tests_resp = await client.post(f"/api/sessions/{session_id}/tests/accept")
        assert accept_tests_resp.status_code == 200
        assert accept_tests_resp.json()["status"] == "validated"

    @pytest.mark.asyncio
    async def test_regenerate_requirement_background_failure_is_visible(self, client, monkeypatch):
        """Un échec LLM en tâche de fond (regenerate) ne doit jamais rester
        invisible : status='requirement_failed' + error_message doivent être
        lisibles via GET (règle 3 CLAUDE.md)."""
        from app.dependencies import get_requirement_analyzer

        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)

        app.dependency_overrides[get_requirement_analyzer] = lambda: RequirementAnalyzerAgent(
            llm_provider=MockProvider(custom_responses={"requirement analyzer agent": INVALID_JSON})
        )

        regen_resp = await client.post(f"/api/sessions/{session_id}/requirement/regenerate")
        assert regen_resp.status_code == 200
        assert regen_resp.json()["status"] == "analyzing"

        get_resp = await client.get(f"/api/sessions/{session_id}")
        assert get_resp.json()["status"] == "requirement_failed"
        assert get_resp.json()["error_message"] is not None

    @pytest.mark.asyncio
    async def test_get_session(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/sessions/{session_id}")

        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})

        list_resp = await client.get("/api/sessions")

        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 2

    @pytest.mark.asyncio
    async def test_reject_requirement(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)

        reject_resp = await client.post(f"/api/sessions/{session_id}/requirement/reject")

        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "requirement_rejected"

    @pytest.mark.asyncio
    async def test_regenerate_requirement(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)

        regen_resp = await client.post(f"/api/sessions/{session_id}/requirement/regenerate")
        assert regen_resp.status_code == 200
        assert regen_resp.json()["status"] == "analyzing"

    @pytest.mark.asyncio
    async def test_regenerate_requirement_with_guidance_forwards_it_to_the_background_task(
        self, client, monkeypatch
    ):
        """Module 8 : vérifie la responsabilité propre au routeur — parser le
        corps JSON {guidance} et le transmettre à la tâche de fond. La
        propagation jusqu'au prompt LLM lui-même est déjà couverte par
        tests/test_session_service.py::TestRegenerationGuidance."""
        from app.services import session_service

        captured: dict = {}
        real_task = session_service.run_requirement_analysis_task

        async def _spy_task(session_factory, session_id, analyzer, guidance=None):
            captured["guidance"] = guidance
            await real_task(session_factory, session_id, analyzer, guidance=guidance)

        monkeypatch.setattr(session_service, "run_requirement_analysis_task", _spy_task)

        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)

        regen_resp = await client.post(
            f"/api/sessions/{session_id}/requirement/regenerate",
            json={"guidance": "[FICTIF] précise le moyen de paiement"},
        )

        assert regen_resp.status_code == 200
        assert captured["guidance"] == "[FICTIF] précise le moyen de paiement"

        get_resp = await client.get(f"/api/sessions/{session_id}")
        assert get_resp.json()["status"] == "pending_requirement_review"

    @pytest.mark.asyncio
    async def test_create_session_with_attachment(self, client, monkeypatch, tmp_path):
        """multipart/form-data avec un fichier joint : la pièce jointe est
        créée immédiatement (kickoff), traitée (texte extrait) seulement une
        fois la tâche de fond du module 2 lancée — c-à-d après
        POST .../chat/finalize (module 9), plus au kickoff direct."""
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

        response = await client.post(
            "/api/sessions",
            data={"user_input": FICTIONAL_USER_INPUT},
            files={"files": ("notes.txt", b"[FICTIF] precision du besoin", "text/plain")},
        )

        assert response.status_code == 201
        body = response.json()
        assert len(body["attachments"]) == 1
        assert body["attachments"][0]["filename"] == "notes.txt"
        assert body["attachments"][0]["extracted_text"] is None  # pas encore traitée, session en 'chatting'

        get_resp = await _finalize_and_wait(client, body["id"])
        attachment = get_resp.json()["attachments"][0]
        assert attachment["extracted_text"] == "[FICTIF] precision du besoin"
        assert attachment["extraction_error"] is None

    @pytest.mark.asyncio
    async def test_create_session_rejects_oversized_attachment(self, client, monkeypatch, tmp_path):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "MAX_ATTACHMENT_SIZE_MB", 1)

        oversized = b"x" * (2 * 1024 * 1024)  # 2 Mo > 1 Mo autorisé
        response = await client.post(
            "/api/sessions",
            data={"user_input": FICTIONAL_USER_INPUT},
            files={"files": ("gros_fichier.txt", oversized, "text/plain")},
        )

        assert response.status_code == 413


class TestChatEndpoints:
    @pytest.mark.asyncio
    async def test_send_chat_message_appends_turn_and_stays_chatting(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        chat_resp = await client.post(
            f"/api/sessions/{session_id}/chat", json={"message": "[FICTIF] pour des clients particuliers"}
        )

        assert chat_resp.status_code == 200
        body = chat_resp.json()
        assert body["status"] == "chatting"
        assert len(body["chat_history"]) == 3
        assert body["chat_history"][1]["content"] == "[FICTIF] pour des clients particuliers"
        assert body["chat_history"][2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_send_chat_message_after_finalize_returns_409(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)

        response = await client.post(f"/api/sessions/{session_id}/chat", json={"message": "trop tard"})

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_send_chat_message_empty_body_returns_422(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        response = await client.post(f"/api/sessions/{session_id}/chat", json={"message": ""})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_llm_failure_returns_503_and_persists_nothing(self, client, monkeypatch):
        from app.agents.llm_layer import LLMProviderError

        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        class _FailingProvider(MockProvider):
            async def complete(self, request):
                raise LLMProviderError("Simulated failure", provider="failing")

        app.dependency_overrides[get_chat_agent] = lambda: ChatAgent(llm_provider=_FailingProvider())

        response = await client.post(f"/api/sessions/{session_id}/chat", json={"message": "[FICTIF] un message"})

        assert response.status_code == 503
        get_resp = await client.get(f"/api/sessions/{session_id}")
        assert len(get_resp.json()["chat_history"]) == 1

    @pytest.mark.asyncio
    async def test_finalize_chat_kicks_off_analysis(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        finalize_resp = await client.post(f"/api/sessions/{session_id}/chat/finalize")

        assert finalize_resp.status_code == 200
        assert finalize_resp.json()["status"] == "analyzing"

        get_resp = await client.get(f"/api/sessions/{session_id}")
        assert get_resp.json()["status"] == "pending_requirement_review"

    @pytest.mark.asyncio
    async def test_finalize_chat_twice_returns_409(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await client.post(f"/api/sessions/{session_id}/chat/finalize")

        response = await client.post(f"/api/sessions/{session_id}/chat/finalize")

        assert response.status_code == 409


class TestAttachmentFileEndpoint:
    @pytest.mark.asyncio
    async def test_get_attachment_file_returns_raw_content(self, client, monkeypatch, tmp_path):
        """Sert le fichier brut (pas le texte extrait) — utilisé par le
        frontend pour les miniatures d'images."""
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

        create_resp = await client.post(
            "/api/sessions",
            data={"user_input": FICTIONAL_USER_INPUT},
            files={"files": ("notes.txt", b"[FICTIF] contenu brut", "text/plain")},
        )
        session_id = create_resp.json()["id"]
        attachment_id = create_resp.json()["attachments"][0]["id"]

        response = await client.get(f"/api/sessions/{session_id}/attachments/{attachment_id}/file")

        assert response.status_code == 200
        assert response.content == b"[FICTIF] contenu brut"
        assert response.headers["content-type"].startswith("text/plain")

    @pytest.mark.asyncio
    async def test_get_attachment_file_unknown_attachment_returns_404(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        response = await client.get(f"/api/sessions/{session_id}/attachments/does-not-exist/file")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_attachment_file_wrong_session_returns_404(self, client, monkeypatch, tmp_path):
        """Un id de pièce jointe valide mais rattaché à une AUTRE session ne
        doit jamais permettre d'en lire le fichier (cf.
        attachment_service.get_attachment, filtré sur session_id ET id)."""
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

        create_resp = await client.post(
            "/api/sessions",
            data={"user_input": FICTIONAL_USER_INPUT},
            files={"files": ("notes.txt", b"[FICTIF] contenu brut", "text/plain")},
        )
        attachment_id = create_resp.json()["attachments"][0]["id"]

        other_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        other_session_id = other_resp.json()["id"]

        response = await client.get(f"/api/sessions/{other_session_id}/attachments/{attachment_id}/file")

        assert response.status_code == 404


class TestSessionsRouterErrors:
    @pytest.mark.asyncio
    async def test_get_unknown_session_returns_404(self, client):
        response = await client.get("/api/sessions/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_accept_tests_before_requirement_returns_409(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]

        response = await client.post(f"/api/sessions/{session_id}/tests/accept")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_double_accept_requirement_returns_409(self, client):
        create_resp = await client.post("/api/sessions", data={"user_input": FICTIONAL_USER_INPUT})
        session_id = create_resp.json()["id"]
        await _finalize_and_wait(client, session_id)
        await client.post(f"/api/sessions/{session_id}/requirement/accept")

        response = await client.post(f"/api/sessions/{session_id}/requirement/accept")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_empty_user_input_returns_422(self, client):
        response = await client.post("/api/sessions", data={"user_input": ""})
        assert response.status_code == 422
