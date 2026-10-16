"""Purchase execution: idempotency, approval/version guards, mock call, mirroring.

Implements `docs/API_CONTRACT.md` §2.2/§5.8 and `docs/DATABASE_SCHEMA.md` §5.4. All correctness
properties rely on the database: an atomic idempotency claim, the `UNIQUE
(idempotency_key)` backstop, composite FKs, and row locks. A successful HTTP response is
never treated as proof of purchase success.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import (
    execution_failed,
    execution_in_progress,
    execution_uncertain,
    idempotency_conflict,
    invalid_state,
    not_found,
    recommendation_superseded,
    stale_approval,
)
from app.mock import MockFailure, MockUnavailable, MockUncertain, get_mock_service
from app.models import (
    Approval,
    ConstraintCheck,
    ExecutionAttempt,
    IdempotencyKey,
    PurchaseOrder,
    PurchaseOrderLineItem,
    PurchasingCase,
    RecommendationVersion,
    ValidationResult,
)
from app.schemas import PurchaseRequest
from app.services import response as R
from app.services.common import load_constraint_checks, lock_case, utcnow

ACTION = "execute_purchase"


def request_fingerprint(case_id: uuid.UUID, version_number: int, approval_id: uuid.UUID) -> str:
    payload = {
        "recommendation_version_number": version_number,
        "approval_id": str(approval_id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raw = f"POST|/api/v1/cases/{case_id}/purchases|{canonical}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lease_expired(ikey: IdempotencyKey, lease_seconds: int) -> bool:
    if ikey.state != "in_progress":
        return False
    now = utcnow()
    updated = ikey.updated_at
    if updated.tzinfo is None:
        from datetime import timezone

        updated = updated.replace(tzinfo=timezone.utc)
    return updated < now - timedelta(seconds=lease_seconds)


def _resolve(
    session: Session,
    key: str,
    case_id: uuid.UUID,
    fingerprint: str,
) -> tuple[str, IdempotencyKey | None]:
    """Return ('owner'|'replay'|'reconcile', ikey)."""
    lease = get_settings().idempotency_lease_seconds
    ikey = session.get(IdempotencyKey, key)
    if ikey is None:
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                key=key,
                case_id=case_id,
                action=ACTION,
                request_fingerprint=fingerprint,
                state="in_progress",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(IdempotencyKey.key)
        )
        inserted = session.execute(stmt).scalar_one_or_none()
        session.commit()
        if inserted is not None:
            return "owner", None
        ikey = session.get(IdempotencyKey, key)

    if ikey is None:  # pragma: no cover - defensive
        return "owner", None

    if ikey.case_id != case_id:
        raise idempotency_conflict(
            "This idempotency key was used for a different case.",
            key=key,
        )
    if ikey.request_fingerprint != fingerprint:
        raise idempotency_conflict(
            "This idempotency key was used with a different request body.",
            key=key,
        )
    if ikey.state in ("completed", "failed"):
        return "replay", ikey
    if ikey.state == "uncertain":
        return "reconcile", ikey
    if _lease_expired(ikey, lease):
        return "reconcile", ikey
    raise execution_in_progress(
        "A purchase with this idempotency key is already in progress.",
        key=key,
    )


def _conditional_claim(session: Session, key: str) -> bool:
    lease = get_settings().idempotency_lease_seconds
    now = utcnow()
    stmt = (
        update(IdempotencyKey)
        .where(
            IdempotencyKey.key == key,
            IdempotencyKey.action == ACTION,
            or_(
                IdempotencyKey.state == "uncertain",
                and_(
                    IdempotencyKey.state == "in_progress",
                    IdempotencyKey.updated_at < now - timedelta(seconds=lease),
                ),
            ),
        )
        .values(state="in_progress", updated_at=now)
        .returning(IdempotencyKey.key)
    )
    claimed = session.execute(stmt).scalar_one_or_none()
    session.commit()
    return claimed is not None


def _next_attempt_number(session: Session, key: str) -> int:
    return (
        session.execute(
            select(func.coalesce(func.max(ExecutionAttempt.attempt_number), 0)).where(
                ExecutionAttempt.idempotency_key == key
            )
        ).scalar_one()
        + 1
    )


def _release_key(session: Session, key: str) -> None:
    """Release a claim when no create was attempted (pre-create guard failed)."""
    session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.key == key,
            IdempotencyKey.state == "in_progress",
            IdempotencyKey.purchase_order_id.is_(None),
        )
    )
    session.commit()


def _verify_and_mark_executing(
    session: Session,
    case_id: uuid.UUID,
    version_number: int,
    approval_id: uuid.UUID,
    *,
    recovering: bool = False,
) -> tuple[PurchasingCase, RecommendationVersion, Approval]:
    case = lock_case(session, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")
    if case.status != "approved" and not (recovering and case.status == "executing"):
        raise invalid_state(
            f"Case is '{case.status}'; only an approved case can be executed.",
            case_status=case.status,
        )
    version = session.execute(
        select(RecommendationVersion).where(
            RecommendationVersion.case_id == case_id,
            RecommendationVersion.version_number == version_number,
        )
    ).scalar_one_or_none()
    if (
        version is None
        or case.current_recommendation_version_id != version.id
        or version.status != "ready"
    ):
        raise recommendation_superseded(
            "The requested recommendation version is not the current one.",
            requested_version=version_number,
        )

    approval = session.get(Approval, approval_id)
    if (
        approval is None
        or approval.case_id != case_id
        or approval.recommendation_version_id != version.id
        or approval.decision != "approved"
        or approval.invalidated_at is not None
    ):
        raise stale_approval(
            "No active approval matches this recommendation version.",
            approval_id=str(approval_id),
        )
    if (
        approval.approved_quantity != version.recommended_quantity
        or approval.supplier_offer_id != version.chosen_supplier_offer_id
        or approval.supplier_name != version.supplier_name
        or approval.unit_price != version.unit_price
        or approval.currency != version.currency
        or approval.total_cost != version.total_cost
    ):
        raise stale_approval(
            "The approval details do not match the recommendation version.",
        )

    if version.decision not in ("accept", "modify"):
        raise invalid_state(f"Version {version_number} is not executable.")

    checks = load_constraint_checks(session, version.id)
    required = {"supplier_availability", "lead_time", "moq", "budget", "storage", "data_quality"}
    present = {c.constraint_type for c in checks}
    bad = [c.constraint_type for c in checks if c.status in ("fail", "unknown")]
    if required - present or bad:
        raise invalid_state(
            "Not all required constraints pass; execution is blocked.",
            failing=sorted(bad),
            missing=sorted(required - present),
        )

    case.status = "executing"
    case.updated_at = utcnow()
    return case, version, approval


def _attempt_payload(
    case: PurchasingCase,
    version: RecommendationVersion,
    approval: Approval,
) -> dict:
    return {
        "sku": case.sku,
        "quantity": approval.approved_quantity,
        "unit_price": f"{approval.unit_price:f}",
        "currency": approval.currency,
        "supplier_name": approval.supplier_name,
    }


def _store_key_state(
    session: Session,
    key: str,
    *,
    state: str,
    response_status: int | None = None,
    response_body: dict | None = None,
    purchase_order_id: uuid.UUID | None = None,
) -> None:
    ikey = session.get(IdempotencyKey, key)
    if ikey is None:
        return
    ikey.state = state
    if response_status is not None:
        ikey.response_status = response_status
    if response_body is not None:
        ikey.response_body = response_body
    if purchase_order_id is not None:
        ikey.purchase_order_id = purchase_order_id
    ikey.updated_at = utcnow()


def _record_attempt(
    session: Session,
    *,
    case_id: uuid.UUID,
    key: str,
    attempt_number: int,
    payload: dict,
    outcome: str,
    http_status: int | None,
    error_code: str | None = None,
    error_message: str | None = None,
    external_po_id: str | None = None,
    purchase_order_id: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
) -> ExecutionAttempt:
    attempt = ExecutionAttempt(
        case_id=case_id,
        purchase_order_id=purchase_order_id,
        approval_id=approval_id,
        idempotency_key=key,
        attempt_number=attempt_number,
        request_payload=payload,
        http_status=http_status,
        outcome=outcome,
        error_code=error_code,
        error_message=error_message,
        external_po_id=external_po_id,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _build_response(session: Session, po: PurchaseOrder) -> dict:
    version = session.get(RecommendationVersion, po.recommendation_version_id)
    attempts = list(
        session.execute(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.purchase_order_id == po.id)
            .order_by(ExecutionAttempt.attempt_number)
        )
        .scalars()
        .all()
    )
    validations = list(
        session.execute(
            select(ValidationResult)
            .where(ValidationResult.purchase_order_id == po.id)
            .order_by(ValidationResult.created_at)
        )
        .scalars()
        .all()
    )
    return R.purchase_order_dict(
        po,
        version,
        attempts,
        validations,
        validation_status=R.validation_status_for(po, validations),
    )


def _mirror_and_complete(
    session: Session,
    *,
    case: PurchasingCase,
    version: RecommendationVersion,
    approval: Approval,
    mock_po,
    key: str,
    attempt_number: int,
    payload: dict,
    reconciled: bool,
) -> tuple[dict, int]:
    now = utcnow()
    existing = session.execute(
        select(PurchaseOrder).where(PurchaseOrder.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is None:
        mirror = PurchaseOrder(
            case_id=case.id,
            recommendation_version_id=version.id,
            approval_id=approval.id,
            supplier_offer_id=approval.supplier_offer_id,
            supplier_name=approval.supplier_name,
            sku=case.sku,
            quantity=approval.approved_quantity,
            unit_price=approval.unit_price,
            total_cost=approval.total_cost,
            currency=approval.currency,
            status="created",
            idempotency_key=key,
            external_po_id=mock_po.external_po_id,
            persisted_snapshot=mock_po.snapshot(),
        )
        session.add(mirror)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            mirror = session.execute(
                select(PurchaseOrder).where(PurchaseOrder.idempotency_key == key)
            ).scalar_one()
        else:
            session.add(
                PurchaseOrderLineItem(
                    purchase_order_id=mirror.id,
                    sku=case.sku,
                    quantity=approval.approved_quantity,
                    unit_price=approval.unit_price,
                    line_total=approval.total_cost,
                )
            )
            session.flush()
    else:
        mirror = existing
        mirror.external_po_id = mock_po.external_po_id
        mirror.persisted_snapshot = mock_po.snapshot()

    attempt_number = _next_attempt_number(session, key)
    _record_attempt(
        session,
        case_id=case.id,
        key=key,
        attempt_number=attempt_number,
        payload=payload if not reconciled else {**payload, "reconciled": True},
        outcome="succeeded",
        http_status=200 if reconciled else 201,
        external_po_id=mock_po.external_po_id,
        purchase_order_id=mirror.id,
        approval_id=approval.id,
    )
    case.status = "validating"
    case.updated_at = now
    session.flush()

    response_body = _build_response(session, mirror)
    _store_key_state(
        session,
        key,
        state="completed",
        response_status=200 if reconciled else 201,
        response_body=response_body,
        purchase_order_id=mirror.id,
    )
    session.commit()
    return response_body, 200 if reconciled else 201


def _create_and_mirror(
    session: Session,
    *,
    case_id: uuid.UUID,
    version_number: int,
    approval_id: uuid.UUID,
    key: str,
    fingerprint: str,
    recovering: bool = False,
) -> tuple[dict, int]:
    try:
        case, version, approval = _verify_and_mark_executing(
            session, case_id, version_number, approval_id, recovering=recovering
        )
        session.commit()
    except Exception:
        session.rollback()
        _release_key(session, key)
        raise

    payload = _attempt_payload(case, version, approval)
    mock = get_mock_service()

    try:
        mock_po = mock.create(idempotency_key=key, payload=payload)
    except MockFailure as exc:
        attempt_number = _next_attempt_number(session, key)
        _record_attempt(
            session,
            case_id=case_id,
            key=key,
            attempt_number=attempt_number,
            payload=payload,
            outcome="failed",
            http_status=502,
            error_code="execution_failed",
            error_message=str(exc),
            approval_id=approval.id,
        )
        body = execution_failed("The mock purchasing service confirmed a failure.").to_body()
        _store_key_state(session, key, state="failed", response_status=502, response_body=body)
        case.status = "execution_failed"
        case.updated_at = utcnow()
        session.commit()
        raise execution_failed("The mock purchasing service confirmed a failure.")
    except (MockUncertain, MockUnavailable) as exc:
        attempt_number = _next_attempt_number(session, key)
        _record_attempt(
            session,
            case_id=case_id,
            key=key,
            attempt_number=attempt_number,
            payload=payload,
            outcome="uncertain",
            http_status=503,
            error_code="execution_uncertain",
            error_message=str(exc),
            approval_id=approval.id,
        )
        _store_key_state(
            session,
            key,
            state="uncertain",
            response_status=503,
            response_body=execution_uncertain(str(exc)).to_body(),
        )
        session.commit()
        raise execution_uncertain(
            "The purchase outcome could not be confirmed; reconcile by idempotency key."
        )

    attempt_number = _next_attempt_number(session, key)
    return _mirror_and_complete(
        session,
        case=case,
        version=version,
        approval=approval,
        mock_po=mock_po,
        key=key,
        attempt_number=attempt_number,
        payload=payload,
        reconciled=False,
    )


def _reconcile(
    session: Session,
    *,
    case_id: uuid.UUID,
    version_number: int,
    approval_id: uuid.UUID,
    key: str,
    fingerprint: str,
) -> tuple[dict, int]:
    mock = get_mock_service()
    try:
        mock_po = mock.get_by_key(key)
    except MockUnavailable as exc:
        _store_key_state(
            session,
            key,
            state="uncertain",
            response_status=503,
            response_body=execution_uncertain(str(exc)).to_body(),
        )
        session.commit()
        raise execution_uncertain(
            "The mock purchasing service is unreachable; reconcile later.",
            key=key,
        )

    if mock_po is not None:
        # Mirror the already-persisted PO; no new create.
        po = session.execute(
            select(PurchaseOrder).where(PurchaseOrder.idempotency_key == key)
        ).scalar_one_or_none()
        if po is not None:
            version = session.get(RecommendationVersion, po.recommendation_version_id)
            approval = session.get(Approval, po.approval_id)
            case = session.get(PurchasingCase, case_id)
        else:
            # We may be recovering without prior guards; reload under lock.
            case = lock_case(session, case_id)
            version = session.execute(
                select(RecommendationVersion).where(
                    RecommendationVersion.case_id == case_id,
                    RecommendationVersion.version_number == version_number,
                )
            ).scalar_one_or_none()
            approval = session.get(Approval, approval_id)
        if case is None or version is None or approval is None:
            raise not_found("Cannot reconcile: referenced records are missing.")
        return _mirror_and_complete(
            session,
            case=case,
            version=version,
            approval=approval,
            mock_po=mock_po,
            key=key,
            attempt_number=_next_attempt_number(session, key),
            payload=_attempt_payload(case, version, approval),
            reconciled=True,
        )

    # Mock confirms no PO exists: the create is safe to (re)attempt, but only the atomic
    # claim owner may proceed.
    if not _conditional_claim(session, key):
        raise execution_in_progress(
            "Another request is currently creating this purchase.", key=key
        )
    return _create_and_mirror(
        session,
        case_id=case_id,
        version_number=version_number,
        approval_id=approval_id,
        key=key,
        fingerprint=fingerprint,
        recovering=True,
    )


def execute_purchase(
    session: Session,
    case_id: uuid.UUID,
    req: PurchaseRequest,
    idempotency_key: str | None,
) -> tuple[dict, int]:
    if not idempotency_key:
        from app.errors import AppError

        raise AppError("validation_error", 422, "Idempotency-Key header is required.")

    case = session.get(PurchasingCase, case_id)
    if case is None:
        raise not_found(f"Case {case_id} does not exist.")

    fingerprint = request_fingerprint(case_id, req.recommendation_version_number, req.approval_id)
    kind, ikey = _resolve(session, idempotency_key, case_id, fingerprint)

    if kind == "replay":
        assert ikey is not None
        body = ikey.response_body
        if body is None:  # pragma: no cover - defensive
            raise execution_uncertain("Stored idempotent result is unavailable.")
        return body, 200

    if kind == "reconcile":
        return _reconcile(
            session,
            case_id=case_id,
            version_number=req.recommendation_version_number,
            approval_id=req.approval_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )

    return _create_and_mirror(
        session,
        case_id=case_id,
        version_number=req.recommendation_version_number,
        approval_id=req.approval_id,
        key=idempotency_key,
        fingerprint=fingerprint,
    )


def get_purchase_order(
    session: Session, case_id: uuid.UUID, purchase_order_id: uuid.UUID
) -> dict:
    po = session.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.id == purchase_order_id,
            PurchaseOrder.case_id == case_id,
        )
    ).scalar_one_or_none()
    if po is None:
        raise not_found(f"Purchase order {purchase_order_id} does not exist.")
    version = session.get(RecommendationVersion, po.recommendation_version_id)
    line_items = list(
        session.execute(
            select(PurchaseOrderLineItem).where(
                PurchaseOrderLineItem.purchase_order_id == po.id
            )
        )
        .scalars()
        .all()
    )
    attempts = list(
        session.execute(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.purchase_order_id == po.id)
            .order_by(ExecutionAttempt.attempt_number)
        )
        .scalars()
        .all()
    )
    validations = list(
        session.execute(
            select(ValidationResult)
            .where(ValidationResult.purchase_order_id == po.id)
            .order_by(ValidationResult.created_at)
        )
        .scalars()
        .all()
    )
    return R.purchase_order_detail_dict(po, version, line_items, attempts, validations)
