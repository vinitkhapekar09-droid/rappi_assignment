"""FastAPI routes for the purchasing workflow (`docs/API_CONTRACT.md` §4/§5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.schemas import (
    CaseCreateRequest,
    DecisionRequest,
    EvidenceRequest,
    PurchaseRequest,
    RecommendationRequest,
)
from app.services import approvals, cases, evidence, purchases, recommendations, validation


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


router = APIRouter(prefix="/api/v1")


# --- Cases -----------------------------------------------------------------


@router.post("/cases", status_code=201)
def create_case(body: CaseCreateRequest, db: Session = Depends(get_db)) -> dict:
    return cases.create_case(db, body)


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return cases.get_case(db, case_id)


@router.put("/cases/{case_id}/evidence")
def put_evidence(
    case_id: uuid.UUID, body: EvidenceRequest, db: Session = Depends(get_db)
) -> dict:
    return evidence.replace_evidence(db, case_id, body)


# --- Recommendations -------------------------------------------------------


@router.post("/cases/{case_id}/recommendations", status_code=201)
def generate_recommendation(
    case_id: uuid.UUID,
    body: RecommendationRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict:
    return recommendations.generate_recommendation(db, case_id)


@router.get("/cases/{case_id}/recommendations")
def list_recommendations(
    case_id: uuid.UUID,
    latest: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    return recommendations.list_recommendations(db, case_id, latest=latest)


@router.get("/cases/{case_id}/recommendations/{version_number}")
def get_recommendation(
    case_id: uuid.UUID, version_number: int, db: Session = Depends(get_db)
) -> dict:
    return recommendations.get_recommendation(db, case_id, version_number)


@router.post("/cases/{case_id}/recommendations/{version_number}/decision")
def decide(
    case_id: uuid.UUID,
    version_number: int,
    body: DecisionRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    payload, status = approvals.decide(db, case_id, version_number, body)
    return JSONResponse(content=payload, status_code=status)


# --- Purchases -------------------------------------------------------------


@router.post("/cases/{case_id}/purchases")
def execute_purchase(
    case_id: uuid.UUID,
    body: PurchaseRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    payload, status = purchases.execute_purchase(db, case_id, body, idempotency_key)
    return JSONResponse(content=payload, status_code=status)


@router.post("/cases/{case_id}/purchase-orders/{purchase_order_id}/validate")
def validate_purchase_order(
    case_id: uuid.UUID, purchase_order_id: uuid.UUID, db: Session = Depends(get_db)
) -> JSONResponse:
    payload, status = validation.validate_purchase_order(db, case_id, purchase_order_id)
    return JSONResponse(content=payload, status_code=status)


@router.get("/cases/{case_id}/purchase-orders/{purchase_order_id}")
def get_purchase_order(
    case_id: uuid.UUID, purchase_order_id: uuid.UUID, db: Session = Depends(get_db)
) -> dict:
    return purchases.get_purchase_order(db, case_id, purchase_order_id)


@router.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    return {"status": "ok"}
