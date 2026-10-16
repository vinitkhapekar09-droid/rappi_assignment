"""Approval, version, and state guards."""

from __future__ import annotations

from tests.conftest import create_case, new_idempotency_key, standard_evidence


def _setup_ready(client, **evidence_overrides):
    case_id = create_case(client)["case_id"]
    client.put(
        f"/api/v1/cases/{case_id}/evidence", json=standard_evidence(**evidence_overrides)
    )
    rec = client.post(f"/api/v1/cases/{case_id}/recommendations", json={}).json()
    return case_id, rec


def test_approval_quantity_mismatch_rejected(client):
    case_id, rec = _setup_ready(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/recommendations/{rec['version_number']}/decision",
        json={"decision": "approved", "approved_quantity": 500, "approver": "buyer"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "approval_mismatch"


def test_execution_requires_approval(client):
    case_id, rec = _setup_ready(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": rec["version_number"],
            "approval_id": "44444444-4444-4444-4444-444444444444",
        },
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] in ("invalid_state", "stale_approval")


def test_stale_approval_cannot_authorize_newer_version(client):
    case_id, rec1 = _setup_ready(client)
    a1 = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()

    # Generate version 2 from the approved state (supersedes and invalidates version 1).
    rec2 = client.post(f"/api/v1/cases/{case_id}/recommendations", json={}).json()
    assert rec2["version_number"] == 2
    a2 = client.post(
        f"/api/v1/cases/{case_id}/recommendations/2/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()
    assert a2["approval_id"] != a1["approval_id"]

    # Case is approved for version 2, but the supplied approval belongs to version 1.
    resp = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": 2,
            "approval_id": a1["approval_id"],
        },
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "stale_approval"


def test_executing_superseded_version_rejected(client):
    case_id, _ = _setup_ready(client)
    client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    )
    client.post(f"/api/v1/cases/{case_id}/recommendations", json={})  # v2, case awaiting_approval
    resp = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": 1,
            "approval_id": "44444444-4444-4444-4444-444444444444",
        },
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] in ("recommendation_superseded", "invalid_state")


def test_rejected_decision_sets_case_rejected(client):
    case_id, rec = _setup_ready(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/recommendations/{rec['version_number']}/decision",
        json={"decision": "rejected", "approver": "buyer", "note": "stale demand"},
    )
    assert resp.status_code == 201
    assert resp.json()["decision"] == "rejected"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "rejected"


def test_evidence_locked_during_validation(client):
    case_id, rec = _setup_ready(client)
    approval = client.post(
        f"/api/v1/cases/{case_id}/recommendations/1/decision",
        json={"decision": "approved", "approved_quantity": 800, "approver": "buyer"},
    ).json()
    client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={"recommendation_version_number": 1, "approval_id": approval["approval_id"]},
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    # Case is now `validating`; evidence and regeneration are locked.
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"
    ev = client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence())
    assert ev.status_code == 409
    assert ev.json()["error"]["code"] == "invalid_state"
    rec2 = client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    assert rec2.status_code == 409


def test_repeated_approval_is_idempotent(client):
    case_id, rec = _setup_ready(client)
    body = {"decision": "approved", "approved_quantity": 800, "approver": "buyer"}
    first = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json=body)
    second = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["approval_id"] == second.json()["approval_id"]


def test_invalid_evidence_currency_rejected(client):
    case_id = create_case(client)["case_id"]
    bad = standard_evidence()
    bad["supplier_offers"][0]["currency"] = "USD"
    resp = client.put(f"/api/v1/cases/{case_id}/evidence", json=bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
