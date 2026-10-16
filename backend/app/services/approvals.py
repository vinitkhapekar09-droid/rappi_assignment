"""Buyer approval / rejection decisions."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import approval_mismatch, invalid_state, not_found, recommendation_superseded
from app.models import Approval, RecommendationVersion
from app.schemas import DecisionRequest
from app.services import response as R
from app.services.common import lock_case, utcnow


def _load_version(
    session: Session, case_id: uuid.UUID, version_number: int
) -> RecommendationVersion | None:
    return session.execute(
        select(RecommendationVersion).where(
            RecommendationVersion.case_id == case_id,
            RecommendationVersion.version_number == version_number,
        )
    ).scalar_one_or_none()


def decide(
    session: Session, case_id: uuid.UUID, version_number: int, req: DecisionRequest
) -> tuple[dict, int]:
    case = lock_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    version = _load_version(session, case_id, version_number)
    if version is None:
        raise not_found(f"Recommendation version {version_number} does not exist.")

    if (
        case.current_recommendation_version_id != version.id
        or version.status != "ready"
    ):
        raise recommendation_superseded(
            f"Version {version_number} is not the current recommendation version.",
            current_version=None
            if case.current_recommendation_version_id is None
            else "see current_recommendation_version",
        )

    now = utcnow()
    if case.status not in ("awaiting_approval", "approved"):
        raise invalid_state(
            f"Case is '{case.status}' and cannot accept a buyer decision.",
            case_status=case.status,
        )

    if req.decision == "approved":
        if version.decision not in ("accept", "modify"):
            raise invalid_state(
                f"Version {version_number} has decision '{version.decision}' and cannot be approved."
            )
        if req.approved_quantity != version.recommended_quantity:
            raise approval_mismatch(
                "Approved quantity does not match the recommendation version.",
                approved_quantity=req.approved_quantity,
                recommended_quantity=version.recommended_quantity,
            )

        existing = session.execute(
            select(Approval).where(
                Approval.recommendation_version_id == version.id,
                Approval.decision == "approved",
                Approval.invalidated_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.approved_quantity == req.approved_quantity
                and existing.approver == req.approver
            ):
                return R.approval_dict(existing, version), 200
            raise approval_mismatch(
                "An active approval already exists for this version with different details."
            )

        total_cost = version.unit_price * version.recommended_quantity
        approval = Approval(
            case_id=case_id,
            recommendation_version_id=version.id,
            decision="approved",
            approved_quantity=version.recommended_quantity,
            supplier_offer_id=version.chosen_supplier_offer_id,
            supplier_name=version.supplier_name,
            unit_price=version.unit_price,
            total_cost=total_cost,
            currency=version.currency,
            approver=req.approver,
            note=req.note,
        )
        session.add(approval)
        case.status = "approved"
        case.updated_at = now
        session.commit()
        session.refresh(approval)
        return R.approval_dict(approval, version), 201

    # rejected
    session.execute(
        update(Approval).where(
            Approval.recommendation_version_id == version.id,
            Approval.invalidated_at.is_(None),
        ).values(invalidated_at=now)
    )
    approval = Approval(
        case_id=case_id,
        recommendation_version_id=version.id,
        decision="rejected",
        approver=req.approver,
        note=req.note,
    )
    session.add(approval)
    case.status = "rejected"
    case.updated_at = now
    session.commit()
    session.refresh(approval)
    return R.approval_dict(approval, version), 201
