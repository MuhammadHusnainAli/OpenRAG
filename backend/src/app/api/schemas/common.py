"""Shared response schemas (user, session, document, chat)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None = None
    email_verified: bool
    created_at: dt.datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    content: str
    citations: list | None = None
    created_at: dt.datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    chunk_count: int
    error: str | None = None
    created_at: dt.datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    document_ids: list[uuid.UUID] | None = None
