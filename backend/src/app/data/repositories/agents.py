"""Data access for custom agents, their versions, knowledge docs, and access grants.

All owner reads are ownership-scoped; not-owned resolves to None (=> 404).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    Agent,
    AgentAccess,
    AgentDocument,
    AgentVersion,
    AgentVersionStatus,
    DocStatus,
)


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


#  agents 
async def create_agent(
    db: AsyncSession, *, owner_id: uuid.UUID, name: str, description: str | None
) -> Agent:
    agent = Agent(owner_id=owner_id, name=name, description=description)
    db.add(agent)
    await db.flush()
    return agent


async def get_agent(db: AsyncSession, agent_id) -> Agent | None:
    aid = _as_uuid(agent_id)
    return await db.get(Agent, aid) if aid else None


async def get_owned_agent(db: AsyncSession, agent_id, owner_id: uuid.UUID) -> Agent | None:
    agent = await get_agent(db, agent_id)
    if agent is None or agent.owner_id != owner_id:
        return None
    return agent


async def get_agent_by_slug(db: AsyncSession, slug: str) -> Agent | None:
    res = await db.execute(select(Agent).where(Agent.public_slug == slug))
    return res.scalar_one_or_none()


async def list_owned_agents(db: AsyncSession, owner_id: uuid.UUID) -> list[Agent]:
    res = await db.execute(
        select(Agent).where(Agent.owner_id == owner_id).order_by(Agent.updated_at.desc())
    )
    return list(res.scalars().all())


async def list_shared_agents(db: AsyncSession, user_id: uuid.UUID) -> list[Agent]:
    res = await db.execute(
        select(Agent)
        .join(AgentAccess, AgentAccess.agent_id == Agent.id)
        .where(AgentAccess.user_id == user_id)
        .order_by(Agent.updated_at.desc())
    )
    return list(res.scalars().all())


async def slug_exists(db: AsyncSession, slug: str) -> bool:
    res = await db.execute(select(Agent.id).where(Agent.public_slug == slug))
    return res.first() is not None


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    await db.delete(agent)
    await db.flush()


# versions 
async def create_version(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    system_prompt: str = "",
    model: str | None = None,
    parent_version_id: uuid.UUID | None = None,
    status: AgentVersionStatus = AgentVersionStatus.draft,
) -> AgentVersion:
    version = AgentVersion(
        agent_id=agent_id,
        system_prompt=system_prompt,
        model=model,
        parent_version_id=parent_version_id,
        status=status,
    )
    db.add(version)
    await db.flush()
    return version


async def get_version(db: AsyncSession, version_id) -> AgentVersion | None:
    vid = _as_uuid(version_id)
    return await db.get(AgentVersion, vid) if vid else None


async def get_draft(db: AsyncSession, agent_id: uuid.UUID) -> AgentVersion | None:
    res = await db.execute(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.status == AgentVersionStatus.draft,
        )
    )
    return res.scalar_one_or_none()


async def list_versions(db: AsyncSession, agent_id: uuid.UUID) -> list[AgentVersion]:
    res = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.created_at.desc())
    )
    return list(res.scalars().all())


async def next_version_number(db: AsyncSession, agent_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
            AgentVersion.agent_id == agent_id
        )
    )
    return int(res.scalar() or 0) + 1


async def latest_published(db: AsyncSession, agent_id: uuid.UUID) -> AgentVersion | None:
    res = await db.execute(
        select(AgentVersion)
        .where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.version_number.is_not(None),
        )
        .order_by(AgentVersion.version_number.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def publish_version(
    db: AsyncSession,
    version: AgentVersion,
    *,
    number: int,
    status: AgentVersionStatus,
    change_summary: str | None,
) -> AgentVersion:
    version.version_number = number
    version.status = status
    version.change_summary = change_summary
    version.published_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
    return version


#  agent documents (knowledge)
async def create_document(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    owner_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_path: str,
) -> AgentDocument:
    doc = AgentDocument(
        agent_id=agent_id,
        version_id=version_id,
        owner_id=owner_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_path=storage_path,
        status=DocStatus.pending,
    )
    db.add(doc)
    await db.flush()
    return doc


async def get_document(db: AsyncSession, document_id) -> AgentDocument | None:
    did = _as_uuid(document_id)
    return await db.get(AgentDocument, did) if did else None


async def list_documents(db: AsyncSession, version_id: uuid.UUID) -> list[AgentDocument]:
    res = await db.execute(
        select(AgentDocument)
        .where(AgentDocument.version_id == version_id)
        .order_by(AgentDocument.created_at.asc())
    )
    return list(res.scalars().all())


async def count_documents(db: AsyncSession, version_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.count()).select_from(AgentDocument).where(
            AgentDocument.version_id == version_id
        )
    )
    return int(res.scalar() or 0)


async def total_size(db: AsyncSession, version_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.coalesce(func.sum(AgentDocument.size_bytes), 0)).where(
            AgentDocument.version_id == version_id
        )
    )
    return int(res.scalar() or 0)


async def exists_sha(db: AsyncSession, version_id: uuid.UUID, sha256: str) -> bool:
    res = await db.execute(
        select(AgentDocument.id).where(
            AgentDocument.version_id == version_id, AgentDocument.sha256 == sha256
        )
    )
    return res.first() is not None


async def set_doc_status(
    db: AsyncSession,
    doc: AgentDocument,
    status: DocStatus,
    *,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    doc.status = status
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    doc.error = error
    await db.flush()


async def delete_document(db: AsyncSession, doc: AgentDocument) -> None:
    await db.delete(doc)
    await db.flush()


#  access grants 
async def grant_access(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> None:
    exists = await db.get(AgentAccess, {"agent_id": agent_id, "user_id": user_id})
    if exists is None:
        db.add(AgentAccess(agent_id=agent_id, user_id=user_id))
        await db.flush()


async def revoke_access(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> None:
    grant = await db.get(AgentAccess, {"agent_id": agent_id, "user_id": user_id})
    if grant is not None:
        await db.delete(grant)
        await db.flush()


async def has_access(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    grant = await db.get(AgentAccess, {"agent_id": agent_id, "user_id": user_id})
    return grant is not None


async def list_access(db: AsyncSession, agent_id: uuid.UUID) -> list[AgentAccess]:
    res = await db.execute(select(AgentAccess).where(AgentAccess.agent_id == agent_id))
    return list(res.scalars().all())
