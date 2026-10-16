"""Constraint-driven outcomes: budget, storage, MOQ, missing data, and no-purchase."""

from __future__ import annotations

from tests.conftest import create_case, new_idempotency_key, standard_evidence


def _evidence(case_id, client, **overrides):
    resp = client.put(f"/api/v1/cases/{case_id}/evidence", json=standard_evidence(**overrides))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _recommend(case_id, client):
    resp = client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _approve(case_id, client, version, qty, approver="buyer"):
    return client.post(
        f"/api/v1/cases/{case_id}/recommendations/{version}/decision",
        json={"decision": "approved", "approved_quantity": qty, "approver": approver},
    )


def test_budget_constraint_blocks_execution(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, budget_amount="5000.00")
    rec = _recommend(case_id, client)
    assert rec["decision"] == "accept"
    budget = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "budget")
    assert budget["status"] == "fail"
    assert "Purchase blocked" in rec["rationale"]
    assert "fully supported" not in rec["rationale"]

    approval = _approve(case_id, client, rec["version_number"], 800)
    assert approval.status_code == 201, approval.text
    ex = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": rec["version_number"],
            "approval_id": approval.json()["approval_id"],
        },
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert ex.status_code == 409
    assert ex.json()["error"]["code"] == "invalid_state"


def test_storage_constraint_blocks_execution(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, storage_capacity_units="500.0")
    rec = _recommend(case_id, client)
    storage = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "storage")
    assert storage["status"] == "fail"
    assert "Purchase blocked" in rec["rationale"]
    assert "fully supported" not in rec["rationale"]

    approval = _approve(case_id, client, rec["version_number"], 800)
    ex = client.post(
        f"/api/v1/cases/{case_id}/purchases",
        json={
            "recommendation_version_number": rec["version_number"],
            "approval_id": approval.json()["approval_id"],
        },
        headers={"Idempotency-Key": new_idempotency_key()},
    )
    assert ex.status_code == 409
    assert ex.json()["error"]["code"] == "invalid_state"


def test_moq_conflict_escalates_and_is_not_executable(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, supplier_offers=[
        {
            "supplier_name": "Distribuidora Norte",
            "unit_price": "12.50",
            "currency": "MXN",
            "moq": 1000,
            "lead_time_days": 3,
            "is_available": True,
        }
    ])
    rec = _recommend(case_id, client)
    assert rec["decision"] == "investigate"
    assert rec["recommended_quantity"] == 800  # never silently rounded up to MOQ
    assert rec["missing_information"]
    moq = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "moq")
    assert moq["status"] == "fail"

    case = client.get(f"/api/v1/cases/{case_id}").json()
    assert case["status"] == "needs_investigation"

    # A non-executable version cannot be approved.
    dec = _approve(case_id, client, rec["version_number"], 800)
    assert dec.status_code == 409
    assert dec.json()["error"]["code"] == "invalid_state"


def test_missing_evidence_investigates_with_unknown_checks(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, forecast_demand=None)
    rec = _recommend(case_id, client)
    assert rec["decision"] == "investigate"
    assert rec["recommended_quantity"] is None
    assert rec["missing_information"]
    # No constraint may claim to pass when its evidence is absent.
    assert not all(c["status"] == "pass" for c in rec["constraint_checks"])
    dq = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "data_quality")
    assert dq["status"] == "fail"
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "needs_investigation"


def test_zero_requirement_rejects(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, usable_inventory=2000)
    rec = _recommend(case_id, client)
    assert rec["decision"] == "reject"
    assert rec["baseline_required_quantity"] == 0
    assert rec["recommended_quantity"] is None
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "rejected"
    dec = _approve(case_id, client, rec["version_number"], 0)
    assert dec.status_code == 409


def test_missing_budget_yields_unknown_and_investigate(client):
    case_id = create_case(client)["case_id"]
    _evidence(case_id, client, budget_amount=None)
    rec = _recommend(case_id, client)
    assert rec["decision"] == "investigate"
    budget = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "budget")
    assert budget["status"] == "unknown"
