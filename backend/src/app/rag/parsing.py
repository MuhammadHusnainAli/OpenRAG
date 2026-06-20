"""Parse uploaded files into plain text. Runs in the worker (off the request
path) so a malformed/huge file can't hang the API. Office formats are zip
archives, so we bound entry count and total uncompressed size (zip-bomb guard).
"""

from __future__ import annotations

import os
import zipfile

# allowed extension -> sniffed mime types that may back it
ALLOWED_TYPES: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".txt": {"text/plain"},
    ".md": {"text/plain", "text/markdown"},
}

_MAX_ZIP_ENTRIES = 2000
_MAX_UNCOMPRESSED = 200 * 1024 * 1024   # 200 MiB expanded ceiling


class ParseError(Exception):
    pass


def _guard_zip(path: str) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_ENTRIES:
                raise ParseError("Archive has too many entries.")
            total = sum(i.file_size for i in infos)
            if total > _MAX_UNCOMPRESSED:
                raise ParseError("Archive expands beyond the allowed size.")
    except zipfile.BadZipFile as exc:
        raise ParseError("Corrupt office document.") from exc


def _parse_pdf(path: str) -> str:
    import fitz  # PyMuPDF

    parts: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return "\n".join(parts)


def _parse_docx(path: str) -> str:
    _guard_zip(path)
    from docx import Document as DocxDocument

    doc = DocxDocument(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def _parse_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse_document(path: str) -> str:
    """Dispatch by extension; return extracted text (empty -> ParseError)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = _parse_pdf(path)
    elif ext == ".docx":
        text = _parse_docx(path)
    elif ext in (".txt", ".md"):
        text = _parse_text(path)
    else:
        raise ParseError(f"Unsupported file type: {ext}")

    if not text or not text.strip():
        raise ParseError("No extractable text found in document.")
    return text
