"""Custom-agent lifecycle orchestration.

Versioning model (full snapshot): each version owns its knowledge vectors. A
single mutable ``draft`` is where editing happens; deploying it assigns a
version number + status (test/live). Living a version makes it the agent's
default. Creating a new draft clones the latest published version — copying its
documents and re-embedding them under the new version (own vectors).
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.celery_app import celery_app
from app.core.security import sha256_hex
from app.data.models import (
    Agent,
    AgentVersion,
    AgentVersionStatus,
    AgentVisibility,
    User,
)
from app.data.repositories import agents as agent_repo
from app.data.repositories import users as user_repo
from app.rag.qdrant import delete_by_agent, delete_by_agent_document
from app.services.errors import Conflict, NotFound, ServiceError, UploadError
from app.utils.files import (
    UploadRejected,
    copy_agent_file,
    remove_agent_dir,
    validate_and_store_agent,
)

_settings = get_settings()
INGEST_AGENT_TASK = "app.services.ingestion.ingest_agent_document"


#  agent CRUD 
async def create_agent(
    db: AsyncSession, owner: User, *, name: str, description: str | None
) -> Agent:
    agent = await agent_repo.create_agent(
        db, owner_id=owner.id, name=name, description=description
    )
    # every agent starts with an empty editable draft (version 1-to-be)
    await agent_repo.create_version(db, agent_id=agent.id, system_prompt="")
    return agent


async def get_owned_or_404(db: AsyncSession, agent_id: str, owner: User) -> Agent:
    agent = await agent_repo.get_owned_agent(db, agent_id, owner.id)
    if agent is None:
        raise NotFound("Agent not found.")
    return agent


async def list_owned(db: AsyncSession, owner: User) -> list[Agent]:
    return await agent_repo.list_owned_agents(db, owner.id)


async def list_shared(db: AsyncSession, user: User) -> list[Agent]:
    return await agent_repo.list_shared_agents(db, user.id)


async def update_agent(
    db: AsyncSession,
    agent: Agent,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Agent:
    if name is not None:
        agent.name = name
    if description is not None:
        agent.description = description
    await db.flush()
    return agent


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    agent_id = agent.id
    await agent_repo.delete_agent(db, agent)       # cascades versions/docs/access in PG
    await delete_by_agent(str(agent_id))           # all vectors across versions
    remove_agent_dir(agent_id)                     # all blobs


#  draft / editing 
async def ensure_draft(db: AsyncSession, agent: Agent) -> AgentVersion:
    """Return the agent's draft, creating one (cloned from the latest published
    version) if none exists."""
    draft = await agent_repo.get_draft(db, agent.id)
    if draft is not None:
        return draft

    source = await agent_repo.latest_published(db, agent.id)
    draft = await agent_repo.create_version(
        db,
        agent_id=agent.id,
        system_prompt=source.system_prompt if source else "",
        model=source.model if source else None,
        parent_version_id=source.id if source else None,
    )
    if source is not None:
        # clone knowledge: copy each blob into the new version dir + re-embed
        for doc in await agent_repo.list_documents(db, source.id):
            new_path = copy_agent_file(doc.storage_path, agent.id, draft.id)
            new_doc = await agent_repo.create_document(
                db,
                agent_id=agent.id,
                version_id=draft.id,
                owner_id=doc.owner_id,
                filename=doc.filename,
                content_type=doc.content_type,
                size_bytes=doc.size_bytes,
                sha256=doc.sha256,
                storage_path=new_path,
            )
            await db.commit()
            celery_app.send_task(INGEST_AGENT_TASK, args=[str(new_doc.id)])
    return draft


async def update_draft(
    db: AsyncSession,
    agent: Agent,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
) -> AgentVersion:
    draft = await ensure_draft(db, agent)
    if system_prompt is not None:
        draft.system_prompt = system_prompt
    if model is not None:
        draft.model = model or None
    await db.flush()
    return draft


#  knowledge base (on the draft) 
async def upload_knowledge(db: AsyncSession, agent: Agent, owner: User, file: UploadFile):
    draft = await ensure_draft(db, agent)

    if await agent_repo.count_documents(db, draft.id) >= _settings.max_files_per_session:
        raise Conflict(f"Knowledge limit reached ({_settings.max_files_per_session} files).")

    try:
        meta = await validate_and_store_agent(file, agent.id, draft.id)
    except UploadRejected as exc:
        raise UploadError(str(exc)) from exc

    existing_total = await agent_repo.total_size(db, draft.id)
    if existing_total + meta["size_bytes"] > _settings.max_session_total_bytes:
        from app.utils.files import _safe_unlink

        _safe_unlink(meta["storage_path"])
        raise Conflict(f"Knowledge storage limit reached ({_settings.max_session_total_mb} MB).")

    if await agent_repo.exists_sha(db, draft.id, meta["sha256"]):
        from app.utils.files import _safe_unlink

        _safe_unlink(meta["storage_path"])
        raise Conflict("This file is already in the agent's knowledge base.")

    doc = await agent_repo.create_document(
        db, agent_id=agent.id, version_id=draft.id, owner_id=owner.id, **meta
    )
    await db.commit()
    celery_app.send_task(INGEST_AGENT_TASK, args=[str(doc.id)])
    return doc, draft


async def list_knowledge(db: AsyncSession, agent: Agent, version_id: str):
    version = await _owned_version_or_404(db, agent, version_id)
    return await agent_repo.list_documents(db, version.id)


async def delete_knowledge(db: AsyncSession, agent: Agent, document_id: str) -> None:
    doc = await agent_repo.get_document(db, document_id)
    if doc is None or doc.agent_id != agent.id:
        raise NotFound("Document not found.")
    version = await agent_repo.get_version(db, doc.version_id)
    if version is None or version.status != AgentVersionStatus.draft:
        raise Conflict("Knowledge can only be edited on the draft version.")

    version_id, doc_id, path = str(doc.version_id), str(doc.id), doc.storage_path
    await agent_repo.delete_document(db, doc)
    await db.flush()
    await delete_by_agent_document(version_id, doc_id)
    from app.utils.files import _safe_unlink

    _safe_unlink(path)


#  versions / deploy 
async def list_versions(db: AsyncSession, agent: Agent) -> list[AgentVersion]:
    return await agent_repo.list_versions(db, agent.id)


async def _owned_version_or_404(db: AsyncSession, agent: Agent, version_id: str) -> AgentVersion:
    version = await agent_repo.get_version(db, version_id)
    if version is None or version.agent_id != agent.id:
        raise NotFound("Version not found.")
    return version


def _build_change_summary(draft: AgentVersion, parent: AgentVersion | None, note: str | None) -> str:
    parts: list[str] = []
    if parent is None:
        parts.append("Initial version.")
    else:
        if (draft.system_prompt or "") != (parent.system_prompt or ""):
            parts.append("Prompt updated.")
        if (draft.model or None) != (parent.model or None):
            parts.append("Model changed.")
    if note:
        parts.append(note.strip())
    return " ".join(parts) or "No description."


async def deploy(
    db: AsyncSession, agent: Agent, *, mode: str, change_summary: str | None
) -> AgentVersion:
    if mode not in ("test", "live"):
        raise ServiceError("mode must be 'test' or 'live'.", status_code=400)
    draft = await agent_repo.get_draft(db, agent.id)
    if draft is None:
        raise Conflict("No draft to deploy. Create or edit a draft first.")

    parent = await agent_repo.get_version(db, draft.parent_version_id) if draft.parent_version_id else None
    summary = _build_change_summary(draft, parent, change_summary)
    number = await agent_repo.next_version_number(db, agent.id)
    status = AgentVersionStatus.live if mode == "live" else AgentVersionStatus.test

    published = await agent_repo.publish_version(
        db, draft, number=number, status=status, change_summary=summary
    )
    if status == AgentVersionStatus.live:
        agent.default_version_id = published.id  # newest live becomes default
    await db.flush()
    return published


async def promote(db: AsyncSession, agent: Agent, version_id: str) -> AgentVersion:
    """Make an already-published version the live default (move between versions)."""
    version = await _owned_version_or_404(db, agent, version_id)
    if version.version_number is None:
        raise Conflict("Only a published version can be made default.")
    version.status = AgentVersionStatus.live
    agent.default_version_id = version.id
    await db.flush()
    return version


#  sharing / access control 
async def configure_sharing(
    db: AsyncSession,
    agent: Agent,
    *,
    visibility: str,
    public_key: str | None = None,
) -> Agent:
    if visibility not in (v.value for v in AgentVisibility):
        raise ServiceError("Invalid visibility.", status_code=400)
    agent.visibility = AgentVisibility(visibility)

    if agent.visibility == AgentVisibility.public:
        if not agent.public_slug:
            agent.public_slug = await _unique_slug(db)
        if public_key:
            agent.public_key_hash = sha256_hex(public_key)
    await db.flush()
    return agent


async def _unique_slug(db: AsyncSession) -> str:
    for _ in range(10):
        slug = secrets.token_urlsafe(8).replace("_", "").replace("-", "")[:12].lower()
        if slug and not await agent_repo.slug_exists(db, slug):
            return slug
    raise ServiceError("Could not allocate a public slug.", status_code=500)


async def grant_access(db: AsyncSession, agent: Agent, email: str) -> None:
    user = await user_repo.get_by_email(db, email.strip().lower())
    if user is None:
        raise NotFound("No user with that email.")
    await agent_repo.grant_access(db, agent.id, user.id)


async def revoke_access(db: AsyncSession, agent: Agent, user_id: str) -> None:
    uid = uuid.UUID(user_id)
    await agent_repo.revoke_access(db, agent.id, uid)


async def list_access(db: AsyncSession, agent: Agent):
    return await agent_repo.list_access(db, agent.id)


#  access checks + version resolution for chat 
async def can_chat(db: AsyncSession, agent: Agent, user: User) -> bool:
    if agent.owner_id == user.id:
        return True
    if agent.visibility == AgentVisibility.public:
        return True
    if agent.visibility == AgentVisibility.restricted:
        return await agent_repo.has_access(db, agent.id, user.id)
    return False


async def resolve_chat_version(
    db: AsyncSession, agent: Agent, user: User, requested_version_id: str | None
) -> AgentVersion:
    is_owner = agent.owner_id == user.id
    # owner may target any version (incl. draft)  this is "test mode"
    if is_owner and requested_version_id:
        return await _owned_version_or_404(db, agent, requested_version_id)

    if agent.default_version_id is None:
        raise Conflict("This agent has not been deployed live yet.")
    version = await agent_repo.get_version(db, agent.default_version_id)
    if version is None:
        raise Conflict("This agent has no live version.")
    return version


def verify_public_key(agent: Agent, key: str | None) -> None:
    if agent.visibility != AgentVisibility.public:
        raise NotFound("Agent not found.")
    if agent.public_key_hash:
        if not key or sha256_hex(key) != agent.public_key_hash:
            raise ServiceError("Invalid or missing access key.", status_code=401, code="agent_key")
