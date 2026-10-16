"""Structured model output, numeric guard, and deterministic rationale.

The LLM may only interpret evidence. Two independent safeguards keep it from
influencing a decision:

1. :class:`~app.agent.models.RecommendationExplanation` has no quantity, price,
   supplier, or decision field, so the model cannot set an authoritative value.
2. Every string it returns is passed through :func:`validate_and_sanitize`, which
   drops any sentence containing a number that is not already present in the
   deterministic result. Numbers can therefore be restated but not introduced or
   contradicted.

Only :func:`build_authoritative_rationale` supplies the canonical numbers and
constraint outcomes. If the model fails or is rejected entirely, the workflow
still produces a complete deterministic rationale.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.models import (
    ExplanationContext,
    InvestigationResult,
    RecommendationExplanation,
)

_NUMBER_RE = re.compile(r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d,])")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_FALLBACK_SUMMARY = {
    "accept": "The deterministic requirement is supported by the available evidence.",
    "modify": "The recommended quantity deviates from the baseline and is justified.",
    "reject": "No purchase is justified for the planning horizon.",
    "investigate": "The situation requires buyer investigation before any purchase.",
}


def extract_numbers(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            # Accept standard thousands grouping as formatting of the same
            # value, while still comparing the normalized value exactly.
            values.append(Decimal(match.group(0).replace(",", "")))
        except InvalidOperation:  # pragma: no cover - regex is already numeric
            continue
    return values


def allowed_numbers(context: ExplanationContext) -> set[Decimal]:
    """The set of numeric values the model is permitted to mention."""
    investigation = context.investigation
    case = context.case
    evidence = investigation.evidence

    allowed: list[Decimal] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, Decimal):
            allowed.append(value)
        elif isinstance(value, bool):
            return
        elif isinstance(value, int):
            allowed.append(Decimal(value))
        elif isinstance(value, float):
            allowed.append(Decimal(str(value)))

    add(case.planning_horizon_days)
    add(case.initial_recommended_quantity)
    add(investigation.baseline_required_quantity)
    add(investigation.recommended_quantity)
    add(investigation.eligible_incoming_quantity)
    add(investigation.moq)
    add(investigation.lead_time_days)
    add(investigation.unit_price)
    add(investigation.total_cost)
    add(evidence.usable_inventory)
    add(evidence.forecast_demand)
    add(evidence.safety_stock)
    add(evidence.eligible_incoming_quantity)
    add(evidence.budget_amount)
    add(evidence.storage_capacity_units)
    add(evidence.storage_required_per_unit)
    for check in investigation.constraint_checks:
        add(check.evaluated_value)
        add(check.limit_value)
    if context.evidence is not None:
        for order in context.evidence.incoming_orders:
            add(order.quantity)
            allowed.extend(extract_numbers(order.expected_arrival_date.isoformat()))
        for offer in context.evidence.supplier_offers:
            add(offer.unit_price)
            add(offer.moq)
            add(offer.lead_time_days)

    # Numbers already present in authoritative text (dates, written values).
    authoritative_text = " ".join(
        [build_authoritative_rationale(investigation), *investigation.assumptions]
        + [check.message for check in investigation.constraint_checks]
    )
    allowed.extend(extract_numbers(authoritative_text))

    return set(allowed)


def coerce_explanation(
    raw: RecommendationExplanation | str | dict[str, Any],
) -> RecommendationExplanation:
    """Accept the adapter's output in any reasonable shape."""
    if isinstance(raw, RecommendationExplanation):
        return raw
    if isinstance(raw, dict):
        return RecommendationExplanation.model_validate(raw)
    text = (raw or "").strip()
    if not text:
        return RecommendationExplanation()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return RecommendationExplanation(summary=text)
    if isinstance(parsed, dict):
        return RecommendationExplanation.model_validate(parsed)
    return RecommendationExplanation(summary=text)


