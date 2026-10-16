"""Structured-output and numeric-guard tests.

The LLM may restate deterministic numbers but must not introduce or contradict
them, and it can never set an authoritative field.
"""

from __future__ import annotations

from app.agent.adapters.reference_domain import ReferenceDomainService
from app.agent.explanation import validate_and_sanitize
from app.agent.models import ExplanationContext, RecommendationExplanation
from tests.agent.support import StaticLLM, build_harness, make_case, make_evidence


def _context() -> ExplanationContext:
    case = make_case()
    evidence = make_evidence()
    investigation = ReferenceDomainService().investigate(case, evidence)
    return ExplanationContext(case=case, investigation=investigation)


def test_explanation_schema_has_no_authoritative_fields() -> None:
    assert set(RecommendationExplanation.model_fields) == {
        "summary",
        "key_evidence",
        "trade_offs",
        "caveats",
        "confidence",
    }


def test_valid_explanation_is_used() -> None:
    llm = StaticLLM(
        RecommendationExplanation(summary="The 800 unit requirement is fully covered.")
    )
    harness = build_harness(llm=llm)
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.warnings == []
    assert "fully covered" in outcome.recommendation.rationale
    assert llm.calls == 1


def test_unsupported_number_is_dropped_and_falls_back() -> None:
    llm = StaticLLM(RecommendationExplanation(summary="Buy 99999 units immediately."))
    harness = build_harness(llm=llm)
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert "99999" not in outcome.recommendation.rationale
    assert any("unsupported numbers" in warning for warning in outcome.warnings)


def test_partially_valid_text_keeps_only_safe_sentences() -> None:
    llm = StaticLLM(
        RecommendationExplanation(
            summary="The requirement is 800 units. Also order 424242 units."
        )
    )
    harness = build_harness(llm=llm)
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert "800 units" in outcome.recommendation.rationale
    assert "424242" not in outcome.recommendation.rationale


def test_provider_failure_falls_back_to_deterministic_explanation() -> None:
    llm = StaticLLM(RecommendationExplanation(), raises=RuntimeError("provider down"))
    harness = build_harness(llm=llm)
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.recommendation is not None
    assert "Decision: accept." in outcome.recommendation.rationale
    assert any("provider" in warning for warning in outcome.warnings)


def test_restating_deterministic_numbers_is_allowed() -> None:
    llm = StaticLLM(
        RecommendationExplanation(
            summary="Forecast demand is 1000 units and the budget is 15000.00."
        )
    )
    harness = build_harness(llm=llm)
    outcome = harness.workflow.recommend(harness.case.case_id)

    assert outcome.warnings == []
    assert "Forecast demand is 1000 units" in outcome.recommendation.rationale


def test_grouped_thousands_are_normalized_but_still_checked_exactly() -> None:
    explanation, warnings = validate_and_sanitize(
        RecommendationExplanation(
            summary="Forecast demand is 1,000 units and the budget is MXN 15,000.00.",
            trade_offs=["The order costs MXN 10,000.00."],
        ),
        _context(),
    )

    assert warnings == []
    assert "1,000 units" in explanation.summary
    assert explanation.trade_offs == ["The order costs MXN 10,000.00."]


def test_grouped_unsupported_thousands_are_still_rejected() -> None:
    explanation, warnings = validate_and_sanitize(
        RecommendationExplanation(
            summary="Forecast demand is 1,001 units.",
            trade_offs=["Ordering 1,000 units would add unnecessary stock."],
        ),
        _context(),
    )

    assert "1,001" not in explanation.summary
    assert "unsupported numbers" in warnings[0]
    assert explanation.trade_offs == ["Ordering 1,000 units would add unnecessary stock."]


def test_sku_digits_are_not_mistaken_for_numeric_claims() -> None:
    explanation, warnings = validate_and_sanitize(
        RecommendationExplanation(
            summary="SKU-1234 is supported by the available evidence.",
            key_evidence=["The SKU-1234 forecast supports 1000 units."],
        ),
        _context(),
    )

    assert warnings == []
    assert explanation.summary == "SKU-1234 is supported by the available evidence."
    assert explanation.key_evidence == [
        "The SKU-1234 forecast supports 1000 units."
    ]


def test_masking_sku_does_not_allow_same_digits_as_unsupported_quantity() -> None:
    explanation, warnings = validate_and_sanitize(
        RecommendationExplanation(
            summary="SKU-1234 requires 1234 units immediately."
        ),
        _context(),
    )

    assert "1234" not in explanation.summary
    assert any("unsupported numbers in summary" in warning for warning in warnings)


def test_derived_budget_remainder_is_rejected() -> None:
    explanation, warnings = validate_and_sanitize(
        RecommendationExplanation(
            summary="The recommendation is supported by forecast_demand.",
            trade_offs=["budget_amount leaves 5000.0000 MXN unspent."],
        ),
        _context(),
    )

    assert warnings == ["Dropped model sentence(s) with unsupported numbers in trade_offs."]
    assert explanation.trade_offs == []


def test_coerces_json_string_output() -> None:
    raw = '{"summary": "Requirement confirmed.", "confidence": "high"}'
    explanation, warnings = validate_and_sanitize(raw, _context())

    assert explanation.summary == "Requirement confirmed."
    assert explanation.confidence == "high"
    assert warnings == []


def test_unparseable_output_degrades_safely() -> None:
    explanation, warnings = validate_and_sanitize(
        {"summary": 123, "trade_offs": "not-a-list"},  # type: ignore[arg-type]
        _context(),
    )
    assert warnings
    assert explanation.summary
