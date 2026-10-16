"""Reproducible offline demo against a running API. Creates fresh demo cases.

No reset, deletion, or migration is performed. Mock controls affect the local
service globally: run against an isolated demo instance, with no other buyers.
"""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

CASE = {
    "sku": "SKU-1234", "product_name": "Agua Embotellada 1L", "currency": "MXN",
    "volume_unit": "liter", "initial_recommended_quantity": 800,
    "planning_horizon_start": "2026-10-16",
}
EVIDENCE = {
    "usable_inventory": 200, "forecast_demand": 1000, "safety_stock": 100,
    "budget_amount": "15000.00", "storage_capacity_units": "1000.0",
    "storage_required_per_unit": "1.0", "evidence_as_of": "2026-10-16T00:00:00Z",
    "incoming_orders": [{"external_ref": "PO-IN-1", "quantity": 100,
                         "expected_arrival_date": "2026-10-18", "status": "confirmed"}],
    "supplier_offers": [{"supplier_name": "Distribuidora Norte", "unit_price": "12.50",
                         "currency": "MXN", "moq": 500, "lead_time_days": 3,
                         "is_available": True}],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", help="Optional JSON results file")
    parser.add_argument("--acceptance-only", action="store_true",
                        help="Check revised quantity and budget/storage/MOQ guards; no mock controls changed")
    args = parser.parse_args()
    root = args.base_url.rstrip("/")

    def request(method, path, body=None, key=None, expected=(200, 201)):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        req = Request(root + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=30) as response:
                status, payload = response.status, json.load(response)
        except HTTPError as error:
            status, payload = error.code, json.load(error)
        assert status in expected, (method, path, status, payload)
        return payload

    def ready(**overrides):
        case = request("POST", "/api/v1/cases", CASE)
        cid = case["case_id"]
        request("PUT", f"/api/v1/cases/{cid}/evidence", {**EVIDENCE, **overrides})
        rec = request("POST", f"/api/v1/cases/{cid}/recommendations", {})
        return cid, rec

    def approve(cid, version=1, quantity=800, expected=(200, 201)):
        return request("POST", f"/api/v1/cases/{cid}/recommendations/{version}/decision", {
            "decision": "approved", "approved_quantity": quantity, "approver": "demo-buyer",
            "note": "Explicit approval for this isolated mock-service demonstration.",
        }, expected=expected)

    def execute(cid, aid, key, version=1, expected=(200, 201)):
        return request("POST", f"/api/v1/cases/{cid}/purchases", {
            "recommendation_version_number": version, "approval_id": aid,
        }, key=key, expected=expected)

    results = {}
    if args.acceptance_only:
        cid, _ = ready()
        old = approve(cid)
        request("PUT", f"/api/v1/cases/{cid}/evidence", {**EVIDENCE, "forecast_demand": 1200})
        revised = request("POST", f"/api/v1/cases/{cid}/recommendations", {})
        assert revised["initial_recommended_quantity"] == 800
        assert revised["recommended_quantity"] == revised["baseline_required_quantity"] == 1000
        assert revised["version_number"] == 2
        before = execute(cid, old["approval_id"], str(uuid4()), version=2, expected=(409,))
        wrong = approve(cid, 2, 800, expected=(409,))
        assert wrong["error"]["code"] == "approval_mismatch"
        fresh = approve(cid, 2, 1000)
        stale = execute(cid, old["approval_id"], str(uuid4()), version=2, expected=(409,))
        assert stale["error"]["code"] == "stale_approval"
        po = execute(cid, fresh["approval_id"], str(uuid4()), version=2)
        val = request("POST", f"/api/v1/cases/{cid}/purchase-orders/{po['purchase_order_id']}/validate", {})
        assert val["status"] == "passed" and val["persisted_snapshot"]["quantity"] == 1000
        results["revised_quantity"] = {
            "case_id": cid, "initial_suggestion": 800, "required_quantity": 1000,
            "version": 2, "before_fresh_approval": before["error"]["code"],
            "wrong_quantity_approval": wrong["error"]["code"],
            "old_approval_execution": stale["error"]["code"],
            "fresh_approved_cost": fresh["total_cost"], "validation": val["status"],
        }
        offers = [{**EVIDENCE["supplier_offers"][0], "moq": 1000}]
        for name, overrides in (
            ("budget", {"budget_amount": "5000.00"}),
            ("storage", {"storage_capacity_units": "500.0"}),
            ("moq", {"supplier_offers": offers}),
        ):
            cid, rec = ready(**overrides)
            check = next(c for c in rec["constraint_checks"] if c["constraint_type"] == name)
            assert check["status"] == "fail" and rec["recommended_quantity"] == 800
            assert "Purchase blocked" in rec["rationale"]
            assert "fully supported" not in rec["rationale"]
            state = request("GET", f"/api/v1/cases/{cid}")["status"]
            if name == "moq":
                approval = approve(cid, expected=(409,))
                assert rec["decision"] == "investigate" and state == "needs_investigation"
                assert approval["error"]["code"] == "invalid_state"
                execution_code = "not_attempted_approval_blocked"
            else:
                approval = approve(cid)
                blocked = execute(cid, approval["approval_id"], str(uuid4()), expected=(409,))
                execution_code = blocked["error"]["code"]
            results[name] = {"case_id": cid, "decision": rec["decision"], "case_status": state,
                             "check": check, "rationale": rec["rationale"],
                             "execution": execution_code, "quantity": rec["recommended_quantity"]}
        rendered = json.dumps(results, indent=2)
        print(rendered)
        if args.output:
            Path(args.output).write_text(rendered + "\n")
        return
    request("POST", "/mock/v1/_control", {"create_mode": "ok", "read_mode": "ok", "mismatch": "none"})
    try:
        cid, rec = ready()
        assert rec["baseline_required_quantity"] == rec["recommended_quantity"] == 800
        check = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "data_quality")
        assert check["details"]["agent_workflow"]["engine"] == "langgraph"
        key = str(uuid4())
        approval = approve(cid)
        po = execute(cid, approval["approval_id"], key)
        duplicate = execute(cid, approval["approval_id"], key)
        assert duplicate["purchase_order_id"] == po["purchase_order_id"]
        persisted = request("GET", f"/mock/v1/purchase-orders/by-key/{key}")
        assert persisted["quantity"] == 800
        validation = request("POST", f"/api/v1/cases/{cid}/purchase-orders/{po['purchase_order_id']}/validate", {})
        assert validation["status"] == "passed"
        results["happy_and_duplicate"] = {"case_id": cid, "key": key,
            "purchase_order_id": po["purchase_order_id"], "validation": "passed",
            "quantity": 800, "total_cost": po["total_cost"], "duplicate_same_order": True}

        cid, _ = ready()
        approval = approve(cid)
        request("POST", "/mock/v1/_control", {"mismatch": "quantity"})
        po = execute(cid, approval["approval_id"], str(uuid4()))
        request("POST", "/mock/v1/_control", {"mismatch": "none"})
        validation = request("POST", f"/api/v1/cases/{cid}/purchase-orders/{po['purchase_order_id']}/validate", {})
        assert validation["status"] == "failed"
        state = request("GET", f"/api/v1/cases/{cid}")["status"]
        assert state == "validation_failed"
        blocked = execute(cid, approval["approval_id"], str(uuid4()), expected=(409,))
        results["persisted_mismatch"] = {"case_id": cid, "status": state,
            "mismatches": validation["mismatches"], "repurchase_blocked": blocked["error"]["code"]}

        cid, _ = ready()
        missing = execute(cid, str(uuid4()), str(uuid4()), expected=(409,))
        old = approve(cid)
        rec2 = request("POST", f"/api/v1/cases/{cid}/recommendations", {})
        assert rec2["version_number"] == 2
        approve(cid, 2)
        stale = execute(cid, old["approval_id"], str(uuid4()), version=2, expected=(409,))
        assert stale["error"]["code"] == "stale_approval"
        results["approval_guards"] = {"case_id": cid, "missing": missing["error"]["code"],
                                      "stale": stale["error"]["code"]}

        cid, rec = ready(budget_amount=None)
        assert rec["decision"] == "investigate" and rec["recommended_quantity"] is None
        results["missing_evidence"] = {"case_id": cid, "decision": rec["decision"]}
    finally:
        request("POST", "/mock/v1/_control", {"create_mode": "ok", "read_mode": "ok", "mismatch": "none"})
    rendered = json.dumps(results, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n")


if __name__ == "__main__":
    main()
