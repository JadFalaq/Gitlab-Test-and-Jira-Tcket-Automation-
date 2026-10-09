"""
Tests unitaires — app/services/file_validation.py (type réel des pièces jointes).

Toutes les données sont fictives.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.file_validation import AttachmentRejectedError, detect_file_type


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<xml/>")
    return buffer.getvalue()


_DOCX = _zip("[Content_Types].xml", "word/document.xml")
_XLSX = _zip("[Content_Types].xml", "xl/workbook.xml")
_PPTX = _zip("[Content_Types].xml", "ppt/presentation.xml")


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("a.png", b"\x89PNG\r\n\x1a\n...", "image/png"),
        ("a.JPG", b"\xff\xd8\xff\xe0...", "image/jpeg"),
        ("a.jpeg", b"\xff\xd8\xff\xe1...", "image/jpeg"),
        ("a.gif", b"GIF89a...", "image/gif"),
        ("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        ("a.pdf", b"%PDF-1.7 ...", "application/pdf"),
        ("a.docx", _DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("a.xlsx", _XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.pptx", _PPTX, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("a.txt", "[FICTIF] texte accentué".encode("utf-8"), "text/plain"),
        ("a.md", b"# [FICTIF] titre", "text/markdown"),
    ],
)
def test_accepts_matching_content(filename, content, mime):
    assert detect_file_type(filename, content).mime_type == mime


@pytest.mark.parametrize(
    ("filename", "content", "reason"),
    [
        ("a.exe", b"MZ", "non supporté"),
        ("a.html", b"<html>", "non supporté"),
        ("sans_extension", b"%PDF-", "non supporté"),
        ("a.png", b"<html><script>", "ne correspond pas"),
        ("a.pdf", b"\x89PNG\r\n\x1a\n", "ne correspond pas"),
        ("a.docx", _XLSX, "ne correspond pas"),  # un xlsx renommé en docx
        ("a.docx", b"PK\x03\x04pas un zip", "ne correspond pas"),
        ("a.txt", b"\x00binaire", "ne correspond pas"),
        ("a.txt", b"\xff\xfe\xfa", "ne correspond pas"),  # pas de l'UTF-8
        ("a.pdf", b"", "vide"),
    ],
)
def test_rejects_mismatched_or_unsupported(filename, content, reason):
    with pytest.raises(AttachmentRejectedError, match=reason):
        detect_file_type(filename, content)
