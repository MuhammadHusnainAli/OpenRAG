"""Document upload / list / delete within a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import DocumentOut
from app.core.db import get_db
from app.core.deps import current_user
from app.services import document_service, session_service

router = APIRouter(prefix="/api/sessions", tags=["documents"])


@router.post(
    "/{session_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    session_id: str,
    file: UploadFile = File(...),
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await session_service.get_owned_or_404(db, session_id, user)
    return await document_service.upload(db, conv, user, file)


@router.get("/{session_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    session_id: str, user=Depends(current_user), db: AsyncSession = Depends(get_db)
):
    conv = await session_service.get_owned_or_404(db, session_id, user)
    return await document_service.list_for_session(db, conv)


@router.delete(
    "/{session_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(
    session_id: str,
    document_id: str,
    user=Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # ownership of the session is implied by ownership of the document
    await session_service.get_owned_or_404(db, session_id, user)
    await document_service.delete(db, document_id, user)
