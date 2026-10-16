"""Independent validation outcomes and failure handling."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.mock import (
    MISMATCH_NONE,
    MISMATCH_QUANTITY,
    READ_MISSING,
    READ_OK,
    READ_UNAVAILABLE,
    get_mock_service,
)
from app.models import PurchaseOrder, ValidationResult
from tests.conftest import create_case, new_idempotency_key, standard_evidence


def _execute(client):
    case_id = create_case(client)["case_id"]
    client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    approval = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()
    po = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={"recommendation_version_number": 1, "approval_id": approval["approval_id"]},
        headers={"Idempotency-Key": new_idempotency_key()},
    ).json()
    return case_id, po["purchase_order_id"]


def test_persisted_mismatch_is_detected(client, db_session):
    get_mock_service().configure(mismatch=MISMATCH_QUANTITY)
    case_id, po_id = _execute(client)
    get_mock_service().configure(mismatch=MISMATCH_NONE)

    resp = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert any(m["field"] == "quantity" for m in body["mismatches"])
    assert body["persisted_snapshot"]["quantity"] == 500

    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validation_failed"
    detail = client.get(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}").json()
    assert detail["status"] == "validation_failed"
    # The persisted mismatch is recorded independently.
    persisted = db_session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po_id))
    ).scalar_one()
    assert persisted.status == "validation_failed"


def test_transport_failure_persists_nothing(client, db_session):
    case_id, po_id = _execute(client)
    get_mock_service().configure(read_mode=READ_UNAVAILABLE)

    resp = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mock_unavailable"
    assert db_session.execute(select(func.count()).select_from(ValidationResult)).scalar_one() == 0
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"


def test_inconclusive_then_revalidation_passes(client):
    case_id, po_id = _execute(client)
    get_mock_service().configure(read_mode=READ_MISSING)

    first = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert first.status_code == 200
    assert first.json()["status"] == "inconclusive"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"

    get_mock_service().configure(read_mode=READ_OK)
    second = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert second.status_code == 200
    assert second.json()["status"] == "passed"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validated"


def test_terminal_validation_is_idempotent(client, db_session):
    case_id, po_id = _execute(client)
    first = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    second = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["validation_result_id"] == second.json()["validation_result_id"]
    assert db_session.execute(select(func.count()).select_from(ValidationResult)).scalar_one() == 1


def test_validation_of_failed_po_rejected(client, db_session):
    case_id, po_id = _execute(client)
    # Simulate a mirrored PO later determined failed during reconciliation.
    po = db_session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po_id))
    ).scalar_one()
    po.status = "failed"
    db_session.commit()

    resp = client.post(f"/api/v1/cases/{case_id}/purchase-orders/{po_id}/validate", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_state"
    assert db_session.execute(select(func.count()).select_from(ValidationResult)).scalar_one() == 0
