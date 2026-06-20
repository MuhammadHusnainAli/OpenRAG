"""llm.yml interpolation + config loading."""

from __future__ import annotations

from app.config.llm_config import interpolate, load_from_dict


def test_interpolate_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-123")
    assert interpolate("${MY_KEY}") == "secret-123"


def test_interpolate_default_used_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert interpolate("${MISSING_VAR:-fallback}") == "fallback"


def test_interpolate_empty_when_no_default(monkeypatch):
    monkeypatch.delenv("ALSO_MISSING", raising=False)
    assert interpolate("${ALSO_MISSING}") == ""


def test_load_from_dict_builds_provider_configs(monkeypatch):
    monkeypatch.setenv("AZ_KEY", "azkey")
    cfg = load_from_dict(
        {
            "llm": {
                "model": "azure/gpt-4o-dep",
                "api_base": "https://x.openai.azure.com",
                "api_key": "${AZ_KEY}",
                "api_version": "2024-08-01-preview",
                "temperature": 0,
            },
            "embedding": {
                "model": "azure/embed-dep",
                "api_key": "${AZ_KEY}",
                "dimensions": 1536,
            },
        }
    )
    assert cfg.llm.model == "azure/gpt-4o-dep"
    assert cfg.llm.api_key == "azkey"
    assert cfg.embedding.dimensions == 1536


def test_litellm_kwargs_filters_empty():
    cfg = load_from_dict(
        {
            "llm": {"model": "openai/gpt-4o", "api_base": "", "api_key": "k"},
            "embedding": {"model": "openai/text-embedding-3-large", "dimensions": 3072},
        }
    )
    kwargs = cfg.llm.litellm_kwargs()
    assert kwargs == {"api_key": "k"}  # empty api_base/api_version dropped


def test_apply_chat_env_exports_azure(monkeypatch):
    for var in ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_from_dict(
        {
            "llm": {
                "model": "azure/dep",
                "api_base": "https://x.openai.azure.com",
                "api_key": "kk",
                "api_version": "2024-08-01-preview",
            },
            "embedding": {"model": "openai/text-embedding-3-large", "dimensions": 3072},
        }
    )
    cfg.llm.apply_chat_env()
    import os

    assert os.environ["AZURE_API_KEY"] == "kk"
    assert os.environ["AZURE_API_BASE"] == "https://x.openai.azure.com"
    assert os.environ["AZURE_API_VERSION"] == "2024-08-01-preview"
