"""Agent-local typed models.

These are the agent module's internal view of the purchasing domain. Field names
deliberately mirror the frozen vocabulary in ``docs/API_CONTRACT.md`` and
``docs/DATABASE_SCHEMA.md`` so adapters can map one-to-one, but these are **not** the
REST contract objects. The backend owns the REST schemas and the SQLAlchemy
models; conversion happens at the port boundary described in
``app/agent/INTERFACES.md``.

Money and storage volumes use :class:`decimal.Decimal` (never float), matching
``NUMERIC`` semantics. Quantities are whole units (:class:`int`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["accept", "modify", "reject", "investigate"]
ConstraintType = Literal[
    "supplier_availability",
    "lead_time",
    "moq",
    "budget",
    "storage",
    "data_quality",
]
ConstraintStatus = Literal["pass", "fail", "unknown", "not_applicable"]
ApprovalDecision = Literal["approved", "rejected"]
PurchaseOrderStatus = Literal["created", "failed", "validated", "validation_failed"]
ExecutionOutcome = Literal["succeeded", "failed", "uncertain"]
ValidationStatus = Literal["passed", "failed", "inconclusive"]
GeneratedBy = Literal["agent", "offline"]


class SupplierOffer(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier_offer_id: str
    supplier_name: str
    unit_price: Decimal
    currency: str
    moq: int = 0
    lead_time_days: int
    is_available: bool = True


class IncomingOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    incoming_order_id: str
    external_ref: str
    quantity: int
    expected_arrival_date: date
    status: Literal["confirmed", "unconfirmed", "cancelled"]


class CaseContext(BaseModel):
    """Case identity, fixed units/currency, and the seven-day planning window."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    sku: str
    product_name: str | None = None
    currency: str
    volume_unit: str
    planning_horizon_start: date
    planning_horizon_days: int = 7
    initial_recommended_quantity: int = 800

    @property
    def planning_horizon_end(self) -> date:
        return self.planning_horizon_start + timedelta(days=self.planning_horizon_days - 1)


class CaseEvidence(BaseModel):
    """Scalar evidence plus collections. ``None`` means genuinely missing."""

    model_config = ConfigDict(frozen=True)

    usable_inventory: int | None = None
    forecast_demand: int | None = None
    safety_stock: int | None = None
    budget_amount: Decimal | None = None
    storage_capacity_units: Decimal | None = None
    storage_required_per_unit: Decimal | None = None
    evidence_as_of: datetime | None = None
    incoming_orders: list[IncomingOrder] = Field(default_factory=list)
    supplier_offers: list[SupplierOffer] = Field(default_factory=list)


class EvidenceView(BaseModel):
    """The evidence snapshot echoed inside a recommendation (API §5.4)."""

    model_config = ConfigDict(frozen=True)

    usable_inventory: int | None = None
    forecast_demand: int | None = None
    safety_stock: int | None = None
    eligible_incoming_quantity: int | None = 0
    budget_amount: Decimal | None = None
    storage_capacity_units: Decimal | None = None
    storage_required_per_unit: Decimal | None = None


class ConstraintCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    constraint_type: ConstraintType
    status: ConstraintStatus
    evaluated_value: Decimal | None = None
    limit_value: Decimal | None = None
    unit: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class InvestigationResult(BaseModel):
    """Authoritative, deterministic output of the domain service.

    Produced by :class:`app.agent.ports.DomainService`; the agent never invents
    or edits these numbers.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    decision: Decision
    baseline_required_quantity: int | None = None
    recommended_quantity: int | None = None
    chosen_supplier_offer_id: str | None = None
    supplier_name: str | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    total_cost: Decimal | None = None
    lead_time_days: int | None = None
    moq: int | None = None
    eligible_incoming_quantity: int | None = 0
    evidence: EvidenceView = Field(default_factory=EvidenceView)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    constraint_checks: list[ConstraintCheck] = Field(default_factory=list)
    inputs_fingerprint: str = ""


class ExplanationContext(BaseModel):
    """Everything the LLM is allowed to see when writing interpretive prose.

    It excludes the ability to change the decision: the deterministic result is
    provided as read-only context for explanation only.
    """

    model_config = ConfigDict(frozen=True)

    case: CaseContext
    investigation: InvestigationResult
    evidence: CaseEvidence | None = None


class RecommendationExplanation(BaseModel):
    """Structured, interpretive LLM output.

    It contains no quantity, price, supplier, or decision field: the model
    physically cannot set an authoritative value through this schema. Every
    string is still filtered by the numeric guard in
    :mod:`app.agent.explanation` before use.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(default="", max_length=5000)
    key_evidence: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] | None = None


class InferenceInfo(BaseModel):
    """Persisted provenance, never inferred from the configured provider alone."""

    mode: Literal["online", "offline_fallback", "provider_fallback"]
    online_succeeded: bool
    provider: str
    model: str | None = None
    requested_provider: str
    requested_model: str | None = None
    fallback_reason: str | None = None


class RecommendationDraft(BaseModel):
    """Agent-produced recommendation, ready for the backend to persist (API §5.4)."""

    recommendation_id: str
    case_id: str
    version_number: int
    status: Literal["ready"] = "ready"
    decision: Decision
    initial_recommended_quantity: int
    baseline_required_quantity: int | None = None
    recommended_quantity: int | None = None
    chosen_supplier_offer_id: str | None = None
    supplier_name: str | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    total_cost: Decimal | None = None
    lead_time_days: int | None = None
    moq: int | None = None
    rationale: str
    evidence: EvidenceView
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    inputs_fingerprint: str
    constraint_checks: list[ConstraintCheck] = Field(default_factory=list)
    generated_by: GeneratedBy
    created_at: datetime


class Approval(BaseModel):
    """A buyer approval tied to one exact recommendation version."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    case_id: str
    recommendation_version_number: int
    decision: ApprovalDecision
    approved_quantity: int | None = None
    supplier_offer_id: str | None = None
    supplier_name: str | None = None
    unit_price: Decimal | None = None
    total_cost: Decimal | None = None
    currency: str | None = None
    approver: str
    note: str | None = None
    invalidated_at: datetime | None = None
    created_at: datetime


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    recommendation_version_number: int
    approval_id: str
    sku: str
    supplier_offer_id: str | None = None
    supplier_name: str
    quantity: int
    unit_price: Decimal
    total_cost: Decimal
    currency: str
    idempotency_key: str


class ExecutionAttemptInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_attempt_id: str
    attempt_number: int
    outcome: ExecutionOutcome
    http_status: int | None = None
    error_code: str | None = None
    created_at: datetime


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    purchase_order_id: str | None = None
    case_id: str
    approval_id: str
    recommendation_version_number: int
    sku: str
    supplier_name: str
    quantity: int
    unit_price: Decimal
    total_cost: Decimal
    currency: str
    status: PurchaseOrderStatus | None = None
    external_po_id: str | None = None
    outcome: ExecutionOutcome
    execution_attempt: ExecutionAttemptInfo | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class ValidationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    purchase_order_id: str


class ValidationMismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    approved_value: Any = None
    persisted_value: Any = None


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_result_id: str
    purchase_order_id: str
    case_id: str
    recommendation_version_number: int
    status: ValidationStatus
    checked_fields: dict[str, Any] = Field(default_factory=dict)
    mismatches: list[ValidationMismatch] = Field(default_factory=list)
    persisted_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkflowEvent(BaseModel):
    """A meaningful workflow transition or failure reason for observability."""

    model_config = ConfigDict(frozen=True)

    event: str
    message: str
    at: datetime
    data: dict[str, Any] = Field(default_factory=dict)
