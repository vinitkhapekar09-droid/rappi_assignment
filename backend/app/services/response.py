"""Response serializers.

Bodies are built explicitly so money/volume are JSON strings with stable formatting, as
required by `docs/API_CONTRACT.md` §2.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.formatting import fmt_money, fmt_volume
from app.models import (
    Approval,
    CaseEvidence,
    ConstraintCheck,
    ExecutionAttempt,
    IncomingPurchaseOrder,
    PurchaseOrder,
    PurchaseOrderLineItem,
    RecommendationVersion,
    SupplierOffer,
    ValidationResult,
    PurchasingCase,
)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _check_value(value: Decimal | None, unit: str | None, case: PurchasingCase) -> Any:
    if value is None:
        return None
    if unit in ("units", "days"):
        return int(value)
    if unit == case.currency:
        return fmt_money(value)
    return fmt_volume(value)


def incoming_order_dict(order: IncomingPurchaseOrder) -> dict[str, Any]:
    return {
        "incoming_order_id": str(order.id),
        "external_ref": order.external_ref,
        "quantity": order.quantity,
        "expected_arrival_date": iso_date(order.expected_arrival_date),
        "status": order.status,
    }


def supplier_offer_dict(offer: SupplierOffer) -> dict[str, Any]:
    return {
        "supplier_offer_id": str(offer.id),
        "supplier_name": offer.supplier_name,
        "unit_price": fmt_money(offer.unit_price),
        "currency": offer.currency,
        "moq": offer.moq,
        "lead_time_days": offer.lead_time_days,
        "is_available": offer.is_available,
    }


def evidence_dict(
    evidence: CaseEvidence | None,
    incoming: list[IncomingPurchaseOrder],
    offers: list[SupplierOffer],
) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "usable_inventory": evidence.usable_inventory,
        "forecast_demand": evidence.forecast_demand,
        "safety_stock": evidence.safety_stock,
        "budget_amount": None if evidence.budget_amount is None else fmt_money(evidence.budget_amount),
        "storage_capacity_units": None
        if evidence.storage_capacity_units is None
        else fmt_volume(evidence.storage_capacity_units),
        "storage_required_per_unit": None
        if evidence.storage_required_per_unit is None
        else fmt_volume(evidence.storage_required_per_unit),
        "evidence_as_of": iso_utc(evidence.evidence_as_of),
        "updated_at": iso_utc(evidence.updated_at),
        "incoming_orders": [incoming_order_dict(o) for o in incoming],
        "supplier_offers": [supplier_offer_dict(o) for o in offers],
    }


def case_dict(
    case: PurchasingCase,
    *,
    current_version_number: int | None,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "case_id": str(case.id),
        "sku": case.sku,
        "product_name": case.product_name,
        "currency": case.currency,
        "volume_unit": case.volume_unit,
        "planning_horizon_start": iso_date(case.planning_horizon_start),
        "planning_horizon_days": case.planning_horizon_days,
        "planning_horizon_end": iso_date(case.planning_horizon_end),
        "initial_recommended_quantity": case.initial_recommended_quantity,
        "status": case.status,
        "current_recommendation_version": current_version_number,
        "evidence": evidence,
        "created_at": iso_utc(case.created_at),
        "updated_at": iso_utc(case.updated_at),
    }


def constraint_check_dict(check: ConstraintCheck, case: PurchasingCase) -> dict[str, Any]:
    result: dict[str, Any] = {
        "constraint_type": check.constraint_type,
        "status": check.status,
        "message": check.message,
    }
    if check.evaluated_value is not None:
        result["evaluated_value"] = _check_value(check.evaluated_value, check.unit, case)
    if check.limit_value is not None:
        result["limit_value"] = _check_value(check.limit_value, check.unit, case)
    if check.unit is not None:
        result["unit"] = check.unit
    if check.details:
        result["details"] = check.details
    return result


def recommendation_dict(
    version: RecommendationVersion,
    checks: list[ConstraintCheck],
    case: PurchasingCase,
) -> dict[str, Any]:
    return {
        "recommendation_id": str(version.id),
        "case_id": str(version.case_id),
        "version_number": version.version_number,
        "status": version.status,
        "decision": version.decision,
        "initial_recommended_quantity": version.initial_recommended_quantity,
        "baseline_required_quantity": version.baseline_required_quantity,
        "recommended_quantity": version.recommended_quantity,
        "chosen_supplier_offer_id": None
        if version.chosen_supplier_offer_id is None
        else str(version.chosen_supplier_offer_id),
        "supplier_name": version.supplier_name,
        "unit_price": None if version.unit_price is None else fmt_money(version.unit_price),
        "currency": version.currency,
        "total_cost": None if version.total_cost is None else fmt_money(version.total_cost),
        "lead_time_days": version.lead_time_days,
        "moq": version.moq,
        "rationale": version.rationale,
        "evidence": version.evidence_snapshot,
        "assumptions": version.assumptions,
        "missing_information": version.missing_information,
        "inputs_fingerprint": version.inputs_fingerprint,
        "constraint_checks": [constraint_check_dict(c, case) for c in checks],
        "generated_by": version.generated_by,
        "created_at": iso_utc(version.created_at),
    }


def recommendation_summary_dict(version: RecommendationVersion) -> dict[str, Any]:
    return {
        "recommendation_id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "decision": version.decision,
        "recommended_quantity": version.recommended_quantity,
        "total_cost": None if version.total_cost is None else fmt_money(version.total_cost),
        "created_at": iso_utc(version.created_at),
    }


def approval_dict(approval: Approval, version: RecommendationVersion) -> dict[str, Any]:
    return {
        "approval_id": str(approval.id),
        "case_id": str(approval.case_id),
        "recommendation_version_number": version.version_number,
        "decision": approval.decision,
        "approved_quantity": approval.approved_quantity,
        "supplier_offer_id": None
        if approval.supplier_offer_id is None
        else str(approval.supplier_offer_id),
        "supplier_name": approval.supplier_name,
        "unit_price": None if approval.unit_price is None else fmt_money(approval.unit_price),
        "total_cost": None if approval.total_cost is None else fmt_money(approval.total_cost),
        "currency": approval.currency,
        "approver": approval.approver,
        "note": approval.note,
        "created_at": iso_utc(approval.created_at),
    }


def execution_attempt_dict(attempt: ExecutionAttempt) -> dict[str, Any]:
    return {
        "execution_attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "outcome": attempt.outcome,
        "http_status": attempt.http_status,
        "error_code": attempt.error_code,
        "created_at": iso_utc(attempt.created_at),
    }


def purchase_order_dict(
    po: PurchaseOrder,
    version: RecommendationVersion,
    attempts: list[ExecutionAttempt],
    validations: list[ValidationResult],
    *,
    validation_status: str,
) -> dict[str, Any]:
    return {
        "purchase_order_id": str(po.id),
        "case_id": str(po.case_id),
        "approval_id": str(po.approval_id),
        "recommendation_version_number": version.version_number,
        "supplier_name": po.supplier_name,
        "sku": po.sku,
        "quantity": po.quantity,
        "unit_price": fmt_money(po.unit_price),
        "total_cost": fmt_money(po.total_cost),
        "currency": po.currency,
        "status": po.status,
        "external_po_id": po.external_po_id,
        "execution_attempt": execution_attempt_dict(attempts[-1]) if attempts else None,
        "validation_status": validation_status,
        "created_at": iso_utc(po.created_at),
    }


def purchase_order_detail_dict(
    po: PurchaseOrder,
    version: RecommendationVersion,
    line_items: list[PurchaseOrderLineItem],
    attempts: list[ExecutionAttempt],
    validations: list[ValidationResult],
) -> dict[str, Any]:
    return {
        "purchase_order_id": str(po.id),
        "case_id": str(po.case_id),
        "status": po.status,
        "supplier_name": po.supplier_name,
        "sku": po.sku,
        "quantity": po.quantity,
        "unit_price": fmt_money(po.unit_price),
        "total_cost": fmt_money(po.total_cost),
        "currency": po.currency,
        "external_po_id": po.external_po_id,
        "line_items": [
            {
                "sku": li.sku,
                "quantity": li.quantity,
                "unit_price": fmt_money(li.unit_price),
                "line_total": fmt_money(li.line_total),
            }
            for li in line_items
        ],
        "execution_attempts": [execution_attempt_dict(a) for a in attempts],
        "validation_results": [
            {
                "validation_result_id": str(v.id),
                "status": v.status,
                "mismatches": v.mismatches,
                "created_at": iso_utc(v.created_at),
            }
            for v in validations
        ],
        "created_at": iso_utc(po.created_at),
    }


def validation_dict(
    result: ValidationResult, version: RecommendationVersion
) -> dict[str, Any]:
    return {
        "validation_result_id": str(result.id),
        "purchase_order_id": str(result.purchase_order_id),
        "case_id": str(result.case_id),
        "recommendation_version_number": version.version_number,
        "status": result.status,
        "checked_fields": result.checked_fields,
        "mismatches": result.mismatches,
        "persisted_snapshot": result.persisted_snapshot,
        "created_at": iso_utc(result.created_at),
    }


def validation_status_for(po: PurchaseOrder, validations: list[ValidationResult]) -> str:
    if any(v.status == "passed" for v in validations):
        return "passed"
    if any(v.status == "failed" for v in validations):
        return "failed"
    if validations:
        return validations[-1].status
    if po.status == "validated":
        return "passed"
    if po.status == "validation_failed":
        return "failed"
    return "pending"
