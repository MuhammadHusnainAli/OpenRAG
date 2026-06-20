"""Conversation (session) orchestration. Deleting a session also removes its
Qdrant points so no vectors are orphaned."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Conversation, User
from app.data.repositories import conversations as conv_repo
from app.data.repositories import messages as msg_repo
from app.rag.qdrant import delete_by_session
from app.services.errors import NotFound
from app.utils.files import remove_chat_dir


async def create(db: AsyncSession, user: User, title: str | None) -> Conversation:
    return await conv_repo.create(db, user_id=user.id, title=title or "New chat")


async def list_for_user(db: AsyncSession, user: User) -> list[Conversation]:
    return await conv_repo.list_for_user(db, user.id)


async def get_owned_or_404(
    db: AsyncSession, conversation_id: str, user: User
) -> Conversation:
    conv = await conv_repo.get_owned(db, conversation_id, user.id)
    if conv is None:
        raise NotFound("Conversation not found.")
    return conv


async def get_with_messages(db: AsyncSession, conversation_id: str, user: User):
    conv = await get_owned_or_404(db, conversation_id, user)
    messages = await msg_repo.list_for_conversation(db, conv.id)
    return conv, messages


async def rename(db: AsyncSession, conversation_id: str, user: User, title: str) -> Conversation:
    conv = await get_owned_or_404(db, conversation_id, user)
    return await conv_repo.rename(db, conv, title)


async def delete(db: AsyncSession, conversation_id: str, user: User) -> None:
    conv = await get_owned_or_404(db, conversation_id, user)
    session_id = str(conv.id)
    conv_id = conv.id
    await conv_repo.delete(db, conv)             # cascades docs + messages in PG
    await delete_by_session(str(user.id), session_id)  # remove vectors in Qdrant
    remove_chat_dir(user.id, conv_id)            # remove blobs from the volume
