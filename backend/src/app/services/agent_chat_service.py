"""Chat orchestration for custom agents.

Authenticated chat persists to a conversation bound to the agent + version (so
owners and granted users keep history). Public chat (anonymous, key-gated) is
stateless — no DB writes — and accepts optional prior turns from the client.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agents import stream_agent_knowledge
from app.config import get_settings
from app.core import cache
from app.core.logging import get_logger
from app.data.models import Agent, AgentVersion, Conversation, User
from app.data.repositories import conversations as conv_repo
from app.data.repositories import messages as msg_repo
from app.services.errors import BudgetExceeded, NotFound

_settings = get_settings()
log = get_logger("agent_chat")

_MAX_PUBLIC_HISTORY = 20


async def get_or_create_conversation(
    db: AsyncSession, agent: Agent, version: AgentVersion, user: User, conversation_id: str | None
) -> Conversation:
    if conversation_id:
        conv = await conv_repo.get_owned(db, conversation_id, user.id)
        if conv is None or conv.agent_id != agent.id:
            raise NotFound("Conversation not found.")
        return conv
    conv = await conv_repo.create(db, user_id=user.id, title=f"{agent.name}")
    conv.agent_id = agent.id
    conv.agent_version_id = version.id
    await db.flush()
    return conv


async def stream_chat(
    db: AsyncSession,
    agent: Agent,
    version: AgentVersion,
    user: User,
    conversation: Conversation,
    message: str,
) -> AsyncGenerator[dict, None]:
    used = await cache.get_daily_tokens(db, user.id)
    if used >= _settings.max_tokens_per_user_day:
        raise BudgetExceeded("Daily token budget exhausted. Try again tomorrow.")

    history = await msg_repo.history_as_llm(db, conversation.id)
    await msg_repo.add(db, conversation.id, "user", message)
    await db.commit()

    collected, citations, usage = "", [], {}
    try:
        async for ev in stream_agent_knowledge(
            agent_id=str(agent.id),
            version_id=str(version.id),
            system_prompt=version.system_prompt,
            model=version.model,
            history=history,
            user_message=message,
        ):
            if ev["type"] == "token":
                collected += ev["data"]
                yield ev
            elif ev["type"] == "done":
                citations = ev["citations"]
                usage = ev.get("usage", {})
    except Exception as exc:  # noqa: BLE001
        log.error("agent_chat.error", agent_id=str(agent.id), error=str(exc))
        if collected:
            await msg_repo.add(db, conversation.id, "assistant", collected, citations=citations)
            await db.commit()
        yield {"type": "error", "data": "The agent hit an error. Please retry."}
        return

    await msg_repo.add(
        db, conversation.id, "assistant", collected, citations=citations, token_usage=usage or None
    )
    total = int(usage.get("total_tokens", 0)) if usage else 0
    if total:
        await cache.add_daily_tokens(db, user.id, total)
    await db.commit()
    yield {"type": "done", "citations": citations, "conversation_id": str(conversation.id)}


async def stream_public(
    agent: Agent,
    version: AgentVersion,
    message: str,
    history: list[dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """Anonymous, stateless agent chat (no persistence)."""
    clean_history = [
        {"role": h["role"], "content": h["content"]}
        for h in (history or [])
        if h.get("role") in ("user", "assistant") and h.get("content")
    ][-_MAX_PUBLIC_HISTORY:]

    citations = []
    try:
        async for ev in stream_agent_knowledge(
            agent_id=str(agent.id),
            version_id=str(version.id),
            system_prompt=version.system_prompt,
            model=version.model,
            history=clean_history,
            user_message=message,
        ):
            if ev["type"] == "token":
                yield ev
            elif ev["type"] == "done":
                citations = ev["citations"]
    except Exception as exc:  # noqa: BLE001
        log.error("agent_chat.public_error", agent_id=str(agent.id), error=str(exc))
        yield {"type": "error", "data": "The agent hit an error. Please retry."}
        return
    yield {"type": "done", "citations": citations}
