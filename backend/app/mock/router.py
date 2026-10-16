"""Mock purchasing HTTP API (demonstration + test control).

The execution/validation services use the in-process singleton directly; this router
exposes the same store over HTTP so the mock behaves like an external service.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.mock import service as mock

router = APIRouter(prefix="/mock/v1", tags=["mock-purchasing"])


class MockCreateRequest(BaseModel):
    sku: str
    supplier_name: str
    quantity: int = Field(ge=1)
    unit_price: Decimal
    currency: str


class MockControlRequest(BaseModel):
    create_mode: str | None = None
    read_mode: str | None = None
    mismatch: str | None = None


@router.post("/purchase-orders", status_code=201)
def mock_create(body: MockCreateRequest, idempotency_key: str | None = None) -> dict[str, Any]:
    key = idempotency_key or body.model_dump_json()
    try:
        po = mock.get_mock_service().create(
            idempotency_key=key,
            payload={
                "sku": body.sku,
                "supplier_name": body.supplier_name,
                "quantity": body.quantity,
                "unit_price": str(body.unit_price),
                "currency": body.currency,
            },
        )
    except mock.MockFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except mock.MockUncertain as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return po.snapshot()


@router.get("/purchase-orders/by-key/{key}")
def mock_get_by_key(key: str) -> dict[str, Any]:
    try:
        po = mock.get_mock_service().get_by_key(key)
    except mock.MockUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if po is None:
        raise HTTPException(status_code=404, detail="No purchase order for key")
    return po.snapshot()


@router.get("/purchase-orders/{external_po_id}")
def mock_get_by_external(external_po_id: str) -> dict[str, Any]:
    try:
        po = mock.get_mock_service().get_by_external_id(external_po_id)
    except mock.MockUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if po is None:
        raise HTTPException(status_code=404, detail="Unknown purchase order")
    return po.snapshot()


@router.post("/_control")
def mock_control(body: MockControlRequest) -> dict[str, str]:
    mock.get_mock_service().configure(
        create_mode=body.create_mode, read_mode=body.read_mode, mismatch=body.mismatch
    )
    svc = mock.get_mock_service()
    return {
        "create_mode": svc.create_mode,
        "read_mode": svc.read_mode,
        "mismatch": svc.mismatch,
    }


@router.post("/_reset")
def mock_reset() -> dict[str, str]:
    mock.get_mock_service().reset()
    return {"status": "reset"}
