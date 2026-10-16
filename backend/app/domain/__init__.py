"""Deterministic purchasing domain: pure, offline-testable business rules."""

from app.domain.purchasing import (
    CaseContext,
    ConstraintCheckResult,
    EvidenceInput,
    IncomingOrderInput,
    RecommendationDraft,
    SupplierOfferInput,
    build_recommendation,
    compute_baseline,
    compute_eligible_incoming,
    fingerprint_evidence,
    horizon_end,
    is_incoming_eligible,
    is_supplier_eligible,
    select_supplier,
)

__all__ = [
    "CaseContext",
    "ConstraintCheckResult",
    "EvidenceInput",
    "IncomingOrderInput",
    "RecommendationDraft",
    "SupplierOfferInput",
    "build_recommendation",
    "compute_baseline",
    "compute_eligible_incoming",
    "fingerprint_evidence",
    "horizon_end",
    "is_incoming_eligible",
    "is_supplier_eligible",
    "select_supplier",
]
