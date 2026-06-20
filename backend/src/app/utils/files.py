"""Upload validation + storage.

Defends the parse path: extension allowlist, magic-byte sniff that must match the
extension, size cap enforced *while* reading (not after buffering), and storage
under a server-generated key outside any web root (defeats path traversal).

Blobs are isolated on the mounted volume:
    conversations : {upload_dir}/{user_id}/{conversation_id}/{document_id}{ext}
    agents        : {upload_dir}/agents/{agent_id}/{version_id}/{document_id}{ext}
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid

import magic
from fastapi import UploadFile

from app.config import get_settings
from app.rag.parsing import ALLOWED_TYPES

_settings = get_settings()
_READ_CHUNK = 1024 * 1024  # 1 MiB


class UploadRejected(ValueError):
    pass


def _ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


async def _stream_to(file: UploadFile, dest_dir: str) -> dict:
    """Validate + stream an upload into ``dest_dir`` under a UUID name. Shared by
    conversation and agent uploads."""
    ext = _ext_of(file.filename or "")
    if ext not in ALLOWED_TYPES:
        raise UploadRejected(f"Unsupported file type: {ext or 'unknown'}")

    os.makedirs(dest_dir, exist_ok=True)
    storage_path = os.path.join(dest_dir, f"{uuid.uuid4()}{ext}")

    hasher = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(storage_path, "wb") as out:
            while chunk := await file.read(_READ_CHUNK):
                size += len(chunk)
                if size > _settings.max_upload_bytes:
                    raise UploadRejected(
                        f"File exceeds the {_settings.max_upload_mb} MB limit."
                    )
                if len(head) < 2048:
                    head += chunk[: 2048 - len(head)]
                hasher.update(chunk)
                out.write(chunk)
    except UploadRejected:
        _safe_unlink(storage_path)
        raise
    finally:
        await file.close()

    if size == 0:
        _safe_unlink(storage_path)
        raise UploadRejected("Empty file.")

    sniffed = magic.from_buffer(head, mime=True)
    if sniffed not in ALLOWED_TYPES[ext]:
        _safe_unlink(storage_path)
        raise UploadRejected("File content does not match its extension.")

    return {
        "filename": os.path.basename(file.filename or f"upload{ext}"),
        "content_type": sniffed,
        "size_bytes": size,
        "sha256": hasher.hexdigest(),
        "storage_path": storage_path,
    }


# ── conversation uploads ────────────────────────────────────────────────────────
def _chat_dir(user_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return os.path.join(_settings.upload_dir, str(user_id), str(conversation_id))


async def validate_and_store(
    file: UploadFile, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> dict:
    return await _stream_to(file, _chat_dir(user_id, conversation_id))


def remove_chat_dir(user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    shutil.rmtree(_chat_dir(user_id, conversation_id), ignore_errors=True)


# ── agent uploads ─────────────────────────────────────────────────────────────────
def _agent_dir(agent_id: uuid.UUID) -> str:
    return os.path.join(_settings.upload_dir, "agents", str(agent_id))


def _agent_version_dir(agent_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return os.path.join(_agent_dir(agent_id), str(version_id))


async def validate_and_store_agent(
    file: UploadFile, agent_id: uuid.UUID, version_id: uuid.UUID
) -> dict:
    return await _stream_to(file, _agent_version_dir(agent_id, version_id))


def copy_agent_file(
    src_path: str, agent_id: uuid.UUID, version_id: uuid.UUID
) -> str:
    """Copy an existing knowledge blob into a new version's directory (clone)."""
    ext = _ext_of(src_path)
    dest_dir = _agent_version_dir(agent_id, version_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{uuid.uuid4()}{ext}")
    shutil.copy2(src_path, dest)
    return dest


def remove_agent_dir(agent_id: uuid.UUID) -> None:
    shutil.rmtree(_agent_dir(agent_id), ignore_errors=True)
