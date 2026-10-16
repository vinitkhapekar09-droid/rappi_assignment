"""Case creation and retrieval."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import not_found
from app.models import PurchasingCase, RecommendationVersion
from app.schemas import CaseCreateRequest
from app.services import response as R
from app.services.common import load_evidence_rows


def _current_version_number(session: Session, case: PurchasingCase) -> int | None:
    if case.current_recommendation_version_id is None:
        return None
    return session.execute(
        select(RecommendationVersion.version_number).where(
            RecommendationVersion.id == case.current_recommendation_version_id
        )
    ).scalar_one_or_none()


def create_case(session: Session, req: CaseCreateRequest) -> dict:
    case = PurchasingCase(
        sku=req.sku,
        product_name=req.product_name,
        currency=req.currency.upper(),
        volume_unit=req.volume_unit,
        planning_horizon_start=req.planning_horizon_start or date.today(),
        planning_horizon_days=7,
        initial_recommended_quantity=req.initial_recommended_quantity,
        status="draft",
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return R.case_dict(case, current_version_number=None, evidence=None)


def get_case(session: Session, case_id: uuid.UUID) -> dict:
    case = session.get(PurchasingCase, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    evidence, incoming, offers = load_evidence_rows(session, case_id)
    return R.case_dict(
        case,
        current_version_number=_current_version_number(session, case),
        evidence=R.evidence_dict(evidence, incoming, offers),
    )
