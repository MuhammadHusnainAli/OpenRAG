"""Agentic chat — Server-Sent Events stream."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import ChatRequest
from app.core.db import get_db
from app.core.deps import current_user
from app.services import chat_service, session_service

router = APIRouter(prefix="/api/sessions", tags=["chat"])


@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    body: ChatRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await session_service.get_owned_or_404(db, session_id, user)
    document_ids = [str(d) for d in body.document_ids] if body.document_ids else None

    async def event_stream():
        async for ev in chat_service.stream_chat(db, conv, user, body.message, document_ids):
            if ev["type"] == "token":
                yield f"data: {json.dumps({'token': ev['data']})}\n\n"
            elif ev["type"] == "done":
                yield f"data: {json.dumps({'done': True, 'citations': ev['citations']})}\n\n"
            elif ev["type"] == "error":
                yield f"data: {json.dumps({'error': ev['data']})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
