"""Deterministic domain-rule tests against the reference adapter.

These validate the arithmetic and constraint outcomes the agent depends on. The
backend owns the authoritative rules; this keeps the agent testable offline and
documents the expected outcomes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.agent.adapters.reference_domain import ReferenceDomainService
from app.agent.models import ConstraintCheck
from tests.agent.support import (
    default_incoming,
    default_supplier,
    make_case,
    make_evidence,
)

DOMAIN = ReferenceDomainService()


def _check(result, constraint_type: str) -> ConstraintCheck:
    return next(c for c in result.constraint_checks if c.constraint_type == constraint_type)


def test_accepts_the_800_unit_scenario() -> None:
    result = DOMAIN.investigate(make_case(), make_evidence())

    assert result.decision == "accept"
    assert result.baseline_required_quantity == 800
    assert result.recommended_quantity == 800
    assert result.total_cost == Decimal("10000.00")
    assert result.eligible_incoming_quantity == 100
    assert _check(result, "moq").status == "pass"
    assert _check(result, "budget").status == "pass"
    assert _check(result, "storage").status == "pass"
    assert _check(result, "data_quality").status == "pass"
    assert result.missing_information == []


def test_only_confirmed_in_horizon_supply_is_eligible() -> None:
    evidence = make_evidence(
        incoming_orders=[
            default_incoming(quantity=100, expected_arrival_date=date(2026, 10, 18)),
            default_incoming(
                incoming_order_id="io-2",
                external_ref="PO-IN-2",
                quantity=999,
                expected_arrival_date=date(2026, 11, 30),
            ),
            default_incoming(
                incoming_order_id="io-3",
                external_ref="PO-IN-3",
                quantity=999,
                expected_arrival_date=date(2026, 10, 18),
                status="unconfirmed",
            ),
        ]
    )
    result = DOMAIN.investigate(make_case(), evidence)

    assert result.eligible_incoming_quantity == 100
    assert result.baseline_required_quantity == 800


def test_budget_constraint_escalates() -> None:
    result = DOMAIN.investigate(make_case(), make_evidence(budget_amount=Decimal("5000.00")))

    assert result.decision == "investigate"
    assert result.recommended_quantity is None
    assert _check(result, "budget").status == "fail"
    assert result.missing_information


def test_storage_constraint_escalates() -> None:
    result = DOMAIN.investigate(
        make_case(), make_evidence(storage_capacity_units=Decimal("500.0"))
    )

    assert result.decision == "investigate"
    assert result.recommended_quantity is None
    assert _check(result, "storage").status == "fail"


def test_moq_conflict_does_not_silently_round_up() -> None:
    evidence = make_evidence(forecast_demand=400, safety_stock=0, usable_inventory=100)
    # baseline = 400 + 0 - 100 - 100 = 200, below MOQ 500.
    result = DOMAIN.investigate(make_case(), evidence)

    assert result.baseline_required_quantity == 200
    assert result.decision == "investigate"
    assert result.recommended_quantity is None
    assert _check(result, "moq").status == "fail"
    assert _check(result, "moq").details.get("candidate_quantity") == 500


def test_missing_decision_critical_evidence_escalates() -> None:
    result = DOMAIN.investigate(make_case(), make_evidence(forecast_demand=None))

    assert result.decision == "investigate"
    assert result.baseline_required_quantity is None
    assert result.recommended_quantity is None
    assert _check(result, "data_quality").status == "fail"
    assert any("forecast_demand" in item for item in result.missing_information)


def test_no_purchase_needed_is_reject() -> None:
    evidence = make_evidence(forecast_demand=50, safety_stock=0, usable_inventory=200)
    result = DOMAIN.investigate(make_case(), evidence)

    assert result.baseline_required_quantity == 0
    assert result.decision == "reject"
    assert result.recommended_quantity is None


def test_ineligible_supplier_is_not_used() -> None:
    evidence = make_evidence(
        supplier_offers=[default_supplier(is_available=False)]
    )
    result = DOMAIN.investigate(make_case(), evidence)

    assert result.decision == "investigate"
    assert result.chosen_supplier_offer_id is None
    assert _check(result, "supplier_availability").status == "unknown"


def test_inputs_fingerprint_is_deterministic_and_evidence_sensitive() -> None:
    first = DOMAIN.investigate(make_case(), make_evidence())
    second = DOMAIN.investigate(make_case(), make_evidence())
    changed = DOMAIN.investigate(make_case(), make_evidence(forecast_demand=1001))

    assert first.inputs_fingerprint == second.inputs_fingerprint
    assert first.inputs_fingerprint != changed.inputs_fingerprint
    assert first.inputs_fingerprint.startswith("sha256:")