def validate_and_sanitize(
    raw: RecommendationExplanation | str | dict[str, Any],
    context: ExplanationContext,
) -> tuple[RecommendationExplanation, list[str]]:
    """Coerce, then remove any sentence that introduces a disallowed number.

    Returns the sanitised explanation and a list of warning strings. Never
    raises: an invalid model output degrades to a safe deterministic explanation.
    """
    warnings: list[str] = []
    try:
        explanation = coerce_explanation(raw)
    except Exception as exc:  # noqa: BLE001 - untrusted model output
        warnings.append(f"Rejected unparseable model output: {type(exc).__name__}.")
        return RecommendationExplanation(summary=_fallback_summary(context)), warnings

    allowed = allowed_numbers(context)

    summary, removed = _sanitize_text(explanation.summary, allowed)
    if removed:
        warnings.append("Dropped model sentence(s) with unsupported numbers in summary.")
    if not summary:
        summary = _fallback_summary(context)

    key_evidence, removed_ke = _sanitize_list(explanation.key_evidence, allowed)
    if removed_ke:
        warnings.append("Dropped model sentence(s) with unsupported numbers in key_evidence.")

    trade_offs, removed_to = _sanitize_list(explanation.trade_offs, allowed)
    if removed_to:
        warnings.append("Dropped model sentence(s) with unsupported numbers in trade_offs.")

    caveats, removed_ca = _sanitize_list(explanation.caveats, allowed)
    if removed_ca:
        warnings.append("Dropped model sentence(s) with unsupported numbers in caveats.")

    return (
        RecommendationExplanation(
            summary=summary,
            key_evidence=key_evidence,
            trade_offs=trade_offs,
            caveats=caveats,
            confidence=explanation.confidence,
        ),
        warnings,
    )


def build_authoritative_rationale(investigation: InvestigationResult) -> str:
    """Deterministic rationale carrying every canonical number and outcome."""
    parts: list[str] = [f"Decision: {investigation.decision}."]
    if investigation.baseline_required_quantity is not None:
        parts.append(
            f"Baseline requirement is {investigation.baseline_required_quantity} units."
        )
    if investigation.recommended_quantity is not None:
        parts.append(
            f"Recommended quantity is {investigation.recommended_quantity} units."
        )
    if investigation.total_cost is not None:
        parts.append(
            f"Total cost is {investigation.total_cost} {investigation.currency}."
        )
    if investigation.moq is not None:
        parts.append(f"Supplier MOQ is {investigation.moq} units.")
    if investigation.lead_time_days is not None:
        parts.append(f"Supplier lead time is {investigation.lead_time_days} days.")
    for check in investigation.constraint_checks:
        parts.append(f"{check.constraint_type} check: {check.status} - {check.message}")
    if any(c.status in ("fail", "unknown") for c in investigation.constraint_checks):
        parts.append("Purchase blocked: resolve failed or unknown constraints before execution.")
    return " ".join(parts)


def compose_rationale(
    investigation: InvestigationResult, explanation: RecommendationExplanation
) -> str:
    """Authoritative rationale first, then the sanitised interpretive prose."""
    authoritative = build_authoritative_rationale(investigation)
    extra = " ".join(
        text
        for text in [
            explanation.summary,
            *explanation.key_evidence,
            *explanation.trade_offs,
            *explanation.caveats,
        ]
        if text
    ).strip()
    return f"{authoritative} {extra}".strip() if extra else authoritative


def _fallback_summary(context: ExplanationContext) -> str:
    if any(c.status in ("fail", "unknown") for c in context.investigation.constraint_checks):
        return "Purchase blocked pending resolution of failed or unknown constraints."
    return _FALLBACK_SUMMARY[context.investigation.decision]


def _sanitize_text(text: str, allowed: set[Decimal]) -> tuple[str, bool]:
    text = (text or "").strip()
    if not text:
        return "", False
    kept: list[str] = []
    removed = False
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if _numbers_are_allowed(sentence, allowed):
            kept.append(sentence.strip())
        else:
            removed = True
    return " ".join(kept).strip(), removed


def _sanitize_list(
    items: Iterable[str], allowed: set[Decimal]
) -> tuple[list[str], bool]:
    result: list[str] = []
    removed_any = False
    for item in items:
        text, removed = _sanitize_text(item, allowed)
        if text:
            result.append(text)
        if removed:
            removed_any = True
    return result, removed_any


def _numbers_are_allowed(text: str, allowed: set[Decimal]) -> bool:
    return all(value in allowed for value in extract_numbers(text))
