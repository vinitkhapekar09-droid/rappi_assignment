"""Deterministic, credential-free LLM adapter.

This is the default provider. It produces a stable explanation derived only from
the deterministic investigation result, so the whole workflow and its tests run
without an external model, network access, or API credentials.
"""

from __future__ import annotations

from app.agent.models import (
    ExplanationContext,
    RecommendationExplanation,
)

_SUMMARIES = {
    "accept": (
        "The deterministic seven-day requirement is fully supported by the "
        "available supplier, lead-time, budget, and storage evidence."
    ),
    "modify": (
        "A deviation from the baseline requirement is justified by the recorded "
        "constraints and is explained in the constraint checks."
    ),
    "reject": (
        "No purchase is justified for this planning horizon based on the "
        "deterministic evidence."
    ),
    "investigate": (
        "The situation cannot be resolved deterministically and requires buyer "
        "investigation before any purchase."
    ),
}

_CONFIDENCE = {
    "accept": "high",
    "modify": "medium",
    "reject": "high",
    "investigate": "low",
}


class OfflineLLMClient:
    """Deterministic adapter with the :class:`~app.agent.ports.LLMClient` shape."""

    name = "offline"
    is_offline = True

    def generate_explanation(
        self, context: ExplanationContext
    ) -> RecommendationExplanation:
        investigation = context.investigation

        key_evidence = [
            f"The {check.constraint_type.replace('_', ' ')} check is {check.status}."
            for check in investigation.constraint_checks
        ]

        trade_offs: list[str] = []
        if investigation.decision == "investigate":
            trade_offs.append(
                "Resolve the recorded missing or unresolved information before "
                "committing to a purchase."
            )

        caveats: list[str] = []
        blocked = [c.constraint_type for c in investigation.constraint_checks
                   if c.status in ("fail", "unknown")]
        if context.case.planning_horizon_days != 7:
            caveats.append("The planning horizon is not the assumed seven days.")

        return RecommendationExplanation(
            summary=("Purchase blocked: resolve the failed or unknown constraints before approval or execution."
                     if blocked else _SUMMARIES[investigation.decision]),
            key_evidence=key_evidence,
            trade_offs=trade_offs,
            caveats=caveats,
            confidence="low" if blocked else _CONFIDENCE[investigation.decision],  # type: ignore[arg-type]
        )
