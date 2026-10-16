"""Typed errors raised by the agent module.

The names map onto the stable error codes in ``docs/API_CONTRACT.md`` §2.1 so the
backend can translate them into HTTP responses without inspecting internals.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for agent-module failures."""

    code: str = "agent_error"


class ConfigurationError(AgentError):
    """The agent is misconfigured (for example a real provider without a key).

    Raised eagerly and visibly instead of silently degrading, so a missing
    credential cannot be mistaken for an offline run.
    """

    code = "configuration_error"


class EvidenceNotFoundError(AgentError):
    """The case or its evidence could not be loaded."""

    code = "not_found"


class ApprovalGuardError(AgentError):
    """Execution was requested without a valid approval for the exact version.

    Maps to ``409 stale_approval`` / ``409 recommendation_superseded``. The LLM
    can never satisfy this guard; only a persisted buyer approval can.
    """

    code = "stale_approval"


class MissingEvidenceError(AgentError):
    """Decision-critical evidence is absent or contradictory."""

    code = "missing_evidence"


class ExecutionFailedError(AgentError):
    """The mock purchasing service confirmed a failure before creating a PO.

    The agent does not retry automatically.
    """

    code = "execution_failed"


class ExecutionUncertainError(AgentError):
    """The execution outcome could not be confirmed; reconcile before retrying."""

    code = "execution_uncertain"


class ValidationFailedError(AgentError):
    """Independent validation detected a persisted-result mismatch."""

    code = "validation_failed"


class DependencyUnavailableError(AgentError):
    """A required downstream port (mock service, persistence) was unreachable."""

    code = "dependency_unavailable"
