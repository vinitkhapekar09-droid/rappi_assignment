"""LangGraph workflow state.

The state is a ``TypedDict`` so it doubles as the graph's channel schema. The
``events`` channel uses an additive reducer because several nodes contribute
observability events in one run.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from app.agent.models import (
    Approval,
    CaseContext,
    CaseEvidence,
    ExecutionResult,
    InvestigationResult,
    InferenceInfo,
    RecommendationDraft,
    RecommendationExplanation,
    ValidationResult,
    WorkflowEvent,
)

AgentAction = Literal["recommend", "execute", "execute_and_validate", "validate"]


def append_list(left: list | None, right: list | None) -> list:
    """Additive reducer that tolerates an unset channel on first write."""
    return [*(left or []), *(right or [])]


class AgentState(TypedDict, total=False):
    action: AgentAction
    case_id: str
    version_number: int | None
    approval_id: str | None
    purchase_order_id: str | None
    idempotency_key: str | None
    auto_validate: bool

    case: CaseContext
    evidence: CaseEvidence
    investigation: InvestigationResult
    explanation: RecommendationExplanation
    inference: InferenceInfo
    recommendation: RecommendationDraft

    approval: Approval | None
    execution: ExecutionResult | None
    validation: ValidationResult | None

    status: str
    escalated: bool
    error_code: str | None
    error_message: str | None
    warnings: Annotated[list[str], append_list]
    events: Annotated[list[WorkflowEvent], append_list]
