"""Shared builders for the agent test suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.agent.adapters.inmemory import (
    ExecutionMode,
    InMemoryPurchasingBackend,
    ListEventRecorder,
)
from app.agent.adapters.reference_domain import ReferenceDomainService
from app.agent.config import AgentConfig
from app.agent.dependencies import AgentDependencies
from app.agent.llm.offline import OfflineLLMClient
from app.agent.models import (
    Approval,
    CaseContext,
    CaseEvidence,
    ExplanationContext,
    IncomingOrder,
    RecommendationExplanation,
    SupplierOffer,
)
from app.agent.ports import LLMClient
from app.agent.service import AgentWorkflow

CASE_ID = "case-1"
SUPPLIER_ID = "so-1"


def make_case(**overrides: Any) -> CaseContext:
    base: dict[str, Any] = {
        "case_id": CASE_ID,
        "sku": "SKU-1234",
        "product_name": "Agua Embotellada 1L",
        "currency": "MXN",
        "volume_unit": "liter",
        "planning_horizon_start": date(2026, 10, 16),
        "planning_horizon_days": 7,
        "initial_recommended_quantity": 800,
    }
    base.update(overrides)
    return CaseContext(**base)


def default_supplier(**overrides: Any) -> SupplierOffer:
    base: dict[str, Any] = {
        "supplier_offer_id": SUPPLIER_ID,
        "supplier_name": "Distribuidora Norte",
        "unit_price": Decimal("12.50"),
        "currency": "MXN",
        "moq": 500,
        "lead_time_days": 3,
        "is_available": True,
    }
    base.update(overrides)
    return SupplierOffer(**base)


def default_incoming(**overrides: Any) -> IncomingOrder:
    base: dict[str, Any] = {
        "incoming_order_id": "io-1",
        "external_ref": "PO-IN-1",
        "quantity": 100,
        "expected_arrival_date": date(2026, 10, 18),
        "status": "confirmed",
    }
    base.update(overrides)
    return IncomingOrder(**base)


def make_evidence(**overrides: Any) -> CaseEvidence:
    base: dict[str, Any] = {
        "usable_inventory": 200,
        "forecast_demand": 1000,
        "safety_stock": 100,
        "budget_amount": Decimal("15000.00"),
        "storage_capacity_units": Decimal("1000.0"),
        "storage_required_per_unit": Decimal("1.0"),
        "incoming_orders": [default_incoming()],
        "supplier_offers": [default_supplier()],
    }
    base.update(overrides)
    return CaseEvidence(**base)


def make_approval(**overrides: Any) -> Approval:
    base: dict[str, Any] = {
        "approval_id": "approval-1",
        "case_id": CASE_ID,
        "recommendation_version_number": 1,
        "decision": "approved",
        "approved_quantity": 800,
        "supplier_offer_id": SUPPLIER_ID,
        "supplier_name": "Distribuidora Norte",
        "unit_price": Decimal("12.50"),
        "total_cost": Decimal("10000.00"),
        "currency": "MXN",
        "approver": "buyer@rappi.example",
        "note": "Approved for the seven-day horizon.",
        "created_at": datetime(2026, 10, 15, 12, 6, tzinfo=UTC),
    }
    base.update(overrides)
    return Approval(**base)


@dataclass
class Harness:
    backend: InMemoryPurchasingBackend
    workflow: AgentWorkflow
    recorder: ListEventRecorder
    case: CaseContext
    evidence: CaseEvidence


def build_harness(
    *,
    case: CaseContext | None = None,
    evidence: CaseEvidence | None = None,
    approvals: dict[str, Approval] | None = None,
    execution_mode: ExecutionMode = "succeed",
    mismatch: dict[str, Any] | None = None,
    llm: LLMClient | None = None,
    config: AgentConfig | None = None,
) -> Harness:
    case = case or make_case()
    evidence = evidence or make_evidence()
    backend = InMemoryPurchasingBackend(
        cases={case.case_id: case},
        evidence={case.case_id: evidence},
        approvals=approvals if approvals is not None else {},
        execution_mode=execution_mode,
        mismatch=mismatch,
    )
    recorder = ListEventRecorder()
    deps = AgentDependencies(
        evidence=backend,
        domain=ReferenceDomainService(),
        approvals=backend,
        execution=backend,
        validation=backend,
        llm=llm or OfflineLLMClient(),
        recorder=recorder,
        config=config or AgentConfig(),
    )
    return Harness(
        backend=backend,
        workflow=AgentWorkflow(deps),
        recorder=recorder,
        case=case,
        evidence=evidence,
    )


class StaticLLM:
    """Returns a fixed explanation (or raises) for guard tests."""

    name = "static-test"
    is_offline = True

    def __init__(
        self,
        explanation: RecommendationExplanation | str | dict[str, Any],
        *,
        raises: Exception | None = None,
    ) -> None:
        self._explanation = explanation
        self._raises = raises
        self.calls = 0

    def generate_explanation(self, context: ExplanationContext) -> RecommendationExplanation | str:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._explanation
