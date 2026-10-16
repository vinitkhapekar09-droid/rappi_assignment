"""Adapter boundary between the agent module and the deterministic backend.

The backend worker owns the real implementations (FastAPI routes, domain rules,
persistence, approval enforcement, idempotency, mock purchasing API). The agent
depends only on the protocols below, so it can be built and tested in isolation
and wired to the backend without inventing routes, tables, or schemas.

Required backend interfaces are documented in ``app/agent/INTERFACES.md``. Each
protocol is small on purpose: the closer the boundary, the easier the
integration. Adapters in :mod:`app.agent.adapters` provide deterministic
in-memory implementations used by the offline test suite.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agent.models import (
    Approval,
    CaseContext,
    CaseEvidence,
    ExecutionRequest,
    ExecutionResult,
    ExplanationContext,
    InvestigationResult,
    RecommendationExplanation,
    ValidationRequest,
    ValidationResult,
    WorkflowEvent,
)


@runtime_checkable
class EvidenceProvider(Protocol):
    """Loads a case and its evidence. Read-only."""

    def get_case(self, case_id: str) -> CaseContext:
        """Return the case, or raise ``EvidenceNotFoundError``."""

    def get_evidence(self, case_id: str) -> CaseEvidence:
        """Return the current evidence, or raise ``EvidenceNotFoundError``."""


@runtime_checkable
class DomainService(Protocol):
    """Deterministic calculation and constraint enforcement (authoritative).

    The agent treats the returned :class:`InvestigationResult` as the source of
    truth for quantities, prices, supplier choice, constraints, and the
    accept/modify/reject/investigate decision. The LLM cannot change it.
    """

    def investigate(self, case: CaseContext, evidence: CaseEvidence) -> InvestigationResult:
        ...


@runtime_checkable
class ApprovalService(Protocol):
    """Reads persisted buyer approvals. The agent never creates them."""

    def get_approval(self, approval_id: str) -> Approval | None:
        ...

    def get_active_approval(
        self, case_id: str, version_number: int
    ) -> Approval | None:
        ...


@runtime_checkable
class ExecutionService(Protocol):
    """Executes an approved purchase through the backend/mock service.

    Must be idempotent on ``ExecutionRequest.idempotency_key``: a repeated
    request returns the existing result and never creates a second PO.
    """

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...

    def reconcile(self, idempotency_key: str) -> ExecutionResult | None:
        """Read an existing operation by key before any retry (never a blind retry)."""


@runtime_checkable
class ValidationService(Protocol):
    """Independent post-execution validation of the persisted purchase order."""

    def validate(self, request: ValidationRequest) -> ValidationResult:
        ...


@runtime_checkable
class EventRecorder(Protocol):
    """Persists workflow transitions and failure reasons (no secrets)."""

    def record(self, event: WorkflowEvent) -> None:
        ...


@runtime_checkable
class LLMClient(Protocol):
    """Configurable model adapter. Output is untrusted and validated downstream."""

    name: str
    is_offline: bool

    def generate_explanation(
        self, context: ExplanationContext
    ) -> RecommendationExplanation | str:
        """Return interpretive prose. Never authoritative for numbers."""
