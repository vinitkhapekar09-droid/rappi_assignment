"""Deterministic REFERENCE implementation of :class:`DomainService`.

NOT authoritative business code. The backend/domain worker owns the real
implementation (``app/domain/``). This adapter exists only so the agent workflow
and its offline tests can run in isolation before that module exists. It follows
the frozen rules in ``docs/PROJECT_PLAN.md`` §5, ``docs/API_CONTRACT.md`` §5.4/§3.2, and
``docs/DATABASE_SCHEMA.md`` §2.5. Inject the real service in production.

Determinism: identical evidence always yields an identical result, including the
``inputs_fingerprint`` (sha256 over a canonical JSON form).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.agent.models import (
    CaseContext,
    CaseEvidence,
    ConstraintCheck,
    EvidenceView,
    InvestigationResult,
    SupplierOffer,
)


class ReferenceDomainService:
    """Reference deterministic domain service (see module docstring)."""

    def investigate(self, case: CaseContext, evidence: CaseEvidence) -> InvestigationResult:
        horizon_end = case.planning_horizon_end
        eligible_orders = [
            order
            for order in evidence.incoming_orders
            if order.status == "confirmed"
            and case.planning_horizon_start <= order.expected_arrival_date <= horizon_end
            and order.quantity >= 0
        ]
        eligible_incoming = sum(order.quantity for order in eligible_orders)

        eligible_offers = [
            offer
            for offer in evidence.supplier_offers
            if offer.is_available
            and offer.lead_time_days <= case.planning_horizon_days - 1
            and offer.currency == case.currency
        ]
        chosen = _choose_supplier(eligible_offers)

        core_missing = [
            name
            for name, value in (
                ("forecast_demand", evidence.forecast_demand),
                ("safety_stock", evidence.safety_stock),
                ("usable_inventory", evidence.usable_inventory),
            )
            if value is None
        ]
        contradictory = [
            f"incoming_order {order.external_ref} has a negative quantity"
            for order in evidence.incoming_orders
            if order.quantity < 0
        ]

        baseline: int | None = None
        if not core_missing:
            baseline = max(
                0,
                (evidence.forecast_demand or 0)
                + (evidence.safety_stock or 0)
                - (evidence.usable_inventory or 0)
                - eligible_incoming,
            )

        candidate_quantity = _candidate_quantity(baseline, chosen)

        purchase_missing: list[str] = []
        if baseline is not None and baseline > 0:
            if chosen is None:
                purchase_missing.append("eligible_supplier_offer")
            if evidence.budget_amount is None:
                purchase_missing.append("budget_amount")
            if evidence.storage_capacity_units is None:
                purchase_missing.append("storage_capacity_units")
            if evidence.storage_required_per_unit is None:
                purchase_missing.append("storage_required_per_unit")

        checks = self._constraint_checks(
            case, evidence, chosen, baseline, candidate_quantity
        )

        decision, recommended, missing_information, chosen_out = _decide(
            baseline=baseline,
            core_missing=core_missing,
            contradictory=contradictory,
            purchase_missing=purchase_missing,
            chosen=chosen,
            candidate_quantity=candidate_quantity,
            evidence=evidence,
        )

        return InvestigationResult(
            case_id=case.case_id,
            decision=decision,
            baseline_required_quantity=baseline,
            recommended_quantity=recommended,
            chosen_supplier_offer_id=chosen_out.supplier_offer_id if chosen_out else None,
            supplier_name=chosen_out.supplier_name if chosen_out else None,
            unit_price=chosen_out.unit_price if chosen_out else None,
            currency=chosen_out.currency if chosen_out else None,
            total_cost=(
                chosen_out.unit_price * recommended
                if chosen_out is not None and recommended is not None
                else None
            ),
            lead_time_days=chosen_out.lead_time_days if chosen_out else None,
            moq=chosen_out.moq if chosen_out else None,
            eligible_incoming_quantity=eligible_incoming,
            evidence=EvidenceView(
                usable_inventory=evidence.usable_inventory,
                forecast_demand=evidence.forecast_demand,
                safety_stock=evidence.safety_stock,
                eligible_incoming_quantity=eligible_incoming,
                budget_amount=evidence.budget_amount,
                storage_capacity_units=evidence.storage_capacity_units,
                storage_required_per_unit=evidence.storage_required_per_unit,
            ),
            assumptions=_assumptions(case, eligible_orders),
            missing_information=missing_information,
            constraint_checks=checks,
            inputs_fingerprint=_fingerprint(case, evidence),
        )

    # -------------------------------------------------------------- internals
    def _constraint_checks(
        self,
        case: CaseContext,
        evidence: CaseEvidence,
        chosen: SupplierOffer | None,
        baseline: int | None,
        candidate_quantity: int | None,
    ) -> list[ConstraintCheck]:
        checks: list[ConstraintCheck] = []

        checks.append(
            ConstraintCheck(
                constraint_type="supplier_availability",
                status="pass" if chosen is not None else "unknown",
                message=(
                    "At least one eligible supplier offer is available."
                    if chosen is not None
                    else "No eligible supplier offer is available within the planning horizon."
                ),
            )
        )

        checks.append(
            ConstraintCheck(
                constraint_type="lead_time",
                status="pass" if chosen is not None else "unknown",
                evaluated_value=Decimal(chosen.lead_time_days) if chosen else None,
                limit_value=Decimal(case.planning_horizon_days - 1),
                unit="days",
                message=(
                    "Supplier lead time is within the planning horizon."
                    if chosen is not None
                    else "Lead time cannot be evaluated without an eligible supplier."
                ),
            )
        )

        if chosen is not None and baseline is not None:
            moq_status = "pass" if baseline >= chosen.moq else "fail"
            checks.append(
                ConstraintCheck(
                    constraint_type="moq",
                    status=moq_status,
                    evaluated_value=Decimal(baseline),
                    limit_value=Decimal(chosen.moq),
                    unit="units",
                    message=(
                        "The baseline requirement meets the supplier MOQ."
                        if moq_status == "pass"
                        else "The baseline requirement is below the supplier MOQ."
                    ),
                    details=(
                        {"candidate_quantity": candidate_quantity}
                        if moq_status == "fail"
                        else {}
                    ),
                )
            )
        else:
            checks.append(
                ConstraintCheck(
                    constraint_type="moq",
                    status="unknown",
                    message="MOQ cannot be evaluated without a baseline requirement and supplier.",
                )
            )

        if (
            chosen is not None
            and candidate_quantity is not None
            and evidence.budget_amount is not None
        ):
            cost = chosen.unit_price * candidate_quantity
            checks.append(
                ConstraintCheck(
                    constraint_type="budget",
                    status="pass" if cost <= evidence.budget_amount else "fail",
                    evaluated_value=cost,
                    limit_value=evidence.budget_amount,
                    unit=case.currency,
                    message=(
                        "The proposed cost is within budget."
                        if cost <= evidence.budget_amount
                        else "The proposed cost exceeds the available budget."
                    ),
                )
            )
        else:
            checks.append(
                ConstraintCheck(
                    constraint_type="budget",
                    status="unknown",
                    message="Budget cannot be evaluated without a supplier and a budget amount.",
                )
            )

        if (
            candidate_quantity is not None
            and evidence.storage_required_per_unit is not None
            and evidence.storage_capacity_units is not None
        ):
            required = Decimal(candidate_quantity) * evidence.storage_required_per_unit
            checks.append(
                ConstraintCheck(
                    constraint_type="storage",
                    status="pass" if required <= evidence.storage_capacity_units else "fail",
                    evaluated_value=required,
                    limit_value=evidence.storage_capacity_units,
                    unit=case.volume_unit,
                    message=(
                        "The proposed quantity fits within storage capacity."
                        if required <= evidence.storage_capacity_units
                        else "The proposed quantity exceeds storage capacity."
                    ),
                )
            )
        else:
            checks.append(
                ConstraintCheck(
                    constraint_type="storage",
                    status="unknown",
                    message=(
                        "Storage cannot be evaluated without capacity and per-unit "
                        "consumption."
                    ),
                )
            )

        data_complete = not any(c.status == "unknown" for c in checks) and not any(
            c.status == "fail" for c in checks
        )
        checks.append(
            ConstraintCheck(
                constraint_type="data_quality",
                status="pass" if data_complete else "fail",
                message=(
                    "All decision-critical evidence is present and consistent."
                    if data_complete
                    else "Some decision-critical evidence is missing, unknown, or unresolved."
                ),
            )
        )
        return checks


def _choose_supplier(offers: list[SupplierOffer]) -> SupplierOffer | None:
    if not offers:
        return None
    return min(offers, key=lambda o: (o.unit_price, o.supplier_name, o.supplier_offer_id))


def _candidate_quantity(
    baseline: int | None, chosen: SupplierOffer | None
) -> int | None:
    if baseline is None or baseline <= 0 or chosen is None:
        return None
    return max(baseline, chosen.moq)


def _decide(
    *,
    baseline: int | None,
    core_missing: list[str],
    contradictory: list[str],
    purchase_missing: list[str],
    chosen: SupplierOffer | None,
    candidate_quantity: int | None,
    evidence: CaseEvidence,
) -> tuple[str, int | None, list[str], SupplierOffer | None]:
    missing_information: list[str] = []

    if core_missing or contradictory:
        missing_information.extend(
            f"Missing decision-critical evidence: {n}." for n in core_missing
        )
        missing_information.extend(contradictory)
        return "investigate", None, missing_information, None

    assert baseline is not None
    if baseline == 0:
        return "reject", None, [], None

    if purchase_missing:
        missing_information.extend(
            f"Missing decision-critical evidence: {name}." for name in purchase_missing
        )
        return "investigate", None, missing_information, chosen

    assert chosen is not None and candidate_quantity is not None

    if baseline < chosen.moq:
        missing_information.append(
            "Unresolved MOQ overage: the baseline requirement "
            f"{baseline} is below the supplier MOQ {chosen.moq}; buyer investigation is "
            "required before increasing the order."
        )
        return "investigate", None, missing_information, chosen

    cost = chosen.unit_price * baseline
    if evidence.budget_amount is not None and cost > evidence.budget_amount:
        missing_information.append(
            f"Unresolved budget constraint: cost {cost} exceeds budget "
            f"{evidence.budget_amount}."
        )
        return "investigate", None, missing_information, chosen

    if (
        evidence.storage_required_per_unit is not None
        and evidence.storage_capacity_units is not None
    ):
        required = Decimal(baseline) * evidence.storage_required_per_unit
        if required > evidence.storage_capacity_units:
            missing_information.append(
                f"Unresolved storage constraint: required {required} exceeds capacity "
                f"{evidence.storage_capacity_units}."
            )
            return "investigate", None, missing_information, chosen

    return "accept", baseline, [], chosen


def _assumptions(case: CaseContext, eligible_orders: list) -> list[str]:
    assumptions = [
        (
            f"Planning horizon is {case.planning_horizon_start.isoformat()} through "
            f"{case.planning_horizon_end.isoformat()} inclusive "
            f"({case.planning_horizon_days} days)."
        ),
        "Only confirmed incoming orders arriving within the planning horizon are eligible supply.",
    ]
    if eligible_orders:
        refs = ", ".join(f"{o.external_ref} ({o.quantity})" for o in eligible_orders)
        assumptions.append(f"Eligible incoming supply: {refs}.")
    return assumptions


def _fingerprint(case: CaseContext, evidence: CaseEvidence) -> str:
    payload = {
        "sku": case.sku,
        "currency": case.currency,
        "volume_unit": case.volume_unit,
        "planning_horizon_start": case.planning_horizon_start.isoformat(),
        "planning_horizon_days": case.planning_horizon_days,
        "evidence": evidence.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
