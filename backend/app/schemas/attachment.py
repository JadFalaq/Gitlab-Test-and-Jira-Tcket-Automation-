"""
Module 2 (extension) — Pièces jointes : Schémas d'interface

Voir app/models/session.py::AttachmentModel pour le cycle de vie complet
(kickoff synchrone -> traitement en tâche de fond -> extracted_text injecté
dans le contexte du module 2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NamedTuple, Optional

from pydantic import BaseModel, Field

AttachmentKind = Literal["image", "document"]


class UploadedFile(NamedTuple):
    """
    Fichier brut reçu par le router (déjà lu en mémoire depuis l'UploadFile
    FastAPI) — forme de transport pure Python vers session_service.py, qui
    ne doit jamais dépendre d'un type spécifique à FastAPI/Starlette.
    """

    filename: str
    content_type: str
    content: bytes


class AttachmentResponse(BaseModel):
    """Une pièce jointe telle qu'exposée par l'API (jamais le contenu binaire)."""

    id: str
    filename: str = Field(..., description="Nom de fichier original (affichage uniquement)")
    content_type: str
    kind: AttachmentKind
    extracted_text: Optional[str] = Field(
        default=None,
        description="Texte extrait (document) ou description générée (image) ; NULL tant que non traité",
    )
    extraction_error: Optional[str] = Field(
        default=None,
        description="Raison de l'échec si l'extraction/description a échoué (best-effort, n'invalide pas la session)",
    )
    created_at: datetime

    model_config = {"from_attributes": True}


__all__ = ["AttachmentKind", "UploadedFile", "AttachmentResponse"]
