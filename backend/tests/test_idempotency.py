"""Idempotency, duplicate-execution prevention, and safe recovery."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import func, select

from app.mock import CREATE_FAILURE, CREATE_OK, CREATE_UNCERTAIN, get_mock_service
from app.models import IdempotencyKey, PurchaseOrder
from app.services.common import utcnow
from app.services.purchases import request_fingerprint
from tests.conftest import create_case, new_idempotency_key, standard_evidence


def _ready_to_execute(client):
    case_id = create_case(client)["case_id"]
    client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    approval = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()
    return case_id, approval["approval_id"]


def _execute(client, case_id, approval_id, key):
    return client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={"recommendation_version_number": 1, "approval_id": approval_id},
        headers={"Idempotency-Key": key},
    )


def test_duplicate_execution_creates_one_purchase_order(client, db_session):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()

    first = _execute(client, case_id, approval_id, key)
    assert first.status_code == 201, first.text
    second = _execute(client, case_id, approval_id, key)
    assert second.status_code == 200, second.text

    assert first.json()["purchase_order_id"] == second.json()["purchase_order_id"]
    count = db_session.execute(select(func.count()).select_from(PurchaseOrder)).scalar_one()
    assert count == 1


def test_same_key_different_body_conflicts(client):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()
    first = _execute(client, case_id, approval_id, key)
    assert first.status_code == 201

    # Same key, different approval id -> different fingerprint.
    resp = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": 1,
            "approval_id": "44444444-4444-4444-4444-444444444444",
        },
        headers={"Idempotency-Key": key},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "idempotency_conflict"


def test_concurrent_in_progress_returns_conflict(client, db_session):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()
    fp = request_fingerprint(uuid.UUID(case_id), 1, uuid.UUID(approval_id))
    db_session.add(
        IdempotencyKey(
            key=key,
            case_id=uuid.UUID(case_id),
            action="execute_purchase",
            request_fingerprint=fp,
            state="in_progress",
            updated_at=utcnow(),
        )
    )
    db_session.commit()

    resp = _execute(client, case_id, approval_id, key)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "execution_in_progress"


def test_uncertain_then_reconcile_finds_existing_po(client, db_session):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()

    get_mock_service().configure(create_mode=CREATE_UNCERTAIN)
    first = _execute(client, case_id, approval_id, key)
    assert first.status_code == 503, first.text
    assert first.json()["error"]["code"] == "execution_uncertain"

    # The mock persisted the PO despite the uncertain outcome; reconcile by key.
    get_mock_service().configure(create_mode=CREATE_OK)
    second = _execute(client, case_id, approval_id, key)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "created"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"

    count = db_session.execute(select(func.count()).select_from(PurchaseOrder)).scalar_one()
    assert count == 1


def test_confirmed_failure_replays_and_creates_no_po(client, db_session):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()

    get_mock_service().configure(create_mode=CREATE_FAILURE)
    first = _execute(client, case_id, approval_id, key)
    assert first.status_code == 502
    assert first.json()["error"]["code"] == "execution_failed"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "execution_failed"

    get_mock_service().configure(create_mode=CREATE_OK)
    second = _execute(client, case_id, approval_id, key)
    assert second.status_code == 200
    assert second.json()["error"]["code"] == "execution_failed"

    assert db_session.execute(select(func.count()).select_from(PurchaseOrder)).scalar_one() == 0


def test_expired_in_progress_reconciles_to_existing_po(client, db_session):
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()

    fp = request_fingerprint(uuid.UUID(case_id), 1, uuid.UUID(approval_id))
    # Simulate a crashed worker: an expired in_progress claim and a PO persisted by the mock.
    get_mock_service().create(
        idempotency_key=key,
        payload={
            "sku": "SKU-1234",
            "quantity": 800,
            "unit_price": "12.5000",
            "currency": "MXN",
            "supplier_name": "Distribuidora Norte",
        },
    )
    db_session.add(
        IdempotencyKey(
            key=key,
            case_id=uuid.UUID(case_id),
            action="execute_purchase",
            request_fingerprint=fp,
            state="in_progress",
            updated_at=utcnow() - timedelta(seconds=300),
        )
    )
    db_session.commit()

    resp = _execute(client, case_id, approval_id, key)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "created"
    assert db_session.execute(select(func.count()).select_from(PurchaseOrder)).scalar_one() == 1


def test_concurrent_duplicate_requests_create_one_po(client, db_session):
    """Two simultaneous identical requests must not create two purchase orders."""
    case_id, approval_id = _ready_to_execute(client)
    key = new_idempotency_key()
    barrier = threading.Barrier(2)

    def call():
        barrier.wait()
        return _execute(client, case_id, approval_id, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        responses = [f.result() for f in futures]

    codes = sorted(r.status_code for r in responses)
    # One request wins the create (201) or both replay a completed result (200);
    # the loser may also see 409 execution_in_progress. Never two creates.
    assert codes[0] in (200, 201, 409)
    assert 500 not in codes
    for r in responses:
        assert r.status_code in (200, 201, 409)

    count = db_session.execute(select(func.count()).select_from(PurchaseOrder)).scalar_one()
    assert count == 1

    winners = [r.json()["purchase_order_id"] for r in responses if r.status_code in (200, 201)]
    assert len(set(winners)) == 1
