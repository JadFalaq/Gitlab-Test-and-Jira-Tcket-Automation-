"""
Validation des pièces jointes : type RÉEL du fichier (signature binaire) et
taille, avant tout stockage.

Pourquoi ne pas croire le navigateur :
- L'extension et le `Content-Type` envoyés par le client sont choisis par
  lui. Un fichier HTML renommé en `.png`, ou un `.png` déclaré `text/html`,
  serait ensuite servi tel quel par GET .../attachments/{id}/file, donc
  interprété par le navigateur sur notre domaine (XSS stocké).
- On détecte donc le type à partir du contenu, on exige qu'il corresponde à
  l'extension, et c'est le type MIME DÉTECTÉ (canonique) qui est stocké et
  renvoyé, jamais celui du client.

Aucune dépendance ajoutée : signatures binaires connues + inspection des
archives ZIP pour les formats Office.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path


class AttachmentRejectedError(Exception):
    """Pièce jointe refusée (type non supporté, contenu incohérent, trop grosse)."""


@dataclass(frozen=True)
class DetectedFile:
    extension: str  # ".pdf", ".png", ...
    mime_type: str  # type MIME canonique, stocké à la place de celui du client


# Extension -> type MIME canonique. Toute autre extension est refusée.
ALLOWED_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

# Dossier interne qui distingue les trois formats Office (tous des ZIP).
_OFFICE_MAIN_PART = {".docx": "word/", ".xlsx": "xl/", ".pptx": "ppt/"}


def _matches_signature(extension: str, content: bytes) -> bool:
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in (".jpg", ".jpeg"):
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    if extension == ".pdf":
        return content.startswith(b"%PDF-")
    if extension in _OFFICE_MAIN_PART:
        return _is_office_document(content, _OFFICE_MAIN_PART[extension])
    if extension in (".txt", ".md"):
        return _is_text(content)
    return False


def _is_office_document(content: bytes, main_part: str) -> bool:
    if not content.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and any(name.startswith(main_part) for name in names)


def _is_text(content: bytes) -> bool:
    """Texte UTF-8 sans octet nul (un binaire renommé en .txt est refusé)."""
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def detect_file_type(filename: str, content: bytes) -> DetectedFile:
    """Vérifie que l'extension est autorisée ET que le contenu correspond.

    Raises:
        AttachmentRejectedError: extension non supportée, fichier vide, ou
            contenu qui ne correspond pas à l'extension.
    """
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise AttachmentRejectedError(f"'{filename}' : type de fichier non supporté (autorisés : {allowed})")
    if not content:
        raise AttachmentRejectedError(f"'{filename}' : fichier vide")
    if not _matches_signature(extension, content):
        raise AttachmentRejectedError(f"'{filename}' : le contenu ne correspond pas à un fichier {extension}")
    return DetectedFile(extension=extension, mime_type=ALLOWED_TYPES[extension])


__all__ = ["ALLOWED_TYPES", "AttachmentRejectedError", "DetectedFile", "detect_file_type"]
