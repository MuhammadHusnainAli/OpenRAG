"""Custom-agent request/response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)


class UpdateDraftRequest(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=20000)
    model: str | None = Field(default=None, max_length=128)


class DeployRequest(BaseModel):
    mode: str = Field(pattern="^(test|live)$")
    change_summary: str | None = Field(default=None, max_length=1000)


class SharingRequest(BaseModel):
    visibility: str = Field(pattern="^(private|restricted|public)$")
    public_key: str | None = Field(default=None, max_length=128)


class GrantAccessRequest(BaseModel):
    email: str = Field(max_length=320)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    version_id: uuid.UUID | None = None          # owner-only: chat a specific version
    conversation_id: uuid.UUID | None = None


class PublicChatTurn(BaseModel):
    role: str
    content: str


class PublicChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[PublicChatTurn] | None = None


#  responses
class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    visibility: str
    public_slug: str | None
    default_version_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_number: int | None
    status: str
    system_prompt: str
    model: str | None
    change_summary: str | None
    parent_version_id: uuid.UUID | None
    created_at: dt.datetime
    published_at: dt.datetime | None


class AgentDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    chunk_count: int
    error: str | None = None
    created_at: dt.datetime


class AccessGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: uuid.UUID
    created_at: dt.datetime


class PublicAgentOut(BaseModel):
    name: str
    description: str | None
    needs_key: bool
    is_live: bool
