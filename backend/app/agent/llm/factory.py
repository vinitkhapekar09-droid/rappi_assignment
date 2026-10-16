"""Builds the configured LLM adapter."""

from __future__ import annotations

from app.agent.config import AgentConfig
from app.agent.llm.offline import OfflineLLMClient
from app.agent.llm.openai_compatible import OpenAICompatibleLLMClient
from app.agent.ports import LLMClient


def build_llm(config: AgentConfig) -> LLMClient:
    """Return an :class:`~app.agent.ports.LLMClient` for ``config``.

    Fails fast via ``config.validate_for_use`` when a real provider is selected
    without the credentials it needs.
    """
    config.validate_for_use()
    if config.is_offline:
        return OfflineLLMClient()
    return OpenAICompatibleLLMClient(
        model=config.llm_model or "",
        api_key=config.llm_api_key or "",
        base_url=config.llm_base_url,
        timeout=config.llm_timeout_seconds,
    )
