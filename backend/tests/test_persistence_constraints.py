"""Database-level constraint tests: composite FKs, generated column, partial uniques,
and deterministic-arithmetic CHECKs (the safeguards the domain relies on)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Approval,
    PurchaseOrder,
    PurchasingCase,
    RecommendationVersion,
    ValidationResult,
)


def make_case(session, **overrides) -> PurchasingCase:
    case = PurchasingCase(
        sku=overrides.get("sku", "SKU-1234"),
        currency="MXN",
        volume_unit="liter",
        planning_horizon_start=date(2026, 10, 16),
        planning_horizon_days=7,
        initial_recommended_quantity=800,
    )
    session.add(case)
    session.commit()
    return case


def make_version(session, case, **overrides) -> RecommendationVersion:
    version = RecommendationVersion(
        case_id=case.id,
        version_number=overrides.get("version_number", 1),
        decision=overrides.get("decision", "accept"),
        initial_recommended_quantity=800,
        baseline_required_quantity=10,
        recommended_quantity=overrides.get("recommended_quantity", 10),
        supplier_name=overrides.get("supplier_name", "S"),
        unit_price=overrides.get("unit_price", 5),
        currency="MXN",
        total_cost=overrides.get("total_cost", 50),
        rationale="r",
        evidence_snapshot={},
        assumptions=[],
        missing_information=overrides.get("missing_information", []),
        inputs_fingerprint="f",
        generated_by="offline",
    )
    session.add(version)
    session.commit()
    return version


def test_planning_horizon_end_is_generated(db_session):
    case = make_case(db_session)
    db_session.refresh(case)
    assert case.planning_horizon_end == date(2026, 10, 22)


def test_recommendation_total_cost_check(db_session):
    case = make_case(db_session)
    with pytest.raises(IntegrityError):
        make_version(db_session, case, recommended_quantity=10, unit_price=5, total_cost=999)
    db_session.rollback()


def test_investigate_requires_missing_information(db_session):
    case = make_case(db_session)
    with pytest.raises(IntegrityError):
        make_version(
            db_session,
            case,
            decision="investigate",
            recommended_quantity=None,
            unit_price=None,
            total_cost=None,
            supplier_name=None,
            missing_information=[],
        )
    db_session.rollback()


def test_composite_fk_blocks_cross_case_approval(db_session):
    case_a = make_case(db_session)
    case_b = make_case(db_session)
    version_a = make_version(db_session, case_a, version_number=1)
    # Approval claims case_b but points at case_a's version -> composite FK must fail.
    with pytest.raises(IntegrityError):
        db_session.add(
            Approval(
                case_id=case_b.id,
                recommendation_version_id=version_a.id,
                decision="rejected",
                approver="buyer",
            )
        )
        db_session.commit()
    db_session.rollback()


def test_only_one_active_approval_per_version(db_session):
    case = make_case(db_session)
    version = make_version(db_session, case, version_number=1)
    terms = dict(
        approved_quantity=10,
        supplier_name="S",
        unit_price=5,
        total_cost=50,
        currency="MXN",
        approver="buyer",
    )
    db_session.add(
        Approval(
            case_id=case.id,
            recommendation_version_id=version.id,
            decision="approved",
            **terms,
        )
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            Approval(
                case_id=case.id,
                recommendation_version_id=version.id,
                decision="approved",
                **terms,
            )
        )
        db_session.commit()
    db_session.rollback()


def test_purchase_order_idempotency_key_is_unique(db_session):
    case = make_case(db_session)
    version = make_version(db_session, case, version_number=1)
    approval = Approval(
        case_id=case.id,
        recommendation_version_id=version.id,
        decision="approved",
        approved_quantity=10,
        supplier_name="S",
        unit_price=5,
        total_cost=50,
        currency="MXN",
        approver="buyer",
    )
    db_session.add(approval)
    db_session.commit()

    def make_po():
        return PurchaseOrder(
            case_id=case.id,
            recommendation_version_id=version.id,
            approval_id=approval.id,
            supplier_name="S",
            sku="SKU-1234",
            quantity=10,
            unit_price=5,
            total_cost=50,
            currency="MXN",
            idempotency_key="same-key",
            external_po_id=str(uuid.uuid4()),
        )

    db_session.add(make_po())
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(make_po())
        db_session.commit()
    db_session.rollback()


def test_only_one_terminal_validation_per_po(db_session):
    case = make_case(db_session)
    version = make_version(db_session, case, version_number=1)
    approval = Approval(
        case_id=case.id,
        recommendation_version_id=version.id,
        decision="approved",
        approved_quantity=10,
        supplier_name="S",
        unit_price=5,
        total_cost=50,
        currency="MXN",
        approver="buyer",
    )
    db_session.add(approval)
    db_session.commit()
    po = PurchaseOrder(
        case_id=case.id,
        recommendation_version_id=version.id,
        approval_id=approval.id,
        supplier_name="S",
        sku="SKU-1234",
        quantity=10,
        unit_price=5,
        total_cost=50,
        currency="MXN",
        idempotency_key="k",
        external_po_id="MOCK-PO-1",
    )
    db_session.add(po)
    db_session.commit()

    db_session.add(
        ValidationResult(
            case_id=case.id,
            purchase_order_id=po.id,
            recommendation_version_id=version.id,
            status="passed",
            checked_fields={},
            mismatches=[],
            persisted_snapshot={},
        )
    )
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            ValidationResult(
                case_id=case.id,
                purchase_order_id=po.id,
                recommendation_version_id=version.id,
                status="passed",
                checked_fields={},
                mismatches=[],
                persisted_snapshot={},
            )
        )
        db_session.commit()
    db_session.rollback()


def test_failed_validation_requires_mismatches(db_session):
    case = make_case(db_session)
    version = make_version(db_session, case, version_number=1)
    approval = Approval(
        case_id=case.id,
        recommendation_version_id=version.id,
        decision="approved",
        approved_quantity=10,
        supplier_name="S",
        unit_price=5,
        total_cost=50,
        currency="MXN",
        approver="buyer",
    )
    db_session.add(approval)
    db_session.commit()
    po = PurchaseOrder(
        case_id=case.id,
        recommendation_version_id=version.id,
        approval_id=approval.id,
        supplier_name="S",
        sku="SKU-1234",
        quantity=10,
        unit_price=5,
        total_cost=50,
        currency="MXN",
        idempotency_key="k2",
        external_po_id="MOCK-PO-2",
    )
    db_session.add(po)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            ValidationResult(
                case_id=case.id,
                purchase_order_id=po.id,
                recommendation_version_id=version.id,
                status="failed",
                checked_fields={},
                mismatches=[],
                persisted_snapshot={},
            )
        )
        db_session.commit()
    db_session.rollback()


def test_evidence_non_negative_check(db_session):
    from app.models import CaseEvidence

    case = make_case(db_session)
    with pytest.raises(IntegrityError):
        db_session.add(CaseEvidence(case_id=case.id, forecast_demand=-5))
        db_session.commit()
    db_session.rollback()
