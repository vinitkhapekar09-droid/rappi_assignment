"""Public facade for the agent workflow.

The backend calls these methods from its endpoints. Each method runs the graph
with the appropriate entry action and returns a typed :class:`WorkflowOutcome`.
Business rules (arithmetic, constraints, approval, execution, validation) are
all enforced behind the injected ports; the agent orchestrates and explains.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, ConfigDict, Field

from app.agent.dependencies import AgentDependencies
from app.agent.graph import build_agent_graph
from app.agent.models import (
    Approval,
    ExecutionResult,
    InferenceInfo,
    RecommendationDraft,
    RecommendationExplanation,
    ValidationResult,
    WorkflowEvent,
)


class WorkflowOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    status: str
    escalated: bool = False
    recommendation: RecommendationDraft | None = None
    inference: InferenceInfo | None = None
    explanation: RecommendationExplanation | None = None
    approval: Approval | None = None
    execution: ExecutionResult | None = None
    validation: ValidationResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    events: list[WorkflowEvent] = Field(default_factory=list)


class AgentWorkflow:
    def __init__(
        self,
        deps: AgentDependencies,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.deps = deps
        self._graph = build_agent_graph(deps, checkpointer=checkpointer)

    # ------------------------------------------------------------ public API
    def recommend(self, case_id: str) -> WorkflowOutcome:
        """Investigate and produce an immutable recommendation draft."""
        return self._run({"action": "recommend", "case_id": case_id})

    def execute(
        self,
        case_id: str,
        version_number: int,
        approval_id: str,
        idempotency_key: str | None = None,
    ) -> WorkflowOutcome:
        """Execute an approved purchase (no validation step)."""
        return self._run(
            {
                "action": "execute",
                "case_id": case_id,
                "version_number": version_number,
                "approval_id": approval_id,
                "idempotency_key": idempotency_key,
                "auto_validate": False,
            }
        )

    def execute_and_validate(
        self,
        case_id: str,
        version_number: int,
        approval_id: str,
        idempotency_key: str | None = None,
    ) -> WorkflowOutcome:
        """Execute an approved purchase and independently validate the result."""
        return self._run(
            {
                "action": "execute_and_validate",
                "case_id": case_id,
                "version_number": version_number,
                "approval_id": approval_id,
                "idempotency_key": idempotency_key,
                "auto_validate": True,
            }
        )

    def validate(self, case_id: str, purchase_order_id: str) -> WorkflowOutcome:
        """Independently validate an existing persisted purchase order."""
        return self._run(
            {
                "action": "validate",
                "case_id": case_id,
                "purchase_order_id": purchase_order_id,
            }
        )

    # --------------------------------------------------------------- internal
    def _run(self, initial: dict[str, Any]) -> WorkflowOutcome:
        final: dict[str, Any] = self._graph.invoke(initial)
        return WorkflowOutcome(
            action=initial["action"],
            status=final.get("status", "unknown"),
            escalated=bool(final.get("escalated", False)),
            recommendation=final.get("recommendation"),
            inference=final.get("inference"),
            explanation=final.get("explanation"),
            approval=final.get("approval"),
            execution=final.get("execution"),
            validation=final.get("validation"),
            error_code=final.get("error_code"),
            error_message=final.get("error_message"),
            warnings=list(final.get("warnings", [])),
            events=list(final.get("events", [])),
        )
