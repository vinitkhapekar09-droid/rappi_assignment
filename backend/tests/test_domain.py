"""Offline deterministic domain tests (no database, no network, no LLM)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.domain import (
    CaseContext,
    EvidenceInput,
    IncomingOrderInput,
    SupplierOfferInput,
    build_recommendation,
    compute_baseline,
    fingerprint_evidence,
    horizon_end,
    is_incoming_eligible,
    is_supplier_eligible,
    select_supplier,
)

START = date(2026, 10, 16)
CASE = CaseContext(
    sku="SKU-1234",
    currency="MXN",
    volume_unit="liter",
    planning_horizon_start=START,
    planning_horizon_days=7,
    initial_recommended_quantity=800,
)


def offer(name="Distribuidora Norte", price="12.50", moq=500, lead=3, available=True):
    return SupplierOfferInput(None, name, D(price), "MXN", moq, lead, available)


def evidence(**overrides) -> EvidenceInput:
    base = dict(
        usable_inventory=200,
        forecast_demand=1000,
        safety_stock=100,
        budget_amount=D("15000"),
        storage_capacity_units=D("1000"),
        storage_required_per_unit=D("1"),
        incoming_orders=(IncomingOrderInput("PO-IN-1", 100, date(2026, 10, 18), "confirmed"),),
        supplier_offers=(offer(),),
    )
    base.update(overrides)
    return EvidenceInput(**base)


# --- horizon -----------------------------------------------------------------


def test_horizon_end_is_inclusive_seven_days():
    assert horizon_end(START, 7) == date(2026, 10, 22)


@pytest.mark.parametrize(
    "arrival,status,expected",
    [
        (date(2026, 10, 16), "confirmed", True),
        (date(2026, 10, 22), "confirmed", True),
        (date(2026, 10, 23), "confirmed", False),
        (date(2026, 10, 15), "confirmed", False),
        (date(2026, 10, 18), "unconfirmed", False),
        (date(2026, 10, 18), "cancelled", False),
    ],
)
def test_incoming_eligibility(arrival, status, expected):
    order = IncomingOrderInput("PO", 10, arrival, status)
    assert is_incoming_eligible(order, START, date(2026, 10, 22)) is expected


@pytest.mark.parametrize(
    "lead,available,expected",
    [(0, True, True), (6, True, True), (7, True, False), (3, False, False)],
)
def test_supplier_eligibility(lead, available, expected):
    assert is_supplier_eligible(offer(lead=lead, available=available), 7) is expected


# --- baseline ----------------------------------------------------------------


def test_baseline_formula():
    assert compute_baseline(1000, 100, 200, 100) == 800
    assert compute_baseline(1000, 100, 1000, 100) == 0  # clamped
    assert compute_baseline(0, 0, 0, 0) == 0


# --- supplier selection ------------------------------------------------------


def test_select_supplier_is_deterministic_lowest_price():
    offers = (
        offer(name="B", price="13.00"),
        offer(name="A", price="12.50"),
        offer(name="C", price="12.50"),
    )
    chosen = select_supplier(offers, 7)
    assert chosen is not None
    assert chosen.supplier_name == "A"  # price tie -> name tie-break


def test_select_supplier_none_when_ineligible():
    assert select_supplier((offer(available=False), offer(lead=9)), 7) is None


# --- fingerprint -------------------------------------------------------------


def test_fingerprint_deterministic_and_sensitive():
    assert fingerprint_evidence(CASE, evidence()) == fingerprint_evidence(CASE, evidence())
    changed = evidence(forecast_demand=1001)
    assert fingerprint_evidence(CASE, evidence()) != fingerprint_evidence(CASE, changed)


# --- decisions ---------------------------------------------------------------


def test_main_scenario_accepts_800():
    draft = build_recommendation(CASE, evidence())
    assert draft.decision == "accept"
    assert draft.baseline_required_quantity == 800
    assert draft.recommended_quantity == 800
    assert draft.total_cost == D("10000.0000")
    assert draft.supplier_name == "Distribuidora Norte"
    assert all(c.status == "pass" for c in draft.constraint_checks)


def test_reject_when_requirement_zero():
    draft = build_recommendation(CASE, evidence(usable_inventory=2000))
    assert draft.decision == "reject"
    assert draft.recommended_quantity is None
    assert draft.baseline_required_quantity == 0


def test_moq_overage_escalates_never_rounds_up():
    draft = build_recommendation(CASE, evidence(supplier_offers=(offer(moq=1000),)))
    assert draft.decision == "investigate"
    assert draft.recommended_quantity == 800  # baseline, not the MOQ
    assert draft.missing_information
    moq_check = next(c for c in draft.constraint_checks if c.constraint_type == "moq")
    assert moq_check.status == "fail"


def test_missing_evidence_is_investigate_with_null_quantity():
    draft = build_recommendation(CASE, evidence(forecast_demand=None))
    assert draft.decision == "investigate"
    assert draft.recommended_quantity is None
    assert draft.missing_information
    dq = next(c for c in draft.constraint_checks if c.constraint_type == "data_quality")
    assert dq.status == "fail"


def test_missing_budget_yields_unknown_not_pass():
    draft = build_recommendation(CASE, evidence(budget_amount=None))
    assert draft.decision == "investigate"
    budget = next(c for c in draft.constraint_checks if c.constraint_type == "budget")
    assert budget.status == "unknown"


def test_budget_failure_blocks_but_keeps_accepted_quantity():
    draft = build_recommendation(CASE, evidence(budget_amount=D("5000")))
    assert draft.decision == "accept"
    assert draft.recommended_quantity == 800
    budget = next(c for c in draft.constraint_checks if c.constraint_type == "budget")
    assert budget.status == "fail"


def test_storage_failure_flagged():
    draft = build_recommendation(CASE, evidence(storage_capacity_units=D("500")))
    storage = next(c for c in draft.constraint_checks if c.constraint_type == "storage")
    assert storage.status == "fail"


def test_currency_contradiction_is_investigate():
    draft = build_recommendation(CASE, evidence(supplier_offers=(offer(),)))
    bad = SupplierOfferInput(None, "Otro", D("10"), "USD", 0, 3, True)
    draft = build_recommendation(CASE, evidence(supplier_offers=(bad,)))
    assert draft.decision == "investigate"
    assert any("currency" in m.lower() for m in draft.missing_information)


def test_no_eligible_supplier_is_investigate():
    draft = build_recommendation(CASE, evidence(supplier_offers=(offer(available=False),)))
    assert draft.decision == "investigate"
    avail = next(c for c in draft.constraint_checks if c.constraint_type == "supplier_availability")
    assert avail.status in ("fail", "unknown")


def test_rationale_override_does_not_change_quantities():
    draft = build_recommendation(CASE, evidence(), rationale_override="LLM text")
    assert draft.rationale == "LLM text"
    assert draft.recommended_quantity == 800
