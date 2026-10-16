"""Live recommendation ports backed by the existing deterministic domain.

The graph prepares recommendations only. Purchase actions stay on the existing
transactional backend endpoints, which own approval, recovery and validation.
No reference or in-memory purchasing adapter is constructed here.
"""

from dataclasses import asdict, replace

from app.agent.config import AgentConfig
from app.agent.dependencies import AgentDependencies
from app.agent.llm.factory import build_llm
from app.agent.models import (
    CaseContext, CaseEvidence, ConstraintCheck, EvidenceView,
    IncomingOrder, InvestigationResult, SupplierOffer,
)
from app.agent.service import AgentWorkflow
from app.domain.purchasing import build_recommendation


class BackendRecommendationPorts:
    def __init__(self, case_id, context, evidence):
        self.case_id = str(case_id)
        self.context = context
        self.evidence = evidence
        self.draft = None

    def get_case(self, case_id):
        if case_id != self.case_id:
            raise ValueError("Unexpected case identity")
        return CaseContext(case_id=case_id, **asdict(self.context))

    def get_evidence(self, case_id):
        self.get_case(case_id)
        ev = self.evidence
        return CaseEvidence(
            **{name: getattr(ev, name) for name in (
                "usable_inventory", "forecast_demand", "safety_stock", "budget_amount",
                "storage_capacity_units", "storage_required_per_unit",
            )},
            incoming_orders=[IncomingOrder(incoming_order_id=o.external_ref, **asdict(o))
                             for o in ev.incoming_orders],
            supplier_offers=[SupplierOffer(
                supplier_offer_id=str(o.id),
                **{k: v for k, v in asdict(o).items() if k != "id"},
            ) for o in ev.supplier_offers],
        )

    def investigate(self, case, evidence):
        # Reuse the exact backend inputs; agent-local models never recalculate terms.
        draft = self.draft = build_recommendation(self.context, self.evidence)
        fields = (
            "decision", "baseline_required_quantity", "recommended_quantity", "supplier_name",
            "unit_price", "currency", "total_cost", "lead_time_days", "moq", "assumptions",
            "missing_information", "inputs_fingerprint",
        )
        view = {k: v for k, v in draft.evidence_snapshot.items()
                if k in EvidenceView.model_fields}
        # Preserve an unknown incoming quantity when demand/inventory is missing.
        incoming = draft.evidence_snapshot["eligible_incoming_quantity"]
        return InvestigationResult(
            case_id=self.case_id,
            **{k: getattr(draft, k) for k in fields},
            chosen_supplier_offer_id=str(draft.chosen_supplier_offer_id)
                if draft.chosen_supplier_offer_id else None,
            eligible_incoming_quantity=incoming,
            evidence=EvidenceView(**view),
            constraint_checks=[ConstraintCheck(**asdict(c)) for c in draft.constraint_checks],
        )


class BackendOwnedPurchaseActions:
    """Fail closed if a recommendation graph ever dispatches a purchase action."""

    def get_approval(self, *args):
        raise RuntimeError("Use the backend approval/execution endpoints")

    get_active_approval = get_approval
    execute = get_approval
    reconcile = get_approval
    validate = get_approval


def generate_agent_draft(case_id, context, evidence):
    ports = BackendRecommendationPorts(case_id, context, evidence)
    purchase_actions = BackendOwnedPurchaseActions()
    config = AgentConfig.from_env()
    workflow = AgentWorkflow(AgentDependencies(
        evidence=ports, domain=ports, approvals=purchase_actions,
        execution=purchase_actions, validation=purchase_actions,
        llm=build_llm(config), config=config,
    ))
    outcome = workflow.recommend(str(case_id))
    if ports.draft is None or outcome.recommendation is None:
        raise RuntimeError("LangGraph did not produce a recommendation")
    # Persist the trace with the existing extensible constraint details, in the
    # same transaction as the immutable recommendation; no new REST schema.
    trace = {
        "engine": "langgraph", "provider": config.llm_provider,
        "events": [event.model_dump(mode="json") for event in outcome.events],
        "warnings": outcome.warnings, "escalated": outcome.escalated,
        "inference": outcome.inference.model_dump(mode="json") if outcome.inference else None,
        "interpretation": outcome.explanation.model_dump(mode="json") if outcome.explanation else None,
    }
    checks = [replace(c, details={**c.details, "agent_workflow": trace})
              if c.constraint_type == "data_quality" else c
              for c in ports.draft.constraint_checks]
    # Only explanation text is imported from the graph. Authoritative purchase
    # fields and the evidence fingerprint remain the backend domain's output.
    return replace(ports.draft, rationale=outcome.recommendation.rationale,
                   generated_by=outcome.recommendation.generated_by,
                   constraint_checks=checks)
