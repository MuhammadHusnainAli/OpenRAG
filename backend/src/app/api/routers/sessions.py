"""Conversation (session) CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import (
    CreateSessionRequest,
    RenameSessionRequest,
    SessionDetailOut,
    SessionOut,
)
from app.core.db import get_db
from app.core.deps import current_user
from app.services import session_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
async def list_sessions(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await session_service.list_for_user(db, user)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest, user=Depends(current_user), db: AsyncSession = Depends(get_db)
):
    return await session_service.create(db, user, body.title)


@router.get("/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)
):
    conv, messages = await session_service.get_with_messages(db, session_id, user)
    return SessionDetailOut(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await session_service.rename(db, session_id, user, body.title)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)
):
    await session_service.delete(db, session_id, user)
