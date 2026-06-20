"""Custom-agent pure logic: change-summary diffing + public-key gating."""

from __future__ import annotations

import pytest

# agent_service pulls celery/rag/litellm; skip cleanly if those aren't installed
pytest.importorskip("celery")
pytest.importorskip("qdrant_client")

from app.data.models import Agent, AgentVersion, AgentVisibility  # noqa: E402
from app.services import agent_service  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402


def _v(prompt="", model=None):
    return AgentVersion(agent_id=None, system_prompt=prompt, model=model)


def test_change_summary_initial():
    summary = agent_service._build_change_summary(_v("hi"), None, None)
    assert "Initial version" in summary


def test_change_summary_detects_prompt_and_model():
    parent = _v("old", "openai/gpt-4o")
    draft = _v("new", "openai/gpt-4o-mini")
    summary = agent_service._build_change_summary(draft, parent, "tuned retrieval")
    assert "Prompt updated" in summary
    assert "Model changed" in summary
    assert "tuned retrieval" in summary


def test_change_summary_no_diff():
    parent = _v("same", None)
    draft = _v("same", None)
    assert agent_service._build_change_summary(draft, parent, None) == "No description."


def test_public_key_required_when_set():
    agent = Agent(visibility=AgentVisibility.public, public_key_hash="deadbeef")
    with pytest.raises(ServiceError):
        agent_service.verify_public_key(agent, "wrong-key")


def test_public_key_absent_allows_open_access():
    agent = Agent(visibility=AgentVisibility.public, public_key_hash=None)
    agent_service.verify_public_key(agent, None)  # no raise


def test_non_public_agent_is_hidden():
    agent = Agent(visibility=AgentVisibility.private, public_key_hash=None)
    with pytest.raises(ServiceError):
        agent_service.verify_public_key(agent, "anything")
