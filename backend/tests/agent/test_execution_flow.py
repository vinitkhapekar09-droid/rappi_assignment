"""Approval guards, idempotent execution, failure, and validation tests."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.agent.support import build_harness, make_approval


def _harness_with_approval(**kwargs):
    approval = kwargs.pop("approval", None) or make_approval()
    harness = build_harness(approvals={approval.approval_id: approval}, **kwargs)
    return harness, approval


def test_happy_path_executes_and_validates() -> None:
    harness, approval = _harness_with_approval()
    outcome = harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)

    assert outcome.status == "validated"
    assert outcome.escalated is False
    assert outcome.execution.outcome == "succeeded"
    assert outcome.validation.status == "passed"
    assert outcome.validation.mismatches == []
    assert harness.backend.create_calls == 1
    assert harness.backend.validation_calls == 1
    assert len(harness.backend.purchase_orders) == 1


def test_duplicate_execution_creates_only_one_purchase_order() -> None:
    harness, approval = _harness_with_approval()

    first = harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)
    second = harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)

    assert first.execution.purchase_order_id == second.execution.purchase_order_id
    assert harness.backend.create_calls == 1
    assert len(harness.backend.purchase_orders) == 1


def test_execute_without_validate_stops_before_validation() -> None:
    harness, approval = _harness_with_approval()
    outcome = harness.workflow.execute("case-1", 1, approval.approval_id)

    assert outcome.status == "validating"
    assert outcome.execution.outcome == "succeeded"
    assert outcome.validation is None
    assert harness.backend.validation_calls == 0


def test_missing_approval_is_rejected_without_execution() -> None:
    harness = build_harness()
    outcome = harness.workflow.execute("case-1", 1, "does-not-exist")

    assert outcome.status == "needs_investigation"
    assert outcome.escalated is True
    assert outcome.error_code == "stale_approval"
    assert harness.backend.create_calls == 0


def test_stale_version_approval_is_rejected() -> None:
    harness, approval = _harness_with_approval()
    outcome = harness.workflow.execute("case-1", 2, approval.approval_id)

    assert outcome.escalated is True
    assert outcome.error_code == "recommendation_superseded"
    assert harness.backend.create_calls == 0


def test_rejected_decision_is_not_an_approval() -> None:
    approval = make_approval(decision="rejected", approved_quantity=None)
    harness = build_harness(approvals={approval.approval_id: approval})
    outcome = harness.workflow.execute("case-1", 1, approval.approval_id)

    assert outcome.error_code == "stale_approval"
    assert harness.backend.create_calls == 0


def test_invalidated_approval_is_rejected() -> None:
    approval = make_approval(invalidated_at=datetime(2026, 10, 15, 13, 0, tzinfo=UTC))
    harness = build_harness(approvals={approval.approval_id: approval})
    outcome = harness.workflow.execute("case-1", 1, approval.approval_id)

    assert outcome.error_code == "stale_approval"
    assert harness.backend.create_calls == 0


def test_execution_failure_does_not_retry() -> None:
    harness, approval = _harness_with_approval(execution_mode="fail")
    outcome = harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)

    assert outcome.status == "execution_failed"
    assert outcome.escalated is True
    assert outcome.error_code == "execution_failed"
    assert harness.backend.create_calls == 1
    assert len(harness.backend.purchase_orders) == 0


def test_uncertain_execution_is_parked_not_retried() -> None:
    harness, approval = _harness_with_approval(execution_mode="uncertain")
    outcome = harness.workflow.execute("case-1", 1, approval.approval_id)

    assert outcome.status == "validating"
    assert outcome.escalated is True
    assert outcome.error_code == "execution_uncertain"
    assert harness.backend.create_calls == 1


def test_persisted_mismatch_is_detected_and_escalated() -> None:
    harness, approval = _harness_with_approval(mismatch={"quantity": 500})
    outcome = harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)

    assert outcome.status == "validation_failed"
    assert outcome.escalated is True
    assert outcome.validation.status == "failed"
    mismatched = {m.field for m in outcome.validation.mismatches}
    assert {"quantity", "total_cost"} <= mismatched
    po = next(iter(harness.backend.purchase_orders.values()))
    assert po.status == "validation_failed"
    # The action is halted; no automatic retry.
    assert harness.backend.create_calls == 1


def test_terminal_validation_is_idempotent() -> None:
    harness, approval = _harness_with_approval(mismatch={"quantity": 500})
    harness.workflow.execute_and_validate("case-1", 1, approval.approval_id)
    po_id = next(iter(harness.backend.purchase_orders))

    replay = harness.workflow.validate("case-1", po_id)

    assert replay.validation.status == "failed"
    assert harness.backend.validation_calls == 1


def test_validation_of_unknown_purchase_order_is_inconclusive() -> None:
    harness = build_harness()
    outcome = harness.workflow.validate("case-1", "unknown-po")

    assert outcome.status == "validating"
    assert outcome.escalated is True
    assert outcome.validation.status == "inconclusive"


def test_escalation_event_is_recorded() -> None:
    approval = make_approval(decision="rejected", approved_quantity=None)
    harness = build_harness(approvals={approval.approval_id: approval})
    harness.workflow.execute("case-1", 1, approval.approval_id)

    events = [event.event for event in harness.recorder.events]
    assert "approval_rejected" in events
    assert "escalated" in events
