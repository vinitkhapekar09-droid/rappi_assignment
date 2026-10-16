"""Independent post-execution validation.

Re-reads the persisted purchase order from the mock purchasing API (never the execution
response), compares it with the approved recommendation version and re-checks budget and
storage against the persisted quantity. A mismatch is data, not a transport error:
HTTP 200 is returned whenever the validation step itself ran (`docs/API_CONTRACT.md` §2.5/§5.9).
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.formatting import fmt_money, fmt_volume
from app.errors import invalid_state, mock_unavailable, not_found
from app.mock import MockUnavailable, get_mock_service
from app.models import (
    ExecutionAttempt,
    PurchaseOrder,
    PurchasingCase,
    RecommendationVersion,
    ValidationResult,
)
from app.services import response as R
from app.services.common import utcnow


def _latest_attempt_id(session: Session, po_id: uuid.UUID) -> uuid.UUID | None:
    return session.execute(
        select(ExecutionAttempt.id)
        .where(ExecutionAttempt.purchase_order_id == po_id)
        .order_by(ExecutionAttempt.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def validate_purchase_order(
    session: Session, case_id: uuid.UUID, purchase_order_id: uuid.UUID
) -> tuple[dict, int]:
    po = session.execute(
        select(PurchaseOrder)
        .where(
            PurchaseOrder.id == purchase_order_id,
            PurchaseOrder.case_id == case_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if po is None:
        raise not_found(f"Purchase order {purchase_order_id} does not exist.")

    version = session.get(RecommendationVersion, po.recommendation_version_id)
    case = session.get(PurchasingCase, case_id)

    # Terminal result replay (idempotent for passed/failed).
    terminal = session.execute(
        select(ValidationResult).where(
            ValidationResult.purchase_order_id == po.id,
            ValidationResult.status.in_(("passed", "failed")),
        )
    ).scalar_one_or_none()
    if terminal is not None:
        return R.validation_dict(terminal, version), 200

    if po.status == "failed":
        raise invalid_state(
            "Purchase order is in terminal 'failed' status and cannot be validated.",
            purchase_order_status=po.status,
        )
    if po.status != "created":
        raise invalid_state(
            f"Purchase order status '{po.status}' cannot be validated.",
            purchase_order_status=po.status,
        )

    mock = get_mock_service()
    try:
        snapshot = None
        if po.external_po_id:
            snapshot = mock.get_by_external_id(po.external_po_id)
        if snapshot is None:
            snapshot = mock.get_by_key(po.idempotency_key)
    except MockUnavailable as exc:
        # No trustworthy data to record; persist nothing.
        session.rollback()
        raise mock_unavailable(
            "The persisted purchase order could not be re-read.", reason=str(exc)
        )

    now = utcnow()
    attempt_id = _latest_attempt_id(session, po.id)

    if snapshot is None:
        result = ValidationResult(
            case_id=case_id,
            purchase_order_id=po.id,
            execution_attempt_id=attempt_id,
            recommendation_version_id=version.id,
            status="inconclusive",
            checked_fields={},
            mismatches=[],
            persisted_snapshot={},
        )
        session.add(result)
        session.flush()
        session.commit()
        return R.validation_dict(result, version), 200

    persisted = snapshot.snapshot()
    mismatches: list[dict] = []

    def compare(field: str, approved: object, persisted_value: object) -> None:
        if approved != persisted_value:
            mismatches.append(
                {
                    "field": field,
                    "approved_value": approved,
                    "persisted_value": persisted_value,
                }
            )

    compare("sku", case.sku, persisted.get("sku"))
    compare("quantity", version.recommended_quantity, persisted.get("quantity"))
    compare("supplier_name", version.supplier_name, persisted.get("supplier_name"))
    compare("currency", version.currency, persisted.get("currency"))
    compare("idempotency_key", po.idempotency_key, persisted.get("idempotency_key"))
    compare("external_po_id", po.external_po_id, persisted.get("external_po_id"))
    compare("status", "confirmed", persisted.get("status"))

    approved_price = _dec(version.unit_price)
    persisted_price = _dec(persisted.get("unit_price"))
    if approved_price != persisted_price:
        mismatches.append({
            "field": "unit_price",
            "approved_value": None if approved_price is None else fmt_money(approved_price),
            "persisted_value": None if persisted_price is None else fmt_money(persisted_price),
        })

    persisted_quantity = int(persisted.get("quantity") or 0)
    persisted_total = _dec(persisted.get("total_cost"))
    expected_total = (persisted_price or Decimal(0)) * persisted_quantity
    if (persisted_total is None or persisted_total != expected_total
            or persisted_total != _dec(version.total_cost)):
        mismatches.append(
            {
                "field": "total_cost",
                "approved_value": fmt_money(version.total_cost) if version.total_cost else None,
                "persisted_value": None if persisted_total is None else fmt_money(persisted_total),
            }
        )

    # Re-check budget and storage against the *persisted* quantity.
    evidence = version.evidence_snapshot or {}
    budget = _dec(evidence.get("budget_amount"))
    if budget is not None and persisted_total is not None and persisted_total > budget:
        mismatches.append(
            {
                "field": "budget",
                "approved_value": fmt_money(budget),
                "persisted_value": fmt_money(persisted_total),
            }
        )
    per_unit = _dec(evidence.get("storage_required_per_unit"))
    capacity = _dec(evidence.get("storage_capacity_units"))
    if per_unit is not None and capacity is not None:
        needed = per_unit * persisted_quantity
        if needed > capacity:
            mismatches.append(
                {
                    "field": "storage",
                    "approved_value": fmt_volume(capacity),
                    "persisted_value": fmt_volume(needed),
                }
            )

    checked_fields = {
        "sku": persisted.get("sku"),
        "quantity": persisted.get("quantity"),
        "supplier_name": persisted.get("supplier_name"),
        "unit_price": persisted.get("unit_price"),
        "total_cost": persisted.get("total_cost"),
        "currency": persisted.get("currency"),
    }

    status = "passed" if not mismatches else "failed"
    result = ValidationResult(
        case_id=case_id,
        purchase_order_id=po.id,
        execution_attempt_id=attempt_id,
        recommendation_version_id=version.id,
        status=status,
        checked_fields=checked_fields,
        mismatches=mismatches,
        persisted_snapshot=persisted,
    )
    session.add(result)
    try:
        session.flush()
    except IntegrityError:
        # Concurrent re-validation already recorded a terminal result; replay it.
        session.rollback()
        terminal = session.execute(
            select(ValidationResult).where(
                ValidationResult.purchase_order_id == po.id,
                ValidationResult.status.in_(("passed", "failed")),
            )
        ).scalar_one()
        return R.validation_dict(terminal, version), 200

    if status == "passed":
        po.status = "validated"
        case.status = "validated"
    else:
        po.status = "validation_failed"
        case.status = "validation_failed"
    case.updated_at = now

    session.commit()
    return R.validation_dict(result, version), 200
