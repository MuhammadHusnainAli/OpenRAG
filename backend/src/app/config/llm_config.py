"""Loader for ``config/llm.yml`` — the LiteLLM model/provider configuration.

Supports ``${VAR}`` and ``${VAR:-default}`` interpolation against the process
environment so secrets stay in ``.env`` and never in source. The chat side
exports the provider env vars LiteLLM expects; the embedding side passes
api_base/api_key/api_version explicitly per call (no global conflict).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")
_LLM_YML = Path(__file__).with_name("llm.yml")


def interpolate(value: str) -> str:
    """Resolve ${VAR} / ${VAR:-default} against os.environ."""

    def repl(m: re.Match) -> str:
        var, default = m.group(1), m.group(2)
        return os.environ.get(var) or (default or "")

    return _ENV_RE.sub(repl, value)


def _resolve(node):
    if isinstance(node, str):
        return interpolate(node)
    if isinstance(node, dict):
        return {k: _resolve(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v) for v in node]
    return node


class ProviderConfig(BaseModel):
    model: str
    api_base: str | None = None
    api_key: str | None = None
    api_version: str | None = None
    deployment_name: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    dimensions: int | None = None

    def _clean(self, value: str | None) -> str | None:
        return value or None

    def litellm_kwargs(self) -> dict:
        """Per-call kwargs for litellm.completion / litellm.aembedding."""
        out: dict = {}
        for key in ("api_base", "api_key", "api_version"):
            val = self._clean(getattr(self, key))
            if val:
                out[key] = val
        return out

    def apply_chat_env(self) -> None:
        """Export provider env vars LiteLLM recognises for the chat model.

        Keyed off the provider prefix of ``model`` so ChatLiteLLM's environment
        validation passes and custom Azure/OpenAI endpoints are honoured.
        """
        provider = self.model.split("/", 1)[0].lower()
        key, base, version = (
            self._clean(self.api_key),
            self._clean(self.api_base),
            self._clean(self.api_version),
        )
        mapping = {
            "azure": ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"),
            "openai": ("OPENAI_API_KEY", "OPENAI_API_BASE", None),
            "anthropic": ("ANTHROPIC_API_KEY", None, None),
            "gemini": ("GEMINI_API_KEY", None, None),
            "groq": ("GROQ_API_KEY", None, None),
        }
        key_env, base_env, ver_env = mapping.get(provider, (None, None, None))
        if key_env and key:
            os.environ.setdefault(key_env, key)
        if base_env and base:
            os.environ.setdefault(base_env, base)
            if provider == "openai":
                os.environ.setdefault("OPENAI_BASE_URL", base)
        if ver_env and version:
            os.environ.setdefault(ver_env, version)


class LLMSettings(BaseModel):
    llm: ProviderConfig
    embedding: ProviderConfig


def load_from_dict(data: dict) -> LLMSettings:
    resolved = _resolve(data)
    return LLMSettings(**resolved)


@lru_cache
def get_llm_config(path: str | None = None) -> LLMSettings:
    target = Path(path) if path else _LLM_YML
    with open(target, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return load_from_dict(data)
