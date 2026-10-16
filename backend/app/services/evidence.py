"""Evidence replacement."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.errors import AppError, invalid_state, not_found
from app.models import (
    Approval,
    CaseEvidence,
    IncomingPurchaseOrder,
    RecommendationVersion,
    SupplierOffer,
)
from app.schemas import EvidenceRequest
from app.services import response as R
from app.services.common import LOCKED_CASE_STATES, lock_case, utcnow


def replace_evidence(session: Session, case_id: uuid.UUID, req: EvidenceRequest) -> dict:
    case = lock_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    if case.status in LOCKED_CASE_STATES:
        raise invalid_state(
            f"Case is '{case.status}' and cannot be edited.",
            case_status=case.status,
        )

    # Currency consistency (docs/API_CONTRACT.md §5.3; docs/DATABASE_SCHEMA.md §2.4 note).
    for offer in req.supplier_offers:
        if offer.currency.upper() != case.currency:
            raise AppError(
                "validation_error",
                422,
                f"Supplier '{offer.supplier_name}' currency {offer.currency} does not match "
                f"case currency {case.currency}.",
            )

    now = utcnow()

    # Supersede the current recommendation and invalidate its approvals before touching
    # the collections (so no approval can be created against evidence that just changed).
    if case.current_recommendation_version_id is not None:
        current = session.get(RecommendationVersion, case.current_recommendation_version_id)
        if current is not None and current.status == "ready":
            current.status = "superseded"
    session.execute(
        update(Approval)
        .where(Approval.case_id == case_id, Approval.invalidated_at.is_(None))
        .values(invalidated_at=now)
    )
    case.current_recommendation_version_id = None

    # Archive prior offers without changing the supplier references or terms on
    # immutable versions and approvals. Only current offers are live evidence.
    session.execute(
        update(SupplierOffer).where(
            SupplierOffer.case_id == case_id, SupplierOffer.is_current.is_(True),
        ).values(is_current=False)
    )

    evidence = session.get(CaseEvidence, case_id)
    if evidence is None:
        evidence = CaseEvidence(case_id=case_id)
        session.add(evidence)
    evidence.usable_inventory = req.usable_inventory
    evidence.forecast_demand = req.forecast_demand
    evidence.safety_stock = req.safety_stock
    evidence.budget_amount = req.budget_amount
    evidence.storage_capacity_units = req.storage_capacity_units
    evidence.storage_required_per_unit = req.storage_required_per_unit
    evidence.evidence_as_of = req.evidence_as_of
    evidence.updated_at = now

    # Full replacement of collections.
    session.execute(delete(IncomingPurchaseOrder).where(IncomingPurchaseOrder.case_id == case_id))
    session.flush()

    incoming = [
        IncomingPurchaseOrder(
            case_id=case_id,
            external_ref=o.external_ref,
            quantity=o.quantity,
            expected_arrival_date=o.expected_arrival_date,
            status=o.status,
        )
        for o in req.incoming_orders
    ]
    offers = [
        SupplierOffer(
            case_id=case_id,
            supplier_name=o.supplier_name,
            unit_price=o.unit_price,
            currency=o.currency.upper(),
            moq=o.moq,
            lead_time_days=o.lead_time_days,
            is_available=o.is_available,
        )
        for o in req.supplier_offers
    ]
    session.add_all(incoming)
    session.add_all(offers)

    case.status = "draft"
    case.updated_at = now

    session.commit()
    session.refresh(evidence)
    return R.evidence_dict(
        evidence,
        sorted(incoming, key=lambda o: o.external_ref),
        sorted(offers, key=lambda o: o.supplier_name),
    )
