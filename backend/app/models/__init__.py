"""SQLAlchemy models for the AI Purchasing Agent.

These mirror `docs/DATABASE_SCHEMA.md` exactly: composite foreign keys, partial unique
indexes, the stored generated `planning_horizon_end`, NUMERIC money/volume, JSONB
structured evidence, and row-lock based concurrency control.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---------------------------------------------------------------------------
# Shared column helpers
# ---------------------------------------------------------------------------

MONEY = Numeric(14, 4)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


CASE_STATUSES = (
    "draft",
    "investigating",
    "awaiting_approval",
    "approved",
    "rejected",
    "needs_investigation",
    "executing",
    "validating",
    "validated",
    "execution_failed",
    "validation_failed",
)

DECISIONS = ("accept", "modify", "reject", "investigate")
CONSTRAINT_TYPES = (
    "supplier_availability",
    "lead_time",
    "moq",
    "budget",
    "storage",
    "data_quality",
)
CHECK_STATUSES = ("pass", "fail", "unknown", "not_applicable")
VERSION_STATUSES = ("ready", "superseded", "invalidated")
APPROVAL_DECISIONS = ("approved", "rejected")
PO_STATUSES = ("created", "failed", "validated", "validation_failed")
ATTEMPT_OUTCOMES = ("succeeded", "failed", "uncertain")
VALIDATION_STATUSES = ("passed", "failed", "inconclusive")
IDEMPOTENCY_STATES = ("in_progress", "completed", "failed", "uncertain")
INCOMING_STATUSES = ("confirmed", "unconfirmed", "cancelled")


def _text_in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


# ---------------------------------------------------------------------------
# purchasing_cases
# ---------------------------------------------------------------------------


class PurchasingCase(Base):
    __tablename__ = "purchasing_cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    volume_unit: Mapped[str] = mapped_column(Text, nullable=False)
    planning_horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    planning_horizon_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("7")
    )
    planning_horizon_end: Mapped[date] = mapped_column(
        Date,
        Computed("planning_horizon_start + (planning_horizon_days - 1)", persisted=True),
        nullable=False,
    )
    initial_recommended_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("800")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    current_recommendation_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("planning_horizon_days = 7", name="planning_horizon_days_is_7"),
        CheckConstraint("initial_recommended_quantity >= 0", name="initial_recommended_non_negative"),
        CheckConstraint(_text_in("status", CASE_STATUSES), name="valid_status"),
        ForeignKeyConstraint(
            ["current_recommendation_version_id", "id"],
            ["recommendation_versions.id", "recommendation_versions.case_id"],
            name="fk_cases_current_reco",
            use_alter=True,
        ),
        Index("idx_cases_status", "status"),
        Index("idx_cases_sku", "sku"),
    )


# ---------------------------------------------------------------------------
# case_evidence
# ---------------------------------------------------------------------------


class CaseEvidence(Base):
    __tablename__ = "case_evidence"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchasing_cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    usable_inventory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forecast_demand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    storage_capacity_units: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    storage_required_per_unit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    evidence_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("usable_inventory IS NULL OR usable_inventory >= 0", name="usable_non_negative"),
        CheckConstraint("forecast_demand IS NULL OR forecast_demand >= 0", name="forecast_non_negative"),
        CheckConstraint("safety_stock IS NULL OR safety_stock >= 0", name="safety_non_negative"),
        CheckConstraint("budget_amount IS NULL OR budget_amount >= 0", name="budget_non_negative"),
        CheckConstraint(
            "storage_capacity_units IS NULL OR storage_capacity_units >= 0",
            name="storage_capacity_non_negative",
        ),
        CheckConstraint(
            "storage_required_per_unit IS NULL OR storage_required_per_unit >= 0",
            name="storage_per_unit_non_negative",
        ),
    )


# ---------------------------------------------------------------------------
# incoming_purchase_orders
# ---------------------------------------------------------------------------


class IncomingPurchaseOrder(Base):
    __tablename__ = "incoming_purchase_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint(_text_in("status", INCOMING_STATUSES), name="valid_status"),
        UniqueConstraint("case_id", "external_ref", name="uq_incoming_case_ref"),
        Index("idx_incoming_case", "case_id"),
        Index("idx_incoming_eligible", "case_id", "status", "expected_arrival_date"),
    )


# ---------------------------------------------------------------------------
# supplier_offers
# ---------------------------------------------------------------------------


class SupplierOffer(Base):
    __tablename__ = "supplier_offers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    supplier_name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    moq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("moq >= 0", name="moq_non_negative"),
        CheckConstraint("lead_time_days >= 0", name="lead_time_non_negative"),
        Index("uq_supplier_case_name", "case_id", "supplier_name", unique=True,
              postgresql_where=text("is_current = true")),
        UniqueConstraint("id", "case_id", name="uq_supplier_id_case"),
        Index("idx_supplier_case", "case_id"),
    )


# ---------------------------------------------------------------------------
# recommendation_versions
# ---------------------------------------------------------------------------


class RecommendationVersion(Base):
    __tablename__ = "recommendation_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ready'"))
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    initial_recommended_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_required_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommended_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chosen_supplier_offer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    assumptions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    missing_information: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    inputs_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(_text_in("status", VERSION_STATUSES), name="valid_status"),
        CheckConstraint(_text_in("decision", DECISIONS), name="valid_decision"),
        CheckConstraint("initial_recommended_quantity >= 0", name="initial_non_negative"),
        CheckConstraint(
            "baseline_required_quantity IS NULL OR baseline_required_quantity >= 0",
            name="baseline_non_negative",
        ),
        CheckConstraint(
            "recommended_quantity IS NULL OR recommended_quantity >= 0", name="reco_non_negative"
        ),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("total_cost IS NULL OR total_cost >= 0", name="total_cost_non_negative"),
        CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 0", name="lead_time_non_negative"),
        CheckConstraint("moq IS NULL OR moq >= 0", name="moq_non_negative"),
        CheckConstraint(_text_in("generated_by", ("agent", "offline")), name="valid_generated_by"),
        CheckConstraint(
            "total_cost IS NULL OR unit_price IS NULL OR recommended_quantity IS NULL "
            "OR total_cost = unit_price * recommended_quantity",
            name="total_cost_deterministic",
        ),
        CheckConstraint(
            "decision NOT IN ('accept','modify') OR (recommended_quantity IS NOT NULL "
            "AND unit_price IS NOT NULL AND total_cost IS NOT NULL AND supplier_name IS NOT NULL)",
            name="executable_decisions_complete",
        ),
        CheckConstraint(
            "decision <> 'reject' OR recommended_quantity IS NULL",
            name="reject_has_no_quantity",
        ),
        CheckConstraint(
            "decision <> 'investigate' OR jsonb_array_length(missing_information) > 0",
            name="investigate_explains_itself",
        ),
        ForeignKeyConstraint(
            ["chosen_supplier_offer_id", "case_id"],
            ["supplier_offers.id", "supplier_offers.case_id"],
            name="fk_reco_supplier",
        ),
        UniqueConstraint("case_id", "version_number", name="uq_reco_case_version_number"),
        UniqueConstraint("id", "case_id", name="uq_reco_id_case"),
        Index("idx_reco_case_version", "case_id", text("version_number DESC")),
        Index("idx_reco_case_status", "case_id", "status"),
    )


# ---------------------------------------------------------------------------
# constraint_checks
# ---------------------------------------------------------------------------


class ConstraintCheck(Base):
    __tablename__ = "constraint_checks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    recommendation_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recommendation_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    constraint_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    limit_value: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_text_in("constraint_type", CONSTRAINT_TYPES), name="valid_type"),
        CheckConstraint(_text_in("status", CHECK_STATUSES), name="valid_status"),
        UniqueConstraint("recommendation_version_id", "constraint_type", name="uq_check_reco_type"),
        Index("idx_checks_reco", "recommendation_version_id"),
    )


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    approved_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier_offer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    approver: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_text_in("decision", APPROVAL_DECISIONS), name="valid_decision"),
        CheckConstraint(
            "approved_quantity IS NULL OR approved_quantity >= 0", name="approved_qty_non_negative"
        ),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("total_cost IS NULL OR total_cost >= 0", name="total_cost_non_negative"),
        CheckConstraint(
            "decision <> 'approved' OR (approved_quantity IS NOT NULL AND total_cost IS NOT NULL "
            "AND supplier_name IS NOT NULL)",
            name="approved_requires_terms",
        ),
        ForeignKeyConstraint(
            ["recommendation_version_id", "case_id"],
            ["recommendation_versions.id", "recommendation_versions.case_id"],
            name="fk_approval_reco",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supplier_offer_id", "case_id"],
            ["supplier_offers.id", "supplier_offers.case_id"],
            name="fk_approval_supplier",
        ),
        UniqueConstraint("id", "case_id", name="uq_approval_id_case"),
        UniqueConstraint("id", "recommendation_version_id", name="uq_approval_id_reco"),
        Index("idx_approvals_case", "case_id"),
        Index(
            "uq_active_approval_per_version",
            "recommendation_version_id",
            unique=True,
            postgresql_where=text("decision = 'approved' AND invalidated_at IS NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# purchase_orders
# ---------------------------------------------------------------------------


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approval_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    supplier_offer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    supplier_name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'created'"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    external_po_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    persisted_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("total_cost >= 0", name="total_cost_non_negative"),
        CheckConstraint("total_cost = quantity * unit_price", name="total_cost_deterministic"),
        CheckConstraint(_text_in("status", PO_STATUSES), name="valid_status"),
        ForeignKeyConstraint(
            ["recommendation_version_id", "case_id"],
            ["recommendation_versions.id", "recommendation_versions.case_id"],
            name="fk_po_reco",
        ),
        ForeignKeyConstraint(
            ["approval_id", "recommendation_version_id"],
            ["approvals.id", "approvals.recommendation_version_id"],
            name="fk_po_approval",
        ),
        ForeignKeyConstraint(
            ["supplier_offer_id", "case_id"],
            ["supplier_offers.id", "supplier_offers.case_id"],
            name="fk_po_supplier",
        ),
        UniqueConstraint("idempotency_key", name="uq_po_idempotency_key"),
        UniqueConstraint("external_po_id", name="uq_po_external_id"),
        UniqueConstraint("id", "case_id", name="uq_po_id_case"),
        Index("idx_po_case", "case_id"),
        Index("idx_po_status", "status"),
    )


# ---------------------------------------------------------------------------
# purchase_order_line_items
# ---------------------------------------------------------------------------


class PurchaseOrderLineItem(Base):
    __tablename__ = "purchase_order_line_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
        CheckConstraint("line_total = quantity * unit_price", name="line_total_deterministic"),
        UniqueConstraint("purchase_order_id", "sku", name="uq_line_po_sku"),
        Index("idx_po_lines_po", "purchase_order_id"),
    )


# ---------------------------------------------------------------------------
# execution_attempts
# ---------------------------------------------------------------------------


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_po_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_text_in("outcome", ATTEMPT_OUTCOMES), name="valid_outcome"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        ForeignKeyConstraint(
            ["purchase_order_id", "case_id"],
            ["purchase_orders.id", "purchase_orders.case_id"],
            name="fk_attempt_po",
        ),
        ForeignKeyConstraint(
            ["approval_id", "case_id"],
            ["approvals.id", "approvals.case_id"],
            name="fk_attempt_approval",
        ),
        UniqueConstraint("idempotency_key", "attempt_number", name="uq_attempt_key_number"),
        UniqueConstraint("id", "purchase_order_id", name="uq_attempt_id_po"),
        Index("idx_attempts_po", "purchase_order_id"),
        Index("idx_attempts_case", "case_id"),
    )


# ---------------------------------------------------------------------------
# validation_results
# ---------------------------------------------------------------------------


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    recommendation_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    checked_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    mismatches: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    persisted_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_text_in("status", VALIDATION_STATUSES), name="valid_status"),
        CheckConstraint(
            "status <> 'failed' OR jsonb_array_length(mismatches) > 0",
            name="failed_requires_mismatches",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id", "case_id"],
            ["purchase_orders.id", "purchase_orders.case_id"],
            name="fk_validation_po",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["execution_attempt_id", "purchase_order_id"],
            ["execution_attempts.id", "execution_attempts.purchase_order_id"],
            name="fk_validation_attempt",
        ),
        ForeignKeyConstraint(
            ["recommendation_version_id", "case_id"],
            ["recommendation_versions.id", "recommendation_versions.case_id"],
            name="fk_validation_reco",
        ),
        Index("idx_validation_po", "purchase_order_id"),
        Index(
            "uq_terminal_validation",
            "purchase_order_id",
            unique=True,
            postgresql_where=text("status IN ('passed','failed')"),
        ),
    )


# ---------------------------------------------------------------------------
# idempotency_keys
# ---------------------------------------------------------------------------


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchasing_cases.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("action = 'execute_purchase'", name="action_execute_purchase"),
        CheckConstraint(_text_in("state", IDEMPOTENCY_STATES), name="valid_state"),
        ForeignKeyConstraint(
            ["purchase_order_id", "case_id"],
            ["purchase_orders.id", "purchase_orders.case_id"],
            name="fk_idem_po",
        ),
        Index("idx_idem_case", "case_id"),
    )
