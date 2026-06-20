"""The agentic RAG loop, built with LangChain's ``create_agent``.

A fresh agent is constructed per request so its single tool can be bound (by
closure) to the caller's scope — the model can never widen its own data access.
``stream_agent`` serves conversation chat (user/session scope); ``stream_agent_
knowledge`` serves custom agents (agent/version scope + custom prompt + optional
per-agent model). Both yield token + done events for SSE.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from langchain.agents import create_agent
from langchain_community.chat_models import ChatLiteLLM
from langchain_core.messages import AIMessageChunk

from app.agents.prompts import SYSTEM_PROMPT
from app.agents.tools import make_agent_search_tool, make_search_tool
from app.config import get_llm_config, get_settings

_settings = get_settings()


@lru_cache
def _model():
    """Default chat model from config/llm.yml via LiteLLM (Azure/OpenAI/...)."""
    cfg = get_llm_config().llm
    cfg.apply_chat_env()  # export provider env vars LiteLLM expects
    return ChatLiteLLM(
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        model_kwargs=cfg.litellm_kwargs(),  # api_base/api_key/api_version passthrough
    )


@lru_cache
def _model_for(model_name: str):
    """A chat model overriding only the model string (reuses llm.yml provider creds)."""
    cfg = get_llm_config().llm
    cfg.apply_chat_env()
    return ChatLiteLLM(
        model=model_name,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        model_kwargs=cfg.litellm_kwargs(),
    )


def build_rag_agent(
    *,
    user_id: str,
    session_id: str,
    citation_sink: list[dict],
    document_ids: list[str] | None = None,
):
    tool = make_search_tool(
        user_id=user_id,
        session_id=session_id,
        citation_sink=citation_sink,
        document_ids=document_ids,
    )
    return create_agent(_model(), tools=[tool], system_prompt=SYSTEM_PROMPT)


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _dedup(citations: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in citations:
        key = (c["document_id"], c["chunk_index"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


async def _drive(agent, messages: list[dict], usage: dict) -> AsyncGenerator[dict, None]:
    """Stream an agent run, emitting token events and accumulating token usage."""
    config = {"recursion_limit": 2 * _settings.max_tool_iterations + 2}
    async for chunk, _meta in agent.astream(
        {"messages": messages}, stream_mode="messages", config=config
    ):
        if not isinstance(chunk, AIMessageChunk):
            continue
        text = _content_to_text(chunk.content)
        if text:
            yield {"type": "token", "data": text}
        if getattr(chunk, "usage_metadata", None):
            um = chunk.usage_metadata
            usage["input_tokens"] += um.get("input_tokens", 0) or 0
            usage["output_tokens"] += um.get("output_tokens", 0) or 0
            usage["total_tokens"] += um.get("total_tokens", 0) or 0


async def stream_agent(
    *,
    user_id: str,
    session_id: str,
    history: list[dict],
    user_message: str,
    document_ids: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """Conversation chat. Yields token chunks then a terminal done event."""
    citations: list[dict] = []
    agent = build_rag_agent(
        user_id=user_id,
        session_id=session_id,
        citation_sink=citations,
        document_ids=document_ids,
    )
    messages = [*history, {"role": "user", "content": user_message}]
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    async for ev in _drive(agent, messages, usage):
        yield ev
    yield {"type": "done", "citations": _dedup(citations), "usage": usage}


async def stream_agent_knowledge(
    *,
    agent_id: str,
    version_id: str,
    system_prompt: str | None,
    model: str | None,
    history: list[dict],
    user_message: str,
) -> AsyncGenerator[dict, None]:
    """Custom-agent chat scoped to one version's knowledge, with its own prompt."""
    citations: list[dict] = []
    tool = make_agent_search_tool(
        agent_id=agent_id, version_id=version_id, citation_sink=citations
    )
    chat_model = _model_for(model) if model else _model()
    agent = create_agent(
        chat_model, tools=[tool], system_prompt=system_prompt or SYSTEM_PROMPT
    )
    messages = [*history, {"role": "user", "content": user_message}]
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    async for ev in _drive(agent, messages, usage):
        yield ev
    yield {"type": "done", "citations": _dedup(citations), "usage": usage}
