"""Recommendation generation, listing, and retrieval."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agent.adapters.backend import generate_agent_draft
from app.errors import invalid_state, missing_evidence, not_found
from app.models import (
    Approval,
    CaseEvidence,
    ConstraintCheck,
    PurchasingCase,
    RecommendationVersion,
)
from app.services import response as R
from app.services.common import (
    LOCKED_CASE_STATES,
    case_context,
    domain_evidence,
    load_case,
    load_constraint_checks,
    load_evidence_rows,
    lock_case,
    utcnow,
)

_DECISION_TO_CASE_STATUS = {
    "accept": "awaiting_approval",
    "modify": "awaiting_approval",
    "investigate": "needs_investigation",
    "reject": "rejected",
}


def generate_recommendation(session: Session, case_id: uuid.UUID) -> dict:
    case = lock_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    if case.status in LOCKED_CASE_STATES:
        raise invalid_state(
            f"Case is '{case.status}' and cannot generate a new recommendation.",
            case_status=case.status,
        )

    evidence, incoming, offers = load_evidence_rows(session, case_id)
    if evidence is None:
        raise missing_evidence(
            "No case evidence has been submitted; nothing to investigate.",
            case_id=str(case_id),
        )

    ctx = case_context(case)
    ev = domain_evidence(evidence, incoming, offers)
    draft = generate_agent_draft(case_id, ctx, ev)

    now = utcnow()

    # Supersede the current version and invalidate its active approvals.
    if case.current_recommendation_version_id is not None:
        current = session.get(RecommendationVersion, case.current_recommendation_version_id)
        if current is not None and current.status == "ready":
            current.status = "superseded"
            session.execute(
                update(Approval)
                .where(
                    Approval.recommendation_version_id == current.id,
                    Approval.invalidated_at.is_(None),
                )
                .values(invalidated_at=now)
            )

    next_number = (
        session.execute(
            select(func.coalesce(func.max(RecommendationVersion.version_number), 0)).where(
                RecommendationVersion.case_id == case_id
            )
        ).scalar_one()
        + 1
    )

    version = RecommendationVersion(
        case_id=case_id,
        version_number=next_number,
        status="ready",
        decision=draft.decision,
        initial_recommended_quantity=case.initial_recommended_quantity,
        baseline_required_quantity=draft.baseline_required_quantity,
        recommended_quantity=draft.recommended_quantity,
        chosen_supplier_offer_id=draft.chosen_supplier_offer_id,
        supplier_name=draft.supplier_name,
        unit_price=draft.unit_price,
        currency=draft.currency,
        total_cost=draft.total_cost,
        lead_time_days=draft.lead_time_days,
        moq=draft.moq,
        rationale=draft.rationale,
        evidence_snapshot=draft.evidence_snapshot,
        assumptions=draft.assumptions,
        missing_information=draft.missing_information,
        inputs_fingerprint=draft.inputs_fingerprint,
        generated_by=draft.generated_by,
    )
    session.add(version)
    session.flush()

    checks: list[ConstraintCheck] = []
    for c in draft.constraint_checks:
        check = ConstraintCheck(
            recommendation_version_id=version.id,
            constraint_type=c.constraint_type,
            status=c.status,
            evaluated_value=c.evaluated_value,
            limit_value=c.limit_value,
            unit=c.unit,
            message=c.message,
            details=c.details,
        )
        session.add(check)
        checks.append(check)

    case.current_recommendation_version_id = version.id
    case.status = _DECISION_TO_CASE_STATUS[draft.decision]
    case.updated_at = now

    session.commit()
    session.refresh(version)
    return R.recommendation_dict(version, checks, case)


def list_recommendations(session: Session, case_id: uuid.UUID, latest: bool = False) -> dict:
    case = load_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    stmt = (
        select(RecommendationVersion)
        .where(RecommendationVersion.case_id == case_id)
        .order_by(RecommendationVersion.version_number.desc())
    )
    if latest:
        stmt = stmt.limit(1)
    versions = list(session.execute(stmt).scalars().all())
    current_number = None
    if case.current_recommendation_version_id is not None:
        current_number = session.execute(
            select(RecommendationVersion.version_number).where(
                RecommendationVersion.id == case.current_recommendation_version_id
            )
        ).scalar_one_or_none()
    return {
        "case_id": str(case_id),
        "current_recommendation_version": current_number,
        "recommendations": [R.recommendation_summary_dict(v) for v in versions],
    }


def get_recommendation(
    session: Session, case_id: uuid.UUID, version_number: int
) -> dict:
    case = load_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    version = session.execute(
        select(RecommendationVersion).where(
            RecommendationVersion.case_id == case_id,
            RecommendationVersion.version_number == version_number,
        )
    ).scalar_one_or_none()
    if version is None:
        raise not_found(f"Recommendation version {version_number} does not exist.")
    checks = load_constraint_checks(session, version.id)
    return R.recommendation_dict(version, checks, case)
