"""Custom-agent endpoints: CRUD, knowledge base, versions, deploy (test/live),
access control, public sharing, and chat (authenticated + public)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.agents import (
    AccessGrantOut,
    AgentChatRequest,
    AgentDocumentOut,
    AgentOut,
    CreateAgentRequest,
    DeployRequest,
    GrantAccessRequest,
    PublicAgentOut,
    PublicChatRequest,
    SharingRequest,
    UpdateAgentRequest,
    UpdateDraftRequest,
    VersionOut,
)
from app.core.db import get_db
from app.core.deps import current_user
from app.data.repositories import agents as agent_repo
from app.services import agent_chat_service, agent_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/agents", tags=["agents"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _sse(ev: dict) -> str:
    if ev["type"] == "token":
        return f"data: {json.dumps({'token': ev['data']})}\n\n"
    if ev["type"] == "done":
        payload = {"done": True, "citations": ev.get("citations", [])}
        if ev.get("conversation_id"):
            payload["conversation_id"] = ev["conversation_id"]
        return f"data: {json.dumps(payload)}\n\n"
    if ev["type"] == "error":
        return f"data: {json.dumps({'error': ev['data']})}\n\n"
    return ""


# ── public (no auth — declared before /{agent_id} to avoid route capture) ──────────
@router.get("/public/{slug}", response_model=PublicAgentOut)
async def public_meta(slug: str, db: AsyncSession = Depends(get_db)):
    agent = await agent_repo.get_agent_by_slug(db, slug)
    if agent is None or agent.visibility.value != "public":
        raise ServiceError("Agent not found.", status_code=404, code="not_found")
    return PublicAgentOut(
        name=agent.name,
        description=agent.description,
        needs_key=bool(agent.public_key_hash),
        is_live=agent.default_version_id is not None,
    )


@router.post("/public/{slug}/chat")
async def public_chat(
    slug: str,
    body: PublicChatRequest,
    x_agent_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_repo.get_agent_by_slug(db, slug)
    if agent is None:
        raise ServiceError("Agent not found.", status_code=404, code="not_found")
    agent_service.verify_public_key(agent, x_agent_key)  # raises 401 on bad key

    if agent.default_version_id is None:
        raise ServiceError("This agent is not live yet.", status_code=409, code="conflict")
    version = await agent_repo.get_version(db, agent.default_version_id)

    history = [t.model_dump() for t in (body.history or [])]

    async def event_stream():
        async for ev in agent_chat_service.stream_public(agent, version, body.message, history):
            yield _sse(ev)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── shared-with-me ───────────────────────────────────────────────────────────────
@router.get("/shared", response_model=list[AgentOut])
async def list_shared(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await agent_service.list_shared(db, user)


# ── agent CRUD ──────────────────────────────────────────────────────────────────
@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: CreateAgentRequest, user=Depends(current_user), db: AsyncSession = Depends(get_db)
):
    return await agent_service.create_agent(db, user, name=body.name, description=body.description)


@router.get("", response_model=list[AgentOut])
async def list_agents(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await agent_service.list_owned(db, user)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await agent_service.get_owned_or_404(db, agent_id, user)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.update_agent(
        db, agent, name=body.name, description=body.description
    )


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    await agent_service.delete_agent(db, agent)


# ── draft editing ───────────────────────────────────────────────────────────────
@router.post("/{agent_id}/draft", response_model=VersionOut)
async def create_draft(agent_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.ensure_draft(db, agent)


@router.patch("/{agent_id}/draft", response_model=VersionOut)
async def update_draft(
    agent_id: str,
    body: UpdateDraftRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.update_draft(
        db, agent, system_prompt=body.system_prompt, model=body.model
    )


# ── knowledge base ──────────────────────────────────────────────────────────────
@router.post(
    "/{agent_id}/knowledge", response_model=AgentDocumentOut, status_code=status.HTTP_201_CREATED
)
async def upload_knowledge(
    agent_id: str,
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    doc, _draft = await agent_service.upload_knowledge(db, agent, user, file)
    return doc


@router.get("/{agent_id}/versions/{version_id}/knowledge", response_model=list[AgentDocumentOut])
async def list_knowledge(
    agent_id: str,
    version_id: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.list_knowledge(db, agent, version_id)


@router.delete("/{agent_id}/knowledge/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    agent_id: str,
    document_id: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    await agent_service.delete_knowledge(db, agent, document_id)


# ── versions / deploy ──────────────────────────────────────────────────────────────
@router.get("/{agent_id}/versions", response_model=list[VersionOut])
async def list_versions(agent_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.list_versions(db, agent)


@router.post("/{agent_id}/deploy", response_model=VersionOut)
async def deploy(
    agent_id: str,
    body: DeployRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.deploy(db, agent, mode=body.mode, change_summary=body.change_summary)


@router.post("/{agent_id}/versions/{version_id}/promote", response_model=VersionOut)
async def promote_version(
    agent_id: str,
    version_id: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.promote(db, agent, version_id)


# ── sharing / access ───────────────────────────────────────────────────────────────
@router.patch("/{agent_id}/sharing", response_model=AgentOut)
async def configure_sharing(
    agent_id: str,
    body: SharingRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.configure_sharing(
        db, agent, visibility=body.visibility, public_key=body.public_key
    )


@router.get("/{agent_id}/access", response_model=list[AccessGrantOut])
async def list_access(agent_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    return await agent_service.list_access(db, agent)


@router.post("/{agent_id}/access", status_code=status.HTTP_204_NO_CONTENT)
async def grant_access(
    agent_id: str,
    body: GrantAccessRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    await agent_service.grant_access(db, agent, body.email)


@router.delete("/{agent_id}/access/{grant_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_access(
    agent_id: str,
    grant_user_id: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_service.get_owned_or_404(db, agent_id, user)
    await agent_service.revoke_access(db, agent, grant_user_id)


# ── authenticated chat ─────────────────────────────────────────────────────────────
@router.post("/{agent_id}/chat")
async def chat(
    agent_id: str,
    body: AgentChatRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agent_repo.get_agent(db, agent_id)
    if agent is None or not await agent_service.can_chat(db, agent, user):
        raise ServiceError("Agent not found.", status_code=404, code="not_found")

    requested = str(body.version_id) if body.version_id else None
    version = await agent_service.resolve_chat_version(db, agent, user, requested)
    conversation = await agent_chat_service.get_or_create_conversation(
        db, agent, version, user, str(body.conversation_id) if body.conversation_id else None
    )

    async def event_stream():
        async for ev in agent_chat_service.stream_chat(
            db, agent, version, user, conversation, body.message
        ):
            yield _sse(ev)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
