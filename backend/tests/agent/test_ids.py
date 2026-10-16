"""Deterministic idempotency-key derivation."""

from __future__ import annotations

from app.agent.ids import derive_execution_idempotency_key


def test_key_is_stable_for_the_same_action() -> None:
    first = derive_execution_idempotency_key("case-1", 1, "approval-1", 800, "so-1")
    second = derive_execution_idempotency_key("case-1", 1, "approval-1", 800, "so-1")
    assert first == second


def test_key_changes_with_any_action_component() -> None:
    base = derive_execution_idempotency_key("case-1", 1, "approval-1", 800, "so-1")
    assert base != derive_execution_idempotency_key("case-1", 2, "approval-1", 800, "so-1")
    assert base != derive_execution_idempotency_key("case-1", 1, "approval-2", 800, "so-1")
    assert base != derive_execution_idempotency_key("case-1", 1, "approval-1", 500, "so-1")
    assert base != derive_execution_idempotency_key("case-1", 1, "approval-1", 800, "so-2")
