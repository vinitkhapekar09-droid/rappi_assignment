"""In-memory doubles for the agent ports.

One shared :class:`InMemoryPurchasingBackend` implements evidence, approval,
execution, and validation so tests can exercise the whole workflow offline. It
also models a deliberately misbehaving mock purchasing service (forced failure,
uncertain outcome, or a persisted result that differs from the approved request)
so the validation-failure path is reproducible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from app.agent.errors import EvidenceNotFoundError
from app.agent.models import (
    Approval,
    CaseContext,
    CaseEvidence,
    ExecutionAttemptInfo,
    ExecutionRequest,
    ExecutionResult,
    ValidationMismatch,
    ValidationRequest,
    ValidationResult,
    WorkflowEvent,
)

ExecutionMode = Literal["succeed", "fail", "uncertain"]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class StoredPurchaseOrder:
    purchase_order_id: str
    external_po_id: str
    case_id: str
    approval_id: str
    recommendation_version_number: int
    sku: str
    quantity: int
    supplier_name: str
    unit_price: Decimal
    total_cost: Decimal
    currency: str
    status: str = "created"
    created_at: datetime = field(default_factory=_now)


class InMemoryPurchasingBackend:
    def __init__(
        self,
        *,
        cases: dict[str, CaseContext],
        evidence: dict[str, CaseEvidence],
        approvals: dict[str, Approval],
        execution_mode: ExecutionMode = "succeed",
        mismatch: dict[str, Any] | None = None,
    ) -> None:
        self._cases = dict(cases)
        self._evidence = dict(evidence)
        self._approvals = dict(approvals)
        self._execution_mode = execution_mode
        self._mismatch = dict(mismatch or {})

        self.purchase_orders: dict[str, StoredPurchaseOrder] = {}
        self._results_by_key: dict[str, ExecutionResult] = {}
        self._terminal_validation: dict[str, ValidationResult] = {}
        self.create_calls = 0
        self.validation_calls = 0

    # ------------------------------------------------------- EvidenceProvider
    def get_case(self, case_id: str) -> CaseContext:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise EvidenceNotFoundError(f"Unknown case {case_id}") from exc

    def get_evidence(self, case_id: str) -> CaseEvidence:
        try:
            return self._evidence[case_id]
        except KeyError as exc:
            raise EvidenceNotFoundError(f"No evidence for case {case_id}") from exc

    # -------------------------------------------------------- ApprovalService
    def get_approval(self, approval_id: str) -> Approval | None:
        return self._approvals.get(approval_id)

    def get_active_approval(
        self, case_id: str, version_number: int
    ) -> Approval | None:
        for approval in self._approvals.values():
            if (
                approval.case_id == case_id
                and approval.recommendation_version_number == version_number
                and approval.decision == "approved"
                and approval.invalidated_at is None
            ):
                return approval
        return None

    def add_approval(self, approval: Approval) -> None:
        self._approvals[approval.approval_id] = approval

    # ------------------------------------------------------- ExecutionService
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        existing = self._results_by_key.get(request.idempotency_key)
        if existing is not None:
            return existing

        self.create_calls += 1
        if self._execution_mode == "fail":
            result = ExecutionResult(
                purchase_order_id=None,
                case_id=request.case_id,
                approval_id=request.approval_id,
                recommendation_version_number=request.recommendation_version_number,
                sku=request.sku,
                supplier_name=request.supplier_name,
                quantity=request.quantity,
                unit_price=request.unit_price,
                total_cost=request.total_cost,
                currency=request.currency,
                outcome="failed",
                status=None,
                execution_attempt=ExecutionAttemptInfo(
                    execution_attempt_id=str(uuid.uuid4()),
                    attempt_number=1,
                    outcome="failed",
                    http_status=502,
                    error_code="execution_failed",
                    created_at=_now(),
                ),
                error_code="execution_failed",
                error_message=(
                    "The mock purchasing service failed before creating a purchase order."
                ),
                created_at=_now(),
            )
        elif self._execution_mode == "uncertain":
            result = ExecutionResult(
                purchase_order_id=None,
                case_id=request.case_id,
                approval_id=request.approval_id,
                recommendation_version_number=request.recommendation_version_number,
                sku=request.sku,
                supplier_name=request.supplier_name,
                quantity=request.quantity,
                unit_price=request.unit_price,
                total_cost=request.total_cost,
                currency=request.currency,
                outcome="uncertain",
                status=None,
                execution_attempt=ExecutionAttemptInfo(
                    execution_attempt_id=str(uuid.uuid4()),
                    attempt_number=1,
                    outcome="uncertain",
                    http_status=None,
                    error_code="execution_uncertain",
                    created_at=_now(),
                ),
                error_code="execution_uncertain",
                error_message="The mock purchasing service outcome could not be confirmed.",
                created_at=_now(),
            )
        else:
            result = self._create_purchase_order(request)

        self._results_by_key[request.idempotency_key] = result
        return result

    def reconcile(self, idempotency_key: str) -> ExecutionResult | None:
        return self._results_by_key.get(idempotency_key)

    def _create_purchase_order(self, request: ExecutionRequest) -> ExecutionResult:
        quantity = int(self._mismatch.get("quantity", request.quantity))
        unit_price = Decimal(str(self._mismatch.get("unit_price", request.unit_price)))
        total_cost = Decimal(
            str(self._mismatch.get("total_cost", unit_price * quantity))
        )
        po = StoredPurchaseOrder(
            purchase_order_id=str(uuid.uuid4()),
            external_po_id="MOCK-PO-" + uuid.uuid4().hex[:8].upper(),
            case_id=request.case_id,
            approval_id=request.approval_id,
            recommendation_version_number=request.recommendation_version_number,
            sku=str(self._mismatch.get("sku", request.sku)),
            quantity=quantity,
            supplier_name=str(
                self._mismatch.get("supplier_name", request.supplier_name)
            ),
            unit_price=unit_price,
            total_cost=total_cost,
            currency=str(self._mismatch.get("currency", request.currency)),
        )
        self.purchase_orders[po.purchase_order_id] = po
        return ExecutionResult(
            purchase_order_id=po.purchase_order_id,
            case_id=po.case_id,
            approval_id=po.approval_id,
            recommendation_version_number=po.recommendation_version_number,
            sku=po.sku,
            supplier_name=po.supplier_name,
            quantity=po.quantity,
            unit_price=po.unit_price,
            total_cost=po.total_cost,
            currency=po.currency,
            status="created",
            external_po_id=po.external_po_id,
            outcome="succeeded",
            execution_attempt=ExecutionAttemptInfo(
                execution_attempt_id=str(uuid.uuid4()),
                attempt_number=1,
                outcome="succeeded",
                http_status=201,
                created_at=_now(),
            ),
            created_at=_now(),
        )

    # ------------------------------------------------------ ValidationService
    def validate(self, request: ValidationRequest) -> ValidationResult:
        terminal = self._terminal_validation.get(request.purchase_order_id)
        if terminal is not None:
            return terminal

        self.validation_calls += 1
        po = self.purchase_orders.get(request.purchase_order_id)
        if po is None:
            return ValidationResult(
                validation_result_id=str(uuid.uuid4()),
                purchase_order_id=request.purchase_order_id,
                case_id=request.case_id,
                recommendation_version_number=0,
                status="inconclusive",
                created_at=_now(),
            )

        approval = self._approvals[po.approval_id]
        case = self._cases[po.case_id]

        approved_fields: dict[str, Any] = {
            "sku": case.sku,
            "quantity": approval.approved_quantity,
            "supplier_name": approval.supplier_name,
            "unit_price": approval.unit_price,
            "total_cost": approval.total_cost,
            "currency": approval.currency,
        }
        persisted_fields: dict[str, Any] = {
            "external_po_id": po.external_po_id,
            "sku": po.sku,
            "quantity": po.quantity,
            "supplier_name": po.supplier_name,
            "unit_price": po.unit_price,
            "total_cost": po.total_cost,
            "currency": po.currency,
        }

        mismatches: list[ValidationMismatch] = []
        for field_name, approved_value in approved_fields.items():
            persisted_value = persisted_fields[field_name]
            if not _values_equal(approved_value, persisted_value):
                mismatches.append(
                    ValidationMismatch(
                        field=field_name,
                        approved_value=_jsonable(approved_value),
                        persisted_value=_jsonable(persisted_value),
                    )
                )

        status = "passed" if not mismatches else "failed"
        po.status = "validated" if status == "passed" else "validation_failed"
        result = ValidationResult(
            validation_result_id=str(uuid.uuid4()),
            purchase_order_id=po.purchase_order_id,
            case_id=po.case_id,
            recommendation_version_number=po.recommendation_version_number,
            status=status,  # type: ignore[arg-type]
            checked_fields={
                k: _jsonable(v)
                for k, v in persisted_fields.items()
                if k != "external_po_id"
            },
            mismatches=mismatches,
            persisted_snapshot={
                k: _jsonable(v) for k, v in persisted_fields.items()
            },
            created_at=_now(),
        )
        self._terminal_validation[po.purchase_order_id] = result
        return result


class ListEventRecorder:
    """Collects workflow events in memory for assertions."""

    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def record(self, event: WorkflowEvent) -> None:
        self.events.append(event)


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, Decimal)) and isinstance(right, (int, Decimal)):
        return Decimal(str(left)) == Decimal(str(right))
    return str(left) == str(right)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value
