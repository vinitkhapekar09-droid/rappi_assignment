"""Shared service helpers: ORM <-> domain conversion and locking."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import (
    CaseContext,
    EvidenceInput,
    IncomingOrderInput,
    SupplierOfferInput,
)
from app.models import (
    CaseEvidence,
    ConstraintCheck,
    IncomingPurchaseOrder,
    PurchasingCase,
    SupplierOffer,
)

# Case states in which evidence/recommendations may not be changed or regenerated.
LOCKED_CASE_STATES = frozenset(
    {
        "executing",
        "validating",
        "validated",
        "execution_failed",
        "validation_failed",
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def lock_case(session: Session, case_id: uuid.UUID) -> PurchasingCase | None:
    return session.execute(
        select(PurchasingCase).where(PurchasingCase.id == case_id).with_for_update()
    ).scalar_one_or_none()


def load_case(session: Session, case_id: uuid.UUID) -> PurchasingCase | None:
    return session.get(PurchasingCase, case_id)


def load_evidence_rows(
    session: Session, case_id: uuid.UUID
) -> tuple[CaseEvidence | None, list[IncomingPurchaseOrder], list[SupplierOffer]]:
    evidence = session.get(CaseEvidence, case_id)
    incoming = list(
        session.execute(
            select(IncomingPurchaseOrder)
            .where(IncomingPurchaseOrder.case_id == case_id)
            .order_by(IncomingPurchaseOrder.external_ref)
        )
        .scalars()
        .all()
    )
    offers = list(
        session.execute(
            select(SupplierOffer)
            .where(SupplierOffer.case_id == case_id, SupplierOffer.is_current.is_(True))
            .order_by(SupplierOffer.supplier_name)
        )
        .scalars()
        .all()
    )
    return evidence, incoming, offers


def load_constraint_checks(session: Session, version_id: uuid.UUID) -> list[ConstraintCheck]:
    return list(
        session.execute(
            select(ConstraintCheck)
            .where(ConstraintCheck.recommendation_version_id == version_id)
            .order_by(ConstraintCheck.constraint_type)
        )
        .scalars()
        .all()
    )


def case_context(case: PurchasingCase) -> CaseContext:
    return CaseContext(
        sku=case.sku,
        currency=case.currency,
        volume_unit=case.volume_unit,
        planning_horizon_start=case.planning_horizon_start,
        planning_horizon_days=case.planning_horizon_days,
        initial_recommended_quantity=case.initial_recommended_quantity,
    )


def domain_evidence(
    evidence: CaseEvidence, incoming: list[IncomingPurchaseOrder], offers: list[SupplierOffer]
) -> EvidenceInput:
    return EvidenceInput(
        usable_inventory=evidence.usable_inventory,
        forecast_demand=evidence.forecast_demand,
        safety_stock=evidence.safety_stock,
        budget_amount=None if evidence.budget_amount is None else Decimal(evidence.budget_amount),
        storage_capacity_units=None
        if evidence.storage_capacity_units is None
        else Decimal(evidence.storage_capacity_units),
        storage_required_per_unit=None
        if evidence.storage_required_per_unit is None
        else Decimal(evidence.storage_required_per_unit),
        incoming_orders=tuple(
            IncomingOrderInput(
                external_ref=o.external_ref,
                quantity=o.quantity,
                expected_arrival_date=o.expected_arrival_date,
                status=o.status,
            )
            for o in incoming
        ),
        supplier_offers=tuple(
            SupplierOfferInput(
                id=o.id,
                supplier_name=o.supplier_name,
                unit_price=Decimal(o.unit_price),
                currency=o.currency,
                moq=o.moq,
                lead_time_days=o.lead_time_days,
                is_available=o.is_available,
            )
            for o in offers
        ),
    )
