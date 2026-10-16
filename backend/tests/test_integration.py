"""Integration regressions at the persistence and orchestration boundaries."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.mock.service import MockPurchasingService
from app.models import Approval, RecommendationVersion
from tests.conftest import create_case, new_idempotency_key, standard_evidence


def ready(client, **overrides):
    case_id = create_case(client)["case_id"]
    assert client.put(f"/api/v1/cases/{case_id}/evidence",
                      json=standard_evidence(**overrides)).status_code == 200
    response = client.post(f"/api/v1/cases/{case_id}/recommendations", json={})
    assert response.status_code == 201, response.text
    return case_id, response.json()


def approve(client, case_id):
    response = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json={
        "decision": "approved", "approved_quantity": 800, "approver": "buyer",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_live_recommendation_persists_langgraph_trace(client, db_session):
    case_id, rec = ready(client)
    version = db_session.execute(select(RecommendationVersion).where(
        RecommendationVersion.case_id == uuid.UUID(case_id))).scalar_one()
    assert version.recommended_quantity == 800
    assert version.total_cost == 10000
    check = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "data_quality")
    trace = check["details"]["agent_workflow"]
    assert trace["engine"] == "langgraph"
    assert trace["inference"]["mode"] == "offline_fallback"
    assert trace["inference"]["online_succeeded"] is False
    assert trace["interpretation"]["summary"]
    assert [e["event"] for e in trace["events"]] == [
        "case_loaded", "evidence_loaded", "investigation_complete",
        "explanation_generated", "recommendation_ready",
    ]


def test_mocked_online_metadata_is_persisted_without_credentials(client, monkeypatch, db_session):
    from types import SimpleNamespace
    from app.agent.models import RecommendationExplanation
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-only")
    monkeypatch.setattr("app.agent.adapters.backend.build_llm", lambda config: SimpleNamespace(
        name="mocked-online", provider="provider.example", model="test-model-resolved", is_offline=False,
        generate_explanation=lambda context: RecommendationExplanation(
            summary="The evidence supports the calculated requirement.",
            trade_offs=["Retain budget headroom."], confidence="high",
        ),
    ))
    case_id, rec = ready(client)
    trace = next(c for c in rec["constraint_checks"] if c["constraint_type"] == "data_quality")["details"]["agent_workflow"]
    assert rec["generated_by"] == "agent"
    assert trace["inference"]["online_succeeded"] is True
    assert trace["inference"]["model"] == "test-model-resolved"
    assert trace["interpretation"]["trade_offs"] == ["Retain budget headroom."]
    assert "test-only" not in str(trace)
    row = db_session.execute(select(RecommendationVersion).where(
        RecommendationVersion.case_id == uuid.UUID(case_id))).scalar_one()
    assert row.recommended_quantity == 800 and row.total_cost == 10000


def test_graph_preserves_unknown_incoming_when_inventory_missing(client):
    case_id, rec = ready(client, usable_inventory=None)
    assert rec["evidence"]["eligible_incoming_quantity"] is None
    assert rec["decision"] == "investigate"
    assert rec["recommended_quantity"] is None
    response = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json={
        "decision": "approved", "approved_quantity": 800, "approver": "buyer",
    })
    assert response.status_code == 409


def test_buyer_cannot_reopen_case_after_purchase(client):
    case_id, _ = ready(client)
    approval = approve(client, case_id)
    assert client.post(f"/api/v1/cases/{case_id}/purchases", json={
        "recommendation_version_number": 1, "approval_id": approval["approval_id"],
    }, headers={"Idempotency-Key": new_idempotency_key()}).status_code == 201
    response = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json={
        "decision": "rejected", "approver": "buyer",
    })
    assert response.status_code == 409
    assert client.get(f"/api/v1/cases/{case_id}").json()["status"] == "validating"


def test_rejection_invalidates_previous_approval(client, db_session):
    case_id, _ = ready(client)
    approval = approve(client, case_id)
    response = client.post(f"/api/v1/cases/{case_id}/recommendations/1/decision", json={
        "decision": "rejected", "approver": "buyer",
    })
    assert response.status_code == 201
    assert db_session.get(Approval, uuid.UUID(approval["approval_id"])).invalidated_at is not None


def test_tampered_approval_currency_blocks_purchase(client, db_session):
    case_id, _ = ready(client)
    approval = approve(client, case_id)
    row = db_session.get(Approval, uuid.UUID(approval["approval_id"]))
    row.currency = "USD"
    db_session.commit()
    response = client.post(f"/api/v1/cases/{case_id}/purchases", json={
        "recommendation_version_number": 1, "approval_id": approval["approval_id"],
    }, headers={"Idempotency-Key": new_idempotency_key()})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_approval"


def test_evidence_replacement_preserves_immutable_supplier_terms(client, db_session):
    case_id, original = ready(client)
    approval = approve(client, case_id)
    changed = standard_evidence()
    changed["supplier_offers"][0]["unit_price"] = "14.00"
    assert client.put(f"/api/v1/cases/{case_id}/evidence", json=changed).status_code == 200
    old = client.get(f"/api/v1/cases/{case_id}/recommendations/1").json()
    for field in ("chosen_supplier_offer_id", "supplier_name", "unit_price", "total_cost",
                  "evidence", "inputs_fingerprint", "rationale"):
        assert old[field] == original[field]
    stored_approval = db_session.get(Approval, uuid.UUID(approval["approval_id"]))
    assert str(stored_approval.supplier_offer_id) == original["chosen_supplier_offer_id"]
    assert stored_approval.invalidated_at is not None
    current = client.get(f"/api/v1/cases/{case_id}").json()["evidence"]["supplier_offers"]
    assert len(current) == 1 and current[0]["unit_price"] == "14.00"


def test_cors_allows_idempotency_header(client):
    response = client.options("/api/v1/cases", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,idempotency-key",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_mock_store_survives_new_service_instance(tmp_path):
    path = str(tmp_path / "orders.sqlite3")
    original = MockPurchasingService(path)
    po = original.create(idempotency_key="persisted", payload={
        "sku": "sku", "supplier_name": "supplier", "quantity": 800,
        "unit_price": "12.50", "currency": "MXN",
    })
    restarted = MockPurchasingService(path)
    assert restarted.get_by_key("persisted").snapshot() == po.snapshot()
    assert restarted.get_by_external_id(po.external_po_id).quantity == 800


def test_separate_mock_instances_atomically_deduplicate(tmp_path):
    path = str(tmp_path / "orders.sqlite3")
    services = [MockPurchasingService(path), MockPurchasingService(path)]
    def create(service):
        return service.create(idempotency_key="concurrent", payload={
            "sku": "sku", "supplier_name": "supplier", "quantity": 800,
            "unit_price": "12.50", "currency": "MXN",
        }).external_po_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(create, services))) == 1


def test_uncertain_without_persisted_order_reconciles_before_new_attempt(client, monkeypatch):
    from app.mock import MockUnavailable, get_mock_service

    case_id, _ = ready(client)
    approval = approve(client, case_id)
    key = new_idempotency_key()
    service = get_mock_service()
    real_create = service.create
    def unavailable(**kwargs):
        raise MockUnavailable("Transport lost before persistence")
    monkeypatch.setattr(service, "create", unavailable)
    body = {"recommendation_version_number": 1, "approval_id": approval["approval_id"]}
    assert client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                       headers={"Idempotency-Key": key}).status_code == 503
    assert service.get_by_key(key) is None
    monkeypatch.setattr(service, "create", real_create)
    reads = []
    real_read = service.get_by_key
    def reconcile_read(existing_key):
        reads.append(existing_key)
        return real_read(existing_key)
    monkeypatch.setattr(service, "get_by_key", reconcile_read)
    second = client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                         headers={"Idempotency-Key": key})
    assert second.status_code == 201, second.text
    assert reads == [key]
    third = client.post(f"/api/v1/cases/{case_id}/purchases", json=body,
                        headers={"Idempotency-Key": key})
    assert third.json()["purchase_order_id"] == second.json()["purchase_order_id"]
