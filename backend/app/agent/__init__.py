"""AI Purchasing Agent orchestration module.

Owns the LangGraph workflow, the configurable LLM adapter, structured model
output validation, the evidence-investigation/tool orchestration, recommendation
explanation generation, and escalation behaviour.

The module depends only on the port interfaces in :mod:`app.agent.ports`. It
never authorizes a purchase and never overrides a deterministic constraint: the
LLM only produces prose, which is validated against the deterministic result
before it is used.
"""

from app.agent.config import AgentConfig
from app.agent.errors import (
    AgentError,
    ApprovalGuardError,
    ConfigurationError,
    EvidenceNotFoundError,
    ExecutionFailedError,
    ValidationFailedError,
)
from app.agent.models import RecommendationDraft, RecommendationExplanation
from app.agent.service import AgentDependencies, AgentWorkflow, WorkflowOutcome

__all__ = [
    "AgentConfig",
    "AgentDependencies",
    "AgentError",
    "AgentWorkflow",
    "ApprovalGuardError",
    "ConfigurationError",
    "EvidenceNotFoundError",
    "ExecutionFailedError",
    "RecommendationDraft",
    "RecommendationExplanation",
    "ValidationFailedError",
    "WorkflowOutcome",
]
