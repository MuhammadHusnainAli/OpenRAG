"""The agent's only tool: read-only document search, scoped to the caller.

Security-critical: the model controls only ``query`` / ``search_type`` /
``top_k``. The server binds ``user_id``, ``session_id`` and the optional
``document_ids`` via closure — they are NOT part of the tool's argument schema,
so no instruction in a document can make the agent read another tenant's data.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rag.search import search_agent_knowledge, search_documents

_DESCRIPTION = (
    "Search the user's uploaded documents for relevant passages. Call this "
    "whenever the answer might be in the documents. Rewrite the user's question "
    "into a focused search query. Returns passages tagged [source:chunk_index]."
)


class SearchArgs(BaseModel):
    query: str = Field(..., description="Focused search query.")
    search_type: Literal["hybrid", "dense", "sparse"] = Field(
        "hybrid", description="Retrieval mode; 'hybrid' (default) fuses dense + sparse."
    )
    top_k: int = Field(8, ge=1, le=20, description="Number of passages to return.")


def make_search_tool(
    *,
    user_id: str,
    session_id: str,
    citation_sink: list[dict],
    document_ids: list[str] | None = None,
) -> StructuredTool:
    async def _run(query: str, search_type: str = "hybrid", top_k: int = 8) -> str:
        hits = await search_documents(
            user_id=user_id,
            session_id=session_id,
            query=query,
            search_type=search_type,  # type: ignore[arg-type]
            top_k=top_k,
            document_ids=document_ids,
        )
        for h in hits:
            citation_sink.append(
                {
                    "document_id": h["document_id"],
                    "chunk_index": h["chunk_index"],
                    "source": h["source"],
                    "score": h["score"],
                }
            )
        if not hits:
            return "No matching passages were found in the user's documents."
        return "\n\n".join(
            f"[{h['source']}:{h['chunk_index']}] {h['text']}" for h in hits
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name="search_documents",
        description=_DESCRIPTION,
        args_schema=SearchArgs,
    )


def make_agent_search_tool(
    *,
    agent_id: str,
    version_id: str,
    citation_sink: list[dict],
) -> StructuredTool:
    """Search tool bound to a custom agent's version-scoped knowledge base.

    The model controls only query/search_type/top_k; the server binds agent_id and
    version_id by closure so retrieval can never escape this agent/version.
    """

    async def _run(query: str, search_type: str = "hybrid", top_k: int = 8) -> str:
        hits = await search_agent_knowledge(
            agent_id=agent_id,
            version_id=version_id,
            query=query,
            search_type=search_type,  # type: ignore[arg-type]
            top_k=top_k,
        )
        for h in hits:
            citation_sink.append(
                {
                    "document_id": h["document_id"],
                    "chunk_index": h["chunk_index"],
                    "source": h["source"],
                    "score": h["score"],
                }
            )
        if not hits:
            return "No matching passages were found in this agent's knowledge base."
        return "\n\n".join(f"[{h['source']}:{h['chunk_index']}] {h['text']}" for h in hits)

    return StructuredTool.from_function(
        coroutine=_run,
        name="search_documents",
        description=_DESCRIPTION,
        args_schema=SearchArgs,
    )
