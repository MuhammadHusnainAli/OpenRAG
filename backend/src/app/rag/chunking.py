"""Token-aware recursive chunking (~800 tokens, 120 overlap)."""

from __future__ import annotations

from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter


@lru_cache
def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=5000,
        chunk_overlap=1000,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def chunk(text: str) -> list[str]:
    return [c for c in _splitter().split_text(text) if c.strip()]
