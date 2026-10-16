"""Pydantic request schemas for the public API.

Response bodies are constructed explicitly in `app/services/response.py` to control
decimal-as-string formatting precisely.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncomingOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_ref: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    expected_arrival_date: date
    status: Literal["confirmed", "unconfirmed", "cancelled"] = "confirmed"


class SupplierOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str = Field(min_length=1)
    unit_price: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    moq: int = Field(default=0, ge=0)
    lead_time_days: int = Field(ge=0)
    is_available: bool = True


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    product_name: str | None = None
    currency: str = Field(min_length=3, max_length=3)
    volume_unit: str = Field(min_length=1)
    initial_recommended_quantity: int = Field(default=800, ge=0)
    planning_horizon_start: date | None = None


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usable_inventory: int | None = Field(default=None, ge=0)
    forecast_demand: int | None = Field(default=None, ge=0)
    safety_stock: int | None = Field(default=None, ge=0)
    budget_amount: Decimal | None = Field(default=None, ge=0)
    storage_capacity_units: Decimal | None = Field(default=None, ge=0)
    storage_required_per_unit: Decimal | None = Field(default=None, ge=0)
    evidence_as_of: datetime | None = None
    incoming_orders: list[IncomingOrderRequest] = Field(default_factory=list)
    supplier_offers: list[SupplierOfferRequest] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    approved_quantity: int | None = Field(default=None, ge=0)
    approver: str = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def _approved_requires_quantity(self) -> "DecisionRequest":
        if self.decision == "approved" and self.approved_quantity is None:
            raise ValueError("approved_quantity is required when decision is 'approved'")
        return self


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_version_number: int = Field(ge=1)
    approval_id: uuid.UUID
