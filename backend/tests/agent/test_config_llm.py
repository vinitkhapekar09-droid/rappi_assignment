"""Configuration and LLM-adapter tests (no network, no credentials)."""

from __future__ import annotations

import json

import pytest

from app.agent.adapters.reference_domain import ReferenceDomainService
from app.agent.config import AgentConfig
from app.agent.errors import ConfigurationError
from app.agent.explanation import validate_and_sanitize
from app.agent.llm import build_llm
from app.agent.llm.offline import OfflineLLMClient
from app.agent.llm.openai_compatible import OpenAICompatibleLLMClient
from app.agent.models import ExplanationContext
from tests.agent.support import make_case, make_evidence


def test_default_provider_is_offline() -> None:
    config = AgentConfig.from_env({})
    assert config.llm_provider == "offline"
    assert config.is_offline is True
    assert isinstance(build_llm(config), OfflineLLMClient)


def test_openai_provider_requires_credentials() -> None:
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env({"AGENT_LLM_PROVIDER": "openai_compatible"})


def test_openai_provider_builds_with_credentials() -> None:
    config = AgentConfig.from_env(
        {
            "AGENT_LLM_PROVIDER": "openai_compatible",
            "AGENT_LLM_MODEL": "some-model",
            "AGENT_LLM_API_KEY": "secret",
        }
    )
    assert config.is_offline is False
    client = build_llm(config)
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.is_offline is False


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env({"AGENT_LLM_PROVIDER": "banana"})


def test_offline_override_forces_offline_even_with_openai_provider() -> None:
    config = AgentConfig.from_env(
        {"AGENT_LLM_PROVIDER": "openai_compatible", "AGENT_OFFLINE": "1"}
    )
    assert config.is_offline is True
    assert isinstance(build_llm(config), OfflineLLMClient)


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        AgentConfig.from_env({"AGENT_LLM_TIMEOUT_SECONDS": "soon"})


def test_offline_explanation_passes_the_numeric_guard() -> None:
    case = make_case()
    investigation = ReferenceDomainService().investigate(case, make_evidence())
    context = ExplanationContext(case=case, investigation=investigation)

    raw = OfflineLLMClient().generate_explanation(context)
    explanation, warnings = validate_and_sanitize(raw, context)

    assert warnings == []
    assert explanation.summary


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            {"choices": [{"message": {"content": self.content}}]}
        )


def test_openai_compatible_parses_structured_output_without_network() -> None:
    content = json.dumps({"summary": "Requirement confirmed.", "confidence": "high"})
    session = _FakeSession(content)
    client = OpenAICompatibleLLMClient(
        model="some-model", api_key="secret", session=session
    )
    case = make_case()
    investigation = ReferenceDomainService().investigate(case, make_evidence())
    context = ExplanationContext(case=case, investigation=investigation)

    explanation = client.generate_explanation(context)

    assert explanation.summary == "Requirement confirmed."
    assert session.calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert session.calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_requires_model_and_key() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMClient(model="", api_key="secret")
