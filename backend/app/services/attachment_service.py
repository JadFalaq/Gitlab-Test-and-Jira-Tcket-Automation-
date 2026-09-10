"""
Module 2 (extension) — Pièces jointes : stockage disque + extraction de
texte, orchestration du traitement (documents + images).

Voir app/models/session.py::AttachmentModel pour le cycle de vie complet.

Design decisions:
    - Le fichier est écrit sur disque sous un nom dérivé de l'id (UUID) de
      la pièce jointe, JAMAIS du nom de fichier original (`filename`) — un
      nom de fichier contrôlé par l'utilisateur ne doit jamais servir à
      construire un chemin disque (risque de traversée de répertoire, ex:
      "../../etc/passwd"). `filename` n'est conservé qu'en métadonnée DB
      pour l'affichage.
    - Extraction de texte (documents) et description (images) sont
      entièrement best-effort : un fichier corrompu, un format non reconnu,
      ou un modèle vision indisponible se traduit par `extraction_error`
      renseigné, jamais par une exception qui remonterait jusqu'à
      l'analyse LLM (cf. session_service.run_requirement_analysis).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.vision_layer import BaseVisionProvider, VisionProviderError
from app.config import settings
from app.models.session import AttachmentModel

logger = logging.getLogger(__name__)

_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

# Extensions de documents reconnues pour l'extraction de texte (hors images).
_DOCUMENT_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx"}

_SAFE_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")


class AttachmentExtractionError(Exception):
    """Échec d'extraction de texte pour un document (fichier corrompu, format non pris en charge...)."""


def _upload_root() -> Path:
    return Path(settings.UPLOAD_DIR)


def _safe_extension(filename: str) -> str:
    """Extension normalisée (minuscules, avec le point) à partir du nom de
    fichier ORIGINAL — utilisée uniquement pour la classification/dispatch
    d'extraction, jamais pour construire le chemin disque réel."""
    suffix = Path(filename).suffix.lower()
    return suffix if len(suffix) <= 11 and _SAFE_EXT_RE.match(suffix.lstrip(".") or "x") else ""


def classify_attachment(filename: str, content_type: str) -> str:
    """Retourne "image" ou "document" selon le content-type déclaré (source
    la plus fiable) avec repli sur l'extension si le content-type est
    générique (ex: "application/octet-stream")."""
    if content_type in _IMAGE_CONTENT_TYPES:
        return "image"
    ext = _safe_extension(filename)
    if ext in _DOCUMENT_EXTENSIONS:
        return "document"
    if content_type.startswith("image/"):
        return "image"
    return "document"


