"""End-to-end recommendation-generation tests, fully offline."""

from __future__ import annotations

from decimal import Decimal

from tests.agent.support import build_harness, make_evidence


def test_recommendation_flow_produces_draft_for_approval() -> None:
    harness = build_harness()
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.status == "awaiting_approval"
    assert outcome.escalated is False
    draft = outcome.recommendation
    assert draft is not None
    assert draft.decision == "accept"
    assert draft.recommended_quantity == 800
    assert draft.baseline_required_quantity == 800
    assert draft.total_cost == Decimal("10000.00")
    assert draft.generated_by == "offline"
    assert draft.missing_information == []
    # All six constraint checks are present and passed.
    assert {c.constraint_type for c in draft.constraint_checks} == {
        "supplier_availability",
        "lead_time",
        "moq",
        "budget",
        "storage",
        "data_quality",
    }
    assert all(c.status == "pass" for c in draft.constraint_checks)


def test_rationale_contains_authoritative_numbers() -> None:
    harness = build_harness()
    outcome = harness.workflow.recommend(harness.case.case_id)
    rationale = outcome.recommendation.rationale

    assert "800" in rationale
    assert "10000.00" in rationale
    assert "Decision: accept." in rationale


def test_workflow_events_are_recorded() -> None:
    harness = build_harness()
    harness.workflow.recommend(harness.case.case_id)

    recorded = [event.event for event in harness.recorder.events]
    assert recorded == [
        "case_loaded",
        "evidence_loaded",
        "investigation_complete",
        "explanation_generated",
        "recommendation_ready",
    ]


def test_investigate_decision_escalates_offline() -> None:
    harness = build_harness(evidence=make_evidence(forecast_demand=None))
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.status == "needs_investigation"
    assert outcome.escalated is True
    assert outcome.recommendation.recommended_quantity is None
    assert outcome.recommendation.missing_information


def test_reject_decision_terminates_without_approval() -> None:
    harness = build_harness(
        evidence=make_evidence(forecast_demand=10, safety_stock=0, usable_inventory=500)
    )
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.status == "rejected"
    assert outcome.escalated is False
    assert outcome.recommendation.decision == "reject"
    assert outcome.recommendation.recommended_quantity is None
