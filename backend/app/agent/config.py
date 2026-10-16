"""Agent configuration, sourced from the environment with safe offline defaults.

The default provider is ``offline``: deterministic, credential-free, and
network-free. Selecting a real provider without the credentials it needs raises
:class:`ConfigurationError` immediately, so a misconfigured run fails visibly
instead of being mistaken for a successful offline run.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.errors import ConfigurationError

LLMProvider = Literal["offline", "openai_compatible"]

_TRUE_VALUES = {"1", "true", "yes", "on"}


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm_provider: LLMProvider = "offline"
    llm_model: str | None = None
    llm_api_key: str | None = Field(default=None, repr=False)
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    offline: bool = False

    @property
    def is_offline(self) -> bool:
        """Offline if explicitly forced, or if the offline provider is selected."""
        return self.offline or self.llm_provider == "offline"

    def validate_for_use(self) -> AgentConfig:
        if self.llm_provider == "openai_compatible" and not self.offline:
            missing = [
                name
                for name, value in (
                    ("AGENT_LLM_MODEL", self.llm_model),
                    ("AGENT_LLM_API_KEY", self.llm_api_key),
                )
                if not value
            ]
            if missing:
                raise ConfigurationError(
                    "LLM provider 'openai_compatible' requires "
                    + ", ".join(missing)
                    + "; refusing to start."
                )
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentConfig:
        source = os.environ if env is None else env

        provider = source.get("AGENT_LLM_PROVIDER", "offline").strip().lower()
        if provider not in ("offline", "openai_compatible"):
            raise ConfigurationError(
                f"Unknown AGENT_LLM_PROVIDER '{provider}'. "
                "Expected 'offline' or 'openai_compatible'."
            )

        raw_timeout = source.get("AGENT_LLM_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ConfigurationError(
                f"AGENT_LLM_TIMEOUT_SECONDS must be a number, got '{raw_timeout}'."
            ) from exc

        config = cls(
            llm_provider=provider,  # type: ignore[arg-type]
            llm_model=_clean(source.get("AGENT_LLM_MODEL")),
            llm_api_key=_clean(source.get("AGENT_LLM_API_KEY")),
            llm_base_url=_clean(source.get("AGENT_LLM_BASE_URL")),
            llm_timeout_seconds=timeout,
            offline=source.get("AGENT_OFFLINE", "").strip().lower() in _TRUE_VALUES,
        )
        return config.validate_for_use()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
