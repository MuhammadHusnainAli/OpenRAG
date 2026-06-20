"""Embeddings: dense (cloud, via LiteLLM) + sparse (local, FastEmbed BM25).

Dense embeddings go through LiteLLM so any provider works (OpenAI, Azure, etc.)
using the config in ``config/llm.yml`` — api_base/api_key/api_version are passed
explicitly per call. Cloud APIs return only dense vectors, so the sparse side
runs locally on CPU to enable true hybrid search.
"""

from __future__ import annotations

from functools import lru_cache

import litellm
from qdrant_client import models

from app.config import get_llm_config


def _embedding_cfg():
    return get_llm_config().embedding


@lru_cache
def _sparse_embedder():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name="Qdrant/bm25")


async def embed_dense(texts: list[str]) -> list[list[float]]:
    cfg = _embedding_cfg()
    resp = await litellm.aembedding(model=cfg.model, input=texts, **cfg.litellm_kwargs())
    return [item["embedding"] for item in resp["data"]]


async def embed_dense_query(text: str) -> list[float]:
    return (await embed_dense([text]))[0]


def embed_sparse(texts: list[str]) -> list[models.SparseVector]:
    out: list[models.SparseVector] = []
    for emb in _sparse_embedder().embed(texts):
        out.append(
            models.SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())
        )
    return out
