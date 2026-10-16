"""Deterministic purchasing domain logic.

This module is authoritative for arithmetic and constraint evaluation. It performs no I/O,
imports no web framework, and is fully testable offline. Any LLM output is treated as
untrusted and may only replace `rationale`; it can never change quantities, constraint
results, or the decision.

Rules implemented here follow `docs/PROJECT_PLAN.md` §5, `docs/API_CONTRACT.md` §3.2/§5.4/§6, and
`docs/DATABASE_SCHEMA.md` §1.2/§2.5.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.domain.formatting import fmt_volume, to_decimal


@dataclass(frozen=True)
class CaseContext:
    sku: str
    currency: str
    volume_unit: str
    planning_horizon_start: date
    planning_horizon_days: int
    initial_recommended_quantity: int


@dataclass(frozen=True)
class IncomingOrderInput:
    external_ref: str
    quantity: int
    expected_arrival_date: date
    status: str


@dataclass(frozen=True)
class SupplierOfferInput:
    id: uuid.UUID | None
    supplier_name: str
    unit_price: Decimal
    currency: str
    moq: int
    lead_time_days: int
    is_available: bool


@dataclass(frozen=True)
class EvidenceInput:
    usable_inventory: int | None
    forecast_demand: int | None
    safety_stock: int | None
    budget_amount: Decimal | None
    storage_capacity_units: Decimal | None
    storage_required_per_unit: Decimal | None
    incoming_orders: tuple[IncomingOrderInput, ...] = ()
    supplier_offers: tuple[SupplierOfferInput, ...] = ()


@dataclass(frozen=True)
class ConstraintCheckResult:
    constraint_type: str
    status: str
    message: str
    evaluated_value: Decimal | None = None
    limit_value: Decimal | None = None
    unit: str | None = None
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RecommendationDraft:
    decision: str
    baseline_required_quantity: int | None
    recommended_quantity: int | None
    chosen_supplier_offer_id: uuid.UUID | None
    supplier_name: str | None
    unit_price: Decimal | None
    currency: str | None
    total_cost: Decimal | None
    lead_time_days: int | None
    moq: int | None
    rationale: str
    evidence_snapshot: dict
    assumptions: list[str]
    missing_information: list[str]
    inputs_fingerprint: str
    constraint_checks: list[ConstraintCheckResult]
    generated_by: str


# ---------------------------------------------------------------------------
# Horizon helpers (docs/API_CONTRACT.md §2.6, docs/DATABASE_SCHEMA.md §1.2)
# ---------------------------------------------------------------------------


def horizon_end(start: date, days: int) -> date:
    """Inclusive last day of the horizon: start + (days - 1) calendars days."""
    return start + timedelta(days=days - 1)


def is_incoming_eligible(order: IncomingOrderInput, start: date, end: date) -> bool:
    """Confirmed supply arriving within the inclusive horizon."""
    return order.status == "confirmed" and start <= order.expected_arrival_date <= end


def is_supplier_eligible(offer: SupplierOfferInput, horizon_days: int) -> bool:
    """Available supplier whose lead time fits inside the horizon (<= days - 1)."""
    return offer.is_available and offer.lead_time_days <= horizon_days - 1


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def compute_eligible_incoming(
    evidence: EvidenceInput, start: date, end: date
) -> int:
    return sum(o.quantity for o in evidence.incoming_orders if is_incoming_eligible(o, start, end))


def compute_baseline(
    forecast_demand: int,
    safety_stock: int,
    usable_inventory: int,
    eligible_incoming_quantity: int,
) -> int:
    return max(0, forecast_demand + safety_stock - usable_inventory - eligible_incoming_quantity)


def select_supplier(
    offers: tuple[SupplierOfferInput, ...], horizon_days: int
) -> SupplierOfferInput | None:
    """Choose the eligible supplier deterministically.

    Ordering: lowest unit price, then lowest lead time, then supplier name. This is a
    deterministic tie-break so recommendations are reproducible.
    """
    eligible = [o for o in offers if is_supplier_eligible(o, horizon_days)]
    if not eligible:
        return None
    return min(eligible, key=lambda o: (o.unit_price, o.lead_time_days, o.supplier_name))


def fingerprint_evidence(case: CaseContext, evidence: EvidenceInput) -> str:
    """Deterministic sha256 of the evidence used to build a version."""
    payload = {
        "sku": case.sku,
        "currency": case.currency,
        "volume_unit": case.volume_unit,
        "planning_horizon_start": case.planning_horizon_start.isoformat(),
        "planning_horizon_days": case.planning_horizon_days,
        "usable_inventory": evidence.usable_inventory,
        "forecast_demand": evidence.forecast_demand,
        "safety_stock": evidence.safety_stock,
        "budget_amount": None if evidence.budget_amount is None else fmt_volume(evidence.budget_amount),
        "storage_capacity_units": None
        if evidence.storage_capacity_units is None
        else fmt_volume(evidence.storage_capacity_units),
        "storage_required_per_unit": None
        if evidence.storage_required_per_unit is None
        else fmt_volume(evidence.storage_required_per_unit),
        "incoming_orders": sorted(
            (
                {
                    "external_ref": o.external_ref,
                    "quantity": o.quantity,
                    "expected_arrival_date": o.expected_arrival_date.isoformat(),
                    "status": o.status,
                }
                for o in evidence.incoming_orders
            ),
            key=lambda d: d["external_ref"],
        ),
        "supplier_offers": sorted(
            (
                {
                    "supplier_name": o.supplier_name,
                    "unit_price": fmt_volume(o.unit_price),
                    "currency": o.currency,
                    "moq": o.moq,
                    "lead_time_days": o.lead_time_days,
                    "is_available": o.is_available,
                }
                for o in evidence.supplier_offers
            ),
            key=lambda d: d["supplier_name"],
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_recommendation(
    case: CaseContext,
    evidence: EvidenceInput,
    *,
    rationale_override: str | None = None,
    generated_by: str = "offline",
) -> RecommendationDraft:
    """Build an immutable recommendation draft from evidence.

    Decision precedence:
      1. Missing/contradictory decision-critical evidence -> `investigate`, qty null.
      2. Baseline requirement of zero -> `reject`, qty null.
      3. MOQ above a positive baseline -> `investigate` (never silently round up).
      4. Otherwise -> `accept` the deterministic baseline. Budget/storage failures are
         recorded as `fail` and block execution, but do not change the accepted quantity.
    """
    start = case.planning_horizon_start
    days = case.planning_horizon_days
    end = horizon_end(start, days)

    missing: list[str] = []

    # -- Contradictory evidence --
    for offer in evidence.supplier_offers:
        if offer.currency != case.currency:
            missing.append(
                f"Supplier '{offer.supplier_name}' currency {offer.currency} does not match "
                f"case currency {case.currency}."
            )
    for order in evidence.incoming_orders:
        if order.quantity < 0:
            missing.append(f"Incoming order '{order.external_ref}' has a negative quantity.")

    # -- Missing decision-critical evidence (docs/API_CONTRACT.md §6) --
    if evidence.forecast_demand is None:
        missing.append("Missing forecast_demand for the seven-day horizon.")
    if evidence.safety_stock is None:
        missing.append("Missing safety_stock.")
    if evidence.usable_inventory is None:
        missing.append("Missing usable_inventory.")
    if evidence.budget_amount is None:
        missing.append("Missing budget_amount for the budget check.")
    if evidence.storage_capacity_units is None or evidence.storage_required_per_unit is None:
        missing.append("Missing storage capacity/consumption for the storage check.")

    eligible_offers = [o for o in evidence.supplier_offers if is_supplier_eligible(o, days)]
    if not eligible_offers:
        missing.append(
            "No eligible supplier offer (available and lead_time_days <= "
            f"{days - 1})."
        )

    # -- Baseline (computable only when the demand/inventory fields are present) --
    baseline: int | None = None
    eligible_incoming_quantity: int | None = None
    if (
        evidence.forecast_demand is not None
        and evidence.safety_stock is not None
        and evidence.usable_inventory is not None
    ):
        eligible_incoming_quantity = compute_eligible_incoming(evidence, start, end)
        baseline = compute_baseline(
            evidence.forecast_demand,
            evidence.safety_stock,
            evidence.usable_inventory,
            eligible_incoming_quantity,
        )

    chosen = select_supplier(evidence.supplier_offers, days)

    # -- Decide --
    decision: str
    recommended_quantity: int | None
    missing_information: list[str] = []
    check_quantity: int | None

    if missing:
        decision = "investigate"
        recommended_quantity = None
        missing_information = list(missing)
        check_quantity = None
    elif baseline == 0:
        decision = "reject"
        recommended_quantity = None
        check_quantity = 0
    elif chosen is not None and chosen.moq > baseline:  # baseline is int here
        decision = "investigate"
        recommended_quantity = baseline
        overage = chosen.moq - baseline
        missing_information = [
            f"Supplier MOQ ({chosen.moq}) exceeds the deterministic requirement ({baseline}); "
            f"ordering the MOQ would add {overage} units not justified by seven-day demand. "
            "Buyer decision required (do not silently round up)."
        ]
        check_quantity = chosen.moq
    else:
        decision = "accept"
        recommended_quantity = baseline
        check_quantity = baseline

    # -- Constraint checks --
    checks = _build_constraint_checks(
        case=case,
        evidence=evidence,
        decision=decision,
        baseline=baseline,
        chosen=chosen,
        check_quantity=check_quantity,
        missing=missing,
    )

    # -- Executable terms (only for a proposed purchase) --
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    total_cost: Decimal | None = None
    lead_time_days: int | None = None
    moq: int | None = None
    if decision in ("accept", "investigate") and chosen is not None and recommended_quantity is not None and recommended_quantity > 0:
        supplier_id = chosen.id
        supplier_name = chosen.supplier_name
        unit_price = to_decimal(chosen.unit_price)
        currency = chosen.currency
        total_cost = unit_price * recommended_quantity
        lead_time_days = chosen.lead_time_days
        moq = chosen.moq

    evidence_snapshot = {
        "usable_inventory": evidence.usable_inventory,
        "forecast_demand": evidence.forecast_demand,
        "safety_stock": evidence.safety_stock,
        "eligible_incoming_quantity": eligible_incoming_quantity,
        "budget_amount": None if evidence.budget_amount is None else fmt_volume(evidence.budget_amount),
        "storage_capacity_units": None
        if evidence.storage_capacity_units is None
        else fmt_volume(evidence.storage_capacity_units),
        "storage_required_per_unit": None
        if evidence.storage_required_per_unit is None
        else fmt_volume(evidence.storage_required_per_unit),
        "chosen_supplier_offer_id": str(supplier_id) if supplier_id else None,
    }

    assumptions = _build_assumptions(case, evidence, baseline, eligible_incoming_quantity, chosen)

    rationale = rationale_override or _build_rationale(
        case, evidence, decision, baseline, recommended_quantity, chosen, checks
    )

    return RecommendationDraft(
        decision=decision,
        baseline_required_quantity=baseline,
        recommended_quantity=recommended_quantity,
        chosen_supplier_offer_id=supplier_id,
        supplier_name=supplier_name,
        unit_price=unit_price,
        currency=currency,
        total_cost=total_cost,
        lead_time_days=lead_time_days,
        moq=moq,
        rationale=rationale,
        evidence_snapshot=evidence_snapshot,
        assumptions=assumptions,
        missing_information=missing_information,
        inputs_fingerprint=fingerprint_evidence(case, evidence),
        constraint_checks=checks,
        generated_by=generated_by,
    )


# ---------------------------------------------------------------------------
# Constraint checks
# ---------------------------------------------------------------------------


def _build_constraint_checks(
    *,
    case: CaseContext,
    evidence: EvidenceInput,
    decision: str,
    baseline: int | None,
    chosen: SupplierOfferInput | None,
    check_quantity: int | None,
    missing: list[str],
) -> list[ConstraintCheckResult]:
    checks: list[ConstraintCheckResult] = []

    # supplier_availability
    if chosen is None:
        checks.append(
            ConstraintCheckResult(
                constraint_type="supplier_availability",
                status="fail" if not missing else "unknown",
                message=(
                    "No eligible supplier offer (available and lead_time_days within horizon)."
                ),
            )
        )
    else:
        checks.append(
            ConstraintCheckResult(
                constraint_type="supplier_availability",
                status="pass",
                message=f"Supplier '{chosen.supplier_name}' is available.",
                details={"supplier_name": chosen.supplier_name},
            )
        )

    # lead_time
    if chosen is None:
        checks.append(
            ConstraintCheckResult(
                constraint_type="lead_time",
                status="unknown",
                message="Lead time cannot be evaluated without an eligible supplier.",
            )
        )
    else:
        limit = case.planning_horizon_days - 1
        checks.append(
            ConstraintCheckResult(
                constraint_type="lead_time",
                status="pass" if chosen.lead_time_days <= limit else "fail",
                evaluated_value=Decimal(chosen.lead_time_days),
                limit_value=Decimal(limit),
                unit="days",
                message=(
                    f"Lead time {chosen.lead_time_days} days is within the "
                    f"{case.planning_horizon_days}-day horizon."
                    if chosen.lead_time_days <= limit
                    else f"Lead time {chosen.lead_time_days} days exceeds the horizon."
                ),
            )
        )

    # moq
    if chosen is None or baseline is None:
        checks.append(
            ConstraintCheckResult(
                constraint_type="moq",
                status="unknown",
                message="MOQ cannot be checked against an unknown requirement.",
            )
        )
    else:
        moq_ok = chosen.moq <= baseline
        checks.append(
            ConstraintCheckResult(
                constraint_type="moq",
                status="pass" if moq_ok else "fail",
                evaluated_value=Decimal(chosen.moq),
                limit_value=Decimal(baseline),
                unit="units",
                message=(
                    f"MOQ {chosen.moq} is satisfied by the requirement {baseline}."
                    if moq_ok
                    else f"MOQ {chosen.moq} exceeds the requirement {baseline}."
                ),
                details={"quantity_evaluated": check_quantity},
            )
        )

    # budget
    if evidence.budget_amount is None or check_quantity is None or chosen is None:
        checks.append(
            ConstraintCheckResult(
                constraint_type="budget",
                status="unknown",
                message="Budget cannot be evaluated without budget_amount and a quantity.",
            )
        )
    else:
        cost = to_decimal(chosen.unit_price) * check_quantity
        within = cost <= to_decimal(evidence.budget_amount)
        checks.append(
            ConstraintCheckResult(
                constraint_type="budget",
                status="pass" if within else "fail",
                evaluated_value=cost,
                limit_value=to_decimal(evidence.budget_amount),
                unit=case.currency,
                message=(
                    f"Total cost {cost} is within budget {evidence.budget_amount}."
                    if within
                    else f"Total cost {cost} exceeds budget {evidence.budget_amount}."
                ),
                details={"quantity_evaluated": check_quantity},
            )
        )

    # storage
    if (
        evidence.storage_capacity_units is None
        or evidence.storage_required_per_unit is None
        or check_quantity is None
    ):
        checks.append(
            ConstraintCheckResult(
                constraint_type="storage",
                status="unknown",
                message="Storage cannot be evaluated without capacity and per-unit consumption.",
            )
        )
    else:
        needed = to_decimal(evidence.storage_required_per_unit) * check_quantity
        within = needed <= to_decimal(evidence.storage_capacity_units)
        checks.append(
            ConstraintCheckResult(
                constraint_type="storage",
                status="pass" if within else "fail",
                evaluated_value=needed,
                limit_value=to_decimal(evidence.storage_capacity_units),
                unit=case.volume_unit,
                message=(
                    f"Required storage {needed} is within capacity "
                    f"{evidence.storage_capacity_units}."
                    if within
                    else f"Required storage {needed} exceeds capacity "
                    f"{evidence.storage_capacity_units}."
                ),
                details={"quantity_evaluated": check_quantity},
            )
        )

    # data_quality
    checks.append(
        ConstraintCheckResult(
            constraint_type="data_quality",
            status="fail" if missing else "pass",
            message=(
                "Decision-critical evidence is missing or contradictory."
                if missing
                else "All decision-critical evidence present."
            ),
            details={"issues": missing},
        )
    )

    # `decision` is accepted here so callers can assert consistency if desired.
    _ = decision
    return checks


def _build_assumptions(
    case: CaseContext,
    evidence: EvidenceInput,
    baseline: int | None,
    eligible_incoming_quantity: int | None,
    chosen: SupplierOfferInput | None,
) -> list[str]:
    end = horizon_end(case.planning_horizon_start, case.planning_horizon_days)
    assumptions = [
        f"Planning horizon is {case.planning_horizon_start.isoformat()} through "
        f"{end.isoformat()} inclusive ({case.planning_horizon_days} days).",
        "Quantities are whole units of one SKU; money is in "
        f"{case.currency}; storage is in {case.volume_unit}.",
    ]
    eligible_refs = [
        o.external_ref
        for o in evidence.incoming_orders
        if is_incoming_eligible(
            o, case.planning_horizon_start, end
        )
    ]
    if eligible_refs:
        assumptions.append(
            "Only confirmed incoming orders arriving within the horizon count ("
            + ", ".join(sorted(eligible_refs))
            + f"; {eligible_incoming_quantity} units)."
        )
    if chosen is not None:
        assumptions.append(
            f"Selected supplier '{chosen.supplier_name}' at unit price "
            f"{chosen.unit_price} ({chosen.currency}), MOQ {chosen.moq}, lead time "
            f"{chosen.lead_time_days} days."
        )
    if baseline is not None:
        assumptions.append(
            "Baseline requirement = max(0, forecast_demand + safety_stock - "
            "usable_inventory - eligible_incoming_quantity)."
        )
    return assumptions


def _build_rationale(
    case: CaseContext,
    evidence: EvidenceInput,
    decision: str,
    baseline: int | None,
    recommended_quantity: int | None,
    chosen: SupplierOfferInput | None,
    checks: list[ConstraintCheckResult],
) -> str:
    if decision == "investigate" and baseline is None:
        return (
            "Cannot compute a reliable recommendation because decision-critical evidence is "
            "missing or contradictory. The case requires investigation before a purchase can "
            "be proposed."
        )

    by_type = {c.constraint_type: c for c in checks}

    if decision == "reject":
        return (
            "No purchase is required. The deterministic requirement is 0 units after "
            "counting usable inventory and eligible incoming supply within the seven-day "
            "horizon."
        )

    if decision == "investigate" and recommended_quantity is not None and chosen is not None:
        return (
            f"The deterministic requirement is {baseline} units, but supplier "
            f"'{chosen.supplier_name}' has a minimum order quantity of {chosen.moq}. "
            f"Ordering the MOQ would add {chosen.moq - (baseline or 0)} units that the "
            "seven-day demand does not justify. This trade-off is escalated for a buyer "
            "decision rather than silently rounding up."
        )

    # accept (or investigate-with-value fallback)
    parts = [f"The deterministic requirement is {baseline} units"]
    if chosen is not None:
        parts.append(
            f"supplier '{chosen.supplier_name}' lead time ({chosen.lead_time_days} days) is "
            f"within the {case.planning_horizon_days}-day horizon"
        )
        moq_check = by_type.get("moq")
        if moq_check is not None and moq_check.status == "pass":
            parts.append(f"MOQ {chosen.moq} is below the requirement")
        cost = to_decimal(chosen.unit_price) * (recommended_quantity or 0)
        cost_str = f"{to_decimal(cost):f}".rstrip("0").rstrip(".")
        budget = by_type.get("budget")
        budget_str = (
            "unknown" if evidence.budget_amount is None else fmt_volume(evidence.budget_amount)
        )
        if budget is not None and budget.status == "pass":
            parts.append(f"total cost {cost_str} {case.currency} is within budget {budget_str}")
        elif budget is not None and budget.status == "fail":
            parts.append(
                f"total cost {cost_str} {case.currency} EXCEEDS budget {budget_str} "
                "(not executable as-is)"
            )
        else:
            parts.append(f"total cost {cost_str} {case.currency}")
        storage = by_type.get("storage")
        if storage is not None and storage.status in ("pass", "fail"):
            needed = to_decimal(evidence.storage_required_per_unit or 0) * (
                recommended_quantity or 0
            )
            verb = "is within" if storage.status == "pass" else "EXCEEDS"
            parts.append(
                f"required storage {fmt_volume(needed)} {verb} capacity "
                f"{fmt_volume(evidence.storage_capacity_units or 0)}"
            )
    sentence = "; ".join(parts) + "."
    failed = [c.constraint_type for c in checks if c.status == "fail"]
    if failed:
        sentence += f" Failing constraints block execution: {', '.join(sorted(failed))}."
    return sentence
