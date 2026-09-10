"""
Module 4 — Interface de validation : routes HTTP

Expose la machine à états de session_service.py. Les erreurs de logique
métier (SessionNotFoundError, InvalidSessionTransitionError) sont
converties en réponses HTTP par les gestionnaires enregistrés dans
app/main.py — pas de try/except ici.

Module 8 (22/07/2026) : toutes les routes exigent une session authentifiée
(Depends(get_current_user)) — chaque assigné ne voit/n'actionne que ses
propres sessions (cf. session_service._get_or_404, filtré par user_id).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.chat_agent import ChatAgent
from app.agents.requirement_analyzer import RequirementAnalyzerAgent
from app.agents.test_design_agent import TestDesignAgent
from app.config import settings
from app.database import get_db, get_session_factory
from app.dependencies import get_chat_agent, get_current_user, get_requirement_analyzer, get_test_design_agent
from app.models.session import SessionModel, UserModel
from app.schemas.attachment import UploadedFile
from app.schemas.session import (
    ChatSendRequest,
    RegenerateRequest,
    SessionCreateRequest,
    SessionResponse,
    SessionSummary,
)
from app.services import attachment_service, session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _to_summary(session: SessionModel) -> SessionSummary:
    title = (
        session.requirement_analysis["title"]
        if session.requirement_analysis
        else session.raw_input[:80]
    )
    return SessionSummary(
        id=session.id,
        status=session.status,
        title=title,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    user_input: str = Form(..., min_length=1, description="Texte libre décrivant le besoin"),
    context: Optional[str] = Form(default=None, description="Contexte additionnel optionnel"),
    files: list[UploadFile] = File(
        default=[],
        description="Photos/documents joints (image, pdf, docx, xlsx, pptx, txt, md)",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    """
    Crée la session (statut 'chatting', module 9) — ouvre une phase de
    clarification conversationnelle libre, AUCUN appel au module 2 pour
    l'instant (cf. POST .../chat et .../chat/finalize ci-dessous).

    multipart/form-data (et non JSON) pour accepter des pièces jointes en
    plus du texte — cf. app/services/attachment_service.py.
    """
    payload = SessionCreateRequest(user_input=user_input, context=context)

    max_attachment_bytes = settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
    uploaded: list[UploadedFile] = []
    for upload in files:
        if upload.filename is None:
            continue
        content = await upload.read()
        if len(content) > max_attachment_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"'{upload.filename}' dépasse la taille maximale autorisée "
                    f"({settings.MAX_ATTACHMENT_SIZE_MB} Mo)"
                ),
            )
        uploaded.append(
            UploadedFile(
                filename=upload.filename,
                content_type=upload.content_type or "application/octet-stream",
                content=content,
            )
        )

    session = await session_service.create_session(db, payload, current_user.id, files=uploaded)
    if uploaded:
        logger.info(
            "create_session: session %s créée avec %d pièce(s) jointe(s)",
            session.id, len(session.attachments),
        )
    return session


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    db: AsyncSession = Depends(get_db), current_user: UserModel = Depends(get_current_user)
) -> list[SessionSummary]:
    sessions = await session_service.list_sessions(db, current_user.id)
    return [_to_summary(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.get_session(db, session_id, current_user.id)


@router.get("/{session_id}/attachments/{attachment_id}/file")
async def get_attachment_file(
    session_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> FileResponse:
    """Sert le contenu brut d'une pièce jointe (ex: miniature d'image côté
    frontend) — jamais son contenu extrait/décrit, qui vit dans
    SessionResponse.attachments[].extracted_text. Vérifie d'abord l'ownership
    de la session (module 8) avant même de chercher la pièce jointe."""
    await session_service.get_session(db, session_id, current_user.id)  # 404 si pas la sienne

    attachment = await attachment_service.get_attachment(db, session_id, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Pièce jointe introuvable")
    file_path = attachment_service.resolve_attachment_path(attachment)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")
    return FileResponse(file_path, media_type=attachment.content_type, filename=attachment.filename)


@router.post("/{session_id}/chat", response_model=SessionResponse)
async def send_chat_message(
    session_id: str,
    payload: ChatSendRequest,
    db: AsyncSession = Depends(get_db),
    chat_agent: ChatAgent = Depends(get_chat_agent),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    """
    Module 9 — ajoute un tour de conversation (statut 'chatting' requis).
    Synchrone (pas de BackgroundTasks) : cf. docstring de
    session_service.send_chat_message pour la justification.
    """
    return await session_service.send_chat_message(db, session_id, current_user.id, payload.message, chat_agent)


@router.post("/{session_id}/chat/finalize", response_model=SessionResponse)
async def finalize_chat(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    analyzer: RequirementAnalyzerAgent = Depends(get_requirement_analyzer),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    """Sort de 'chatting' vers 'analyzing' et lance le module 2 en tâche de
    fond, avec le transcript de conversation comme contexte — le client doit
    ré-interroger GET /{id} jusqu'à en sortir (même contrat que les autres
    kickoffs)."""
    session = await session_service.finalize_chat(db, session_id, current_user.id)
    background_tasks.add_task(
        session_service.run_requirement_analysis_task, session_factory, session_id, analyzer
    )
    return session


@router.post("/{session_id}/requirement/accept", response_model=SessionResponse)
async def accept_requirement(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    designer: TestDesignAgent = Depends(get_test_design_agent),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    """Valide l'analyse (statut 'designing_tests') et lance le module 3 en
    tâche de fond — le client doit ré-interroger GET /{id} jusqu'à en sortir."""
    session = await session_service.accept_requirement(db, session_id, current_user.id)
    background_tasks.add_task(
        session_service.run_test_design_task, session_factory, session_id, designer
    )
    return session


@router.post("/{session_id}/requirement/reject", response_model=SessionResponse)
async def reject_requirement(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.reject_requirement(db, session_id, current_user.id)


@router.post("/{session_id}/requirement/regenerate", response_model=SessionResponse)
async def regenerate_requirement(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[RegenerateRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    analyzer: RequirementAnalyzerAgent = Depends(get_requirement_analyzer),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    session = await session_service.regenerate_requirement(db, session_id, current_user.id)
    background_tasks.add_task(
        session_service.run_requirement_analysis_task,
        session_factory,
        session_id,
        analyzer,
        payload.guidance if payload else None,
    )
    return session


@router.post("/{session_id}/tests/accept", response_model=SessionResponse)
async def accept_tests(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.accept_tests(db, session_id, current_user.id)


@router.post("/{session_id}/tests/reject", response_model=SessionResponse)
async def reject_tests(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    return await session_service.reject_tests(db, session_id, current_user.id)


@router.post("/{session_id}/tests/regenerate", response_model=SessionResponse)
async def regenerate_tests(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[RegenerateRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    designer: TestDesignAgent = Depends(get_test_design_agent),
    current_user: UserModel = Depends(get_current_user),
) -> SessionModel:
    session = await session_service.regenerate_tests(db, session_id, current_user.id)
    background_tasks.add_task(
        session_service.run_test_design_task,
        session_factory,
        session_id,
        designer,
        payload.guidance if payload else None,
    )
    return session
