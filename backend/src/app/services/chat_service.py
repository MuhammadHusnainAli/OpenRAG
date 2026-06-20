"""Chat orchestration: budget enforcement, history load, agent streaming, and
persistence of both turns (history lives in Postgres).

Yields event dicts the router serialises as SSE:
  {"type": "token", "data": "..."}             incremental assistant text
  {"type": "done",  "citations": [...]}        terminal event
  {"type": "error", "data": "..."}             terminal error event
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agents import stream_agent
from app.config import get_settings
from app.core import cache
from app.core.logging import get_logger
from app.data.models import Conversation, User
from app.data.repositories import messages as msg_repo
from app.services.errors import BudgetExceeded

_settings = get_settings()
log = get_logger("chat")


async def stream_chat(
    db: AsyncSession,
    conv: Conversation,
    user: User,
    message: str,
    document_ids: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    # 1) cost guardrail — refuse new turns over the daily budget
    used = await cache.get_daily_tokens(db, user.id)
    if used >= _settings.max_tokens_per_user_day:
        raise BudgetExceeded("Daily token budget exhausted. Try again tomorrow.")

    # 2) load prior history, then persist the new user turn
    history = await msg_repo.history_as_llm(db, conv.id)
    await msg_repo.add(db, conv.id, "user", message)
    await db.commit()

    collected = ""
    citations: list[dict] = []
    usage: dict = {}

    try:
        async for ev in stream_agent(
            user_id=str(user.id),
            session_id=str(conv.id),
            history=history,
            user_message=message,
            document_ids=document_ids,
        ):
            if ev["type"] == "token":
                collected += ev["data"]
                yield ev
            elif ev["type"] == "done":
                citations = ev["citations"]
                usage = ev.get("usage", {})
    except Exception as exc:  # noqa: BLE001
        log.error("chat.agent_error", conversation_id=str(conv.id), error=str(exc))
        # persist whatever we streamed so history stays consistent
        if collected:
            await msg_repo.add(db, conv.id, "assistant", collected, citations=citations)
            await db.commit()
        yield {"type": "error", "data": "The assistant hit an error. Please retry."}
        return

    # 3) persist assistant turn + account for tokens
    await msg_repo.add(
        db, conv.id, "assistant", collected, citations=citations, token_usage=usage or None
    )
    total_tokens = int(usage.get("total_tokens", 0)) if usage else 0
    if total_tokens:
        await cache.add_daily_tokens(db, user.id, total_tokens)
    await db.commit()

    yield {"type": "done", "citations": citations}