async def save_attachment(
    db: AsyncSession,
    session_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> AttachmentModel:
    """
    Écrit le fichier sur disque (nom dérivé de l'id, jamais de `filename`)
    et crée la ligne AttachmentModel correspondante (non committée : à la
    charge de l'appelant, cf. session_service.create_session).
    """
    kind = classify_attachment(filename, content_type)
    attachment = AttachmentModel(
        session_id=session_id,
        filename=filename[:255],
        content_type=content_type or "application/octet-stream",
        kind=kind,
        file_path="",  # renseigné ci-dessous, une fois l'id généré par défaut
    )

    ext = _safe_extension(filename)
    session_dir = _upload_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    disk_path = session_dir / f"{attachment.id}{ext}"
    disk_path.write_bytes(content)

    attachment.file_path = str(disk_path.relative_to(_upload_root()))
    db.add(attachment)
    return attachment


def _extract_text_document(ext: str, content: bytes) -> str:
    """Dispatch d'extraction par extension. Lève AttachmentExtractionError
    si le format n'est pas reconnu ou si le fichier est illisible."""
    try:
        if ext in (".txt", ".md"):
            return content.decode("utf-8", errors="replace")

        if ext == ".pdf":
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()

        if ext == ".docx":
            from io import BytesIO

            from docx import Document

            doc = Document(BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()

        if ext == ".xlsx":
            from io import BytesIO

            from openpyxl import load_workbook

            wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
            lines: list[str] = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append(" | ".join(cells))
            return "\n".join(lines).strip()

        if ext == ".pptx":
            from io import BytesIO

            from pptx import Presentation

            prs = Presentation(BytesIO(content))
            lines = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame and shape.text_frame.text:
                        lines.append(shape.text_frame.text)
            return "\n".join(lines).strip()

    except Exception as exc:
        raise AttachmentExtractionError(f"Impossible de lire le fichier ({ext}) : {exc}") from exc

    raise AttachmentExtractionError(f"Type de document non pris en charge : '{ext or 'sans extension'}'")


def extract_document_text(filename: str, content: bytes) -> str:
    """
    Point d'entrée public d'extraction de texte pour un document, à partir de
    son nom de fichier ORIGINAL (uniquement pour en déduire l'extension —
    jamais utilisé pour un chemin disque, cf. docstring de tête) et de son
    contenu brut. Lève AttachmentExtractionError si le format n'est pas
    reconnu ou le fichier illisible.

    Réutilisé par process_attachment (pièce jointe de session, ci-dessous) et
    par scripts/seed_rag_from_documents.py (amorçage RAG depuis des documents
    historiques, hors pipeline session — cf. sa docstring).
    """
    return _extract_text_document(_safe_extension(filename), content)


async def process_attachment(attachment: AttachmentModel, vision_provider: BaseVisionProvider) -> None:
    """
    Traite une pièce jointe (mutation in-place, pas de commit ici — à la
    charge de l'appelant) : extraction de texte pour un document, ou
    description via le modèle vision pour une image.

    Ne lève jamais : un échec est consigné dans `extraction_error`, la
    session continue sans ce contenu enrichi (cf. docstring de tête).
    """
    disk_path = _upload_root() / attachment.file_path
    try:
        content = disk_path.read_bytes()
    except OSError as exc:
        attachment.extraction_error = f"Fichier introuvable sur disque : {exc}"
        logger.warning("Attachment %s: fichier illisible (%s)", attachment.id, exc)
        return

    if attachment.kind == "image":
        try:
            attachment.extracted_text = await vision_provider.describe(content)
        except VisionProviderError as exc:
            attachment.extraction_error = str(exc)
            logger.info("Attachment %s: description image indisponible (%s)", attachment.id, exc)
        except Exception:
            attachment.extraction_error = "Erreur inattendue pendant la description de l'image."
            logger.exception("Attachment %s: échec inattendu de la description image", attachment.id)
        return

    try:
        attachment.extracted_text = extract_document_text(attachment.filename, content)
    except AttachmentExtractionError as exc:
        attachment.extraction_error = str(exc)
        logger.info("Attachment %s: extraction échouée (%s)", attachment.id, exc)
    except Exception:
        attachment.extraction_error = "Erreur inattendue pendant l'extraction du document."
        logger.exception("Attachment %s: échec inattendu de l'extraction", attachment.id)


async def get_session_attachments(db: AsyncSession, session_id: str) -> list[AttachmentModel]:
    result = await db.execute(
        select(AttachmentModel)
        .where(AttachmentModel.session_id == session_id)
        .order_by(AttachmentModel.created_at)
    )
    return list(result.scalars().all())


async def get_attachment(db: AsyncSession, session_id: str, attachment_id: str) -> Optional[AttachmentModel]:
    """Récupère une pièce jointe précise, en vérifiant qu'elle appartient
    bien à `session_id` (évite qu'un id de pièce jointe valide mais d'une
    autre session ne serve à en lire le fichier — cf. routers/sessions.py,
    endpoint de téléchargement)."""
    result = await db.execute(
        select(AttachmentModel).where(
            AttachmentModel.id == attachment_id, AttachmentModel.session_id == session_id
        )
    )
    return result.scalar_one_or_none()


def resolve_attachment_path(attachment: AttachmentModel) -> Path:
    """Chemin disque absolu du fichier stocké pour cette pièce jointe."""
    return _upload_root() / attachment.file_path


def build_attachments_context(attachments: list[AttachmentModel]) -> Optional[str]:
    """
    Concatène le texte extrait/la description de chaque pièce jointe
    traitée avec succès en un bloc de contexte additionnel, à ajouter au
    `context` de la session avant l'appel LLM. None si aucune pièce jointe
    n'a produit de contenu exploitable.
    """
    blocks = [
        f"Pièce jointe « {a.filename} » :\n{a.extracted_text}"
        for a in attachments
        if a.extracted_text
    ]
    if not blocks:
        return None
    return "\n\n".join(blocks)


__all__ = [
    "AttachmentExtractionError",
    "classify_attachment",
    "save_attachment",
    "extract_document_text",
    "process_attachment",
    "get_session_attachments",
    "get_attachment",
    "resolve_attachment_path",
    "build_attachments_context",
]
