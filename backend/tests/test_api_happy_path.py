"""End-to-end happy path over the real API and real PostgreSQL, including persisted-order
verification (not just the response body)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import PurchaseOrder, PurchaseOrderLineItem
from tests.conftest import create_case, new_idempotency_key, standard_evidence


def test_full_purchasing_flow(client, standard_case):
    case_id = standard_case["case_id"]
    assert standard_case["planning_horizon_end"] == "2026-10-22"
    assert standard_case["status"] == "draft"
    assert standard_case["current_recommendation_version"] is None

    ev = client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    assert ev.status_code == 200, ev.text
    assert ev.json()["budget_amount"] == "15000.00"
    assert ev.json()["incoming_orders"][0]["incoming_order_id"]

    rec = client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    assert rec.status_code == 201, rec.text
    r = rec.json()
    assert r["decision"] == "accept"
    assert r["version_number"] == 1
    assert r["baseline_required_quantity"] == 800
    assert r["recommended_quantity"] == 800
    assert r["total_cost"] == "10000.00"
    assert r["unit_price"] == "12.50"
    assert r["evidence"]["eligible_incoming_quantity"] == 100
    assert {c["constraint_type"] for c in r["constraint_checks"]} == {
        "supplier_availability",
        "lead_time",
        "moq",
        "budget",
        "storage",
        "data_quality",
    }
    assert all(c["status"] == "pass" for c in r["constraint_checks"])

    case = client.get(f"/api/v1/cases/{case_id}").json()
    assert case["status"] == "awaiting_approval"
    assert case["current_recommendation_version"] == 1

    dec = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={
            "decision": "approved",
            "approved_quantity": 800,
            "approver": "buyer@rappi.example",
            "note": "Approved for the seven-day horizon.",
        },
    )
    assert dec.status_code == 201, dec.text
    approval = dec.json()
    assert approval["total_cost"] == "10000.00"
    assert approval["approved_quantity"] == 800
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "approved"

    key = new_idempotency_key()
    ex = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={"recommendation_version_number": 1, "approval_id": approval["approval_id"]},
        headers={"Idempotency-Key": key},
    )
    assert ex.status_code == 201, ex.text
    po = ex.json()
    assert po["status"] == "created"
    assert po["quantity"] == 800
    assert po["unit_price"] == "12.50"
    assert po["total_cost"] == "10000.00"
    assert po["external_po_id"].startswith("MOCK-PO-")
    assert po["execution_attempt"]["outcome"] == "succeeded"
    assert po["validation_status"] == "pending"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"

    val = client.post(
        f"/api/v1/cases/{case_id}/purchase-orders/{po['purchase_order_id']}/validate",
        json={},
    )
    assert val.status_code == 200, val.text
    v = val.json()
    assert v["status"] == "passed"
    assert v["mismatches"] == []
    assert v["checked_fields"]["quantity"] == 800
    assert v["persisted_snapshot"]["quantity"] == 800

    detail = client.get(
        f"/api/v1/cases/{case_id}/purchase-orders/{po['purchase_order_id']}"
    ).json()
    assert detail["status"] == "validated"
    assert detail["validation_results"][-1]["status"] == "passed"
    assert detail["line_items"][0]["line_total"] == "10000.00"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validated"


def test_persisted_purchase_order_matches_approved_request(client, standard_case, db_session):
    case_id = standard_case["case_id"]
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

    row = db_session.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po["purchase_order_id"]))
    ).scalar_one()
    assert row.quantity == 800
    assert str(row.total_cost) == "10000.0000"
    assert row.idempotency_key
    line = db_session.execute(
        select(PurchaseOrderLineItem).where(PurchaseOrderLineItem.purchase_order_id == row.id)
    ).scalar_one()
    assert line.quantity == 800
    assert str(line.line_total) == "10000.0000"


def test_list_and_get_recommendation_versions(client, standard_case):
    case_id = standard_case["case_id"]
    client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    client.post(f"/api/v1/cases/{case_id}/recommendations", json={})

    listing = client.get(f"/api/v1/cases/{case_id}/recommendations").json()
    assert listing["current_recommendation_version"] == 1
    assert len(listing["recommendations"]) == 1

    one = client.get(f"/api/v1/cases/{case_id}/recommendations/1")
    assert one.status_code == 200
    assert one.json()["assumptions"]
    assert client.get(f"/api/v1/cases/{case_id}/recommendations/99").status_code == 404


def test_regeneration_supersedes_and_invalidates(client, standard_case):
    case_id = standard_case["case_id"]
    client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    old_approval = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()
    # New evidence supersedes version 1 and returns the case to draft.
    client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence(forecast_demand=1200))
    case = client.get(f"/api/v1/cases/{case_id}").json()
    assert case["status"] == "draft"
    assert case["current_recommendation_version"] is None

    v2 = client.post(f"/api/v1/cases/{case_id}/recommendations", json={}).json()
    assert v2["version_number"] == 2
    assert v2["baseline_required_quantity"] == 1000

    # The old approval can never authorize the new version.
    stale = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "recommendation_superseded"

    assert v2["initial_recommended_quantity"] == 800
    assert v2["recommended_quantity"] == 1000
    decision_url = f"/api/v1/cases/{case_id}/recommendations/2/decision"
    assert client.post(decision_url, json={
        "decision": "approved", "approved_quantity": 800, "approver": "buyer",
    }).json()["error"]["code"] == "approval_mismatch"
    body = {"recommendation_version_number": 2, "approval_id": old_approval["approval_id"]}
    assert client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                       headers={"Idempotency-Key": new_idempotency_key()}).status_code == 409
    fresh = client.post(decision_url, json={
        "decision": "approved", "approved_quantity": 1000, "approver": "buyer",
    }).json()
    stale = client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                        headers={"Idempotency-Key": new_idempotency_key()})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_approval"
    body["approval_id"] = fresh["approval_id"]
    order = client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                        headers={"Idempotency-Key": new_idempotency_key()})
    assert order.status_code == 201, order.text
    assert order.json()["quantity"] == 1000
    validation = client.post(
        f"/api/v1/cases/{case_id}/purchase-orders/{order.json()['purchase_order_id']}/validate", json={},
    )
    assert validation.json()["status"] == "passed"
    assert validation.json()["persisted_snapshot"]["quantity"] == 1000
