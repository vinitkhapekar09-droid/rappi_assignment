"""LangGraph node functions.

Each node is a small, single-purpose step. Nodes only orchestrate: all business
rules live behind the ports. The agent never lets model output select a supplier,
set a quantity, or satisfy the approval guard.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.agent.dependencies import AgentDependencies
from app.agent.errors import EvidenceNotFoundError
from app.agent.explanation import compose_rationale, validate_and_sanitize
from app.agent.llm.offline import OfflineLLMClient
from app.agent.ids import derive_execution_idempotency_key
from app.agent.models import (
    ExecutionRequest,
    ExplanationContext,
    InferenceInfo,
    RecommendationDraft,
    RecommendationExplanation,
    ValidationRequest,
    WorkflowEvent,
)
from app.agent.state import AgentState


def _now() -> datetime:
    return datetime.now(UTC)


class WorkflowNodes:
    """Bound node callables for one :class:`AgentDependencies` container."""

    def __init__(self, deps: AgentDependencies) -> None:
        self.deps = deps

    # ------------------------------------------------------------------ events
    def _emit(self, event: str, message: str, **data: Any) -> WorkflowEvent:
        record = WorkflowEvent(event=event, message=message, at=_now(), data=data)
        if self.deps.recorder is not None:
            self.deps.recorder.record(record)
        return record

    # ---------------------------------------------------------------- dispatch
    def dispatch(self, state: AgentState) -> str:
        action = state.get("action", "recommend")
        if action == "recommend":
            return "load_case"
        if action in ("execute", "execute_and_validate"):
            return "guard_approval"
        if action == "validate":
            return "validate"
        raise ValueError(f"Unsupported agent action: {action!r}")

    # ----------------------------------------------------------- investigation
    def load_case(self, state: AgentState) -> dict[str, Any]:
        case = self.deps.evidence.get_case(state["case_id"])
        return {
            "case": case,
            "events": [self._emit("case_loaded", "Loaded case context.", case_id=case.case_id)],
        }

    def load_evidence(self, state: AgentState) -> dict[str, Any]:
        if "case" not in state:
            raise EvidenceNotFoundError("Case context must be loaded before evidence.")
        evidence = self.deps.evidence.get_evidence(state["case_id"])
        return {
            "evidence": evidence,
            "events": [
                self._emit(
                    "evidence_loaded",
                    "Loaded case evidence.",
                    incoming_orders=len(evidence.incoming_orders),
                    supplier_offers=len(evidence.supplier_offers),
                )
            ],
        }

    def investigate(self, state: AgentState) -> dict[str, Any]:
        investigation = self.deps.domain.investigate(state["case"], state["evidence"])
        return {
            "investigation": investigation,
            "status": "investigating",
            "events": [
                self._emit(
                    "investigation_complete",
                    "Deterministic investigation complete.",
                    decision=investigation.decision,
                    baseline_required_quantity=investigation.baseline_required_quantity,
                    inputs_fingerprint=investigation.inputs_fingerprint,
                )
            ],
        }

    def explain(self, state: AgentState) -> dict[str, Any]:
        context = ExplanationContext(
            case=state["case"], investigation=state["investigation"], evidence=state.get("evidence")
        )
        warnings: list[str] = []
        offline = self.deps.llm.is_offline
        reason = "Offline mode explicitly selected; no online inference was attempted." if offline else None
        try:
            raw = self.deps.llm.generate_explanation(context)
        except Exception as exc:  # noqa: BLE001 - provider is untrusted/external
            reason = f"Provider request failed ({type(exc).__name__}); deterministic fallback used."
            warnings.append(
                f"LLM provider failed ({type(exc).__name__}); using the deterministic explanation."
            )
            raw = RecommendationExplanation()

        explanation, guard_warnings = validate_and_sanitize(raw, context)
        warnings.extend(guard_warnings)
        if guard_warnings:
            reason = "Model output failed validation; deterministic fallback used."
        if (reason and not offline) or guard_warnings:
            explanation, _ = validate_and_sanitize(OfflineLLMClient().generate_explanation(context), context)
        success = not offline and reason is None
        requested_provider = getattr(self.deps.llm, "provider", self.deps.config.llm_provider)
        inference = InferenceInfo(
            mode="online" if success else "offline_fallback" if offline else "provider_fallback",
            online_succeeded=success,
            provider=requested_provider if success else "offline",
            model=getattr(self.deps.llm, "model", self.deps.config.llm_model) if success else None,
            requested_provider=requested_provider,
            requested_model=self.deps.config.llm_model,
            fallback_reason=reason,
        )
        return {
            "explanation": explanation,
            "inference": inference,
            "warnings": warnings,
            "events": [
                self._emit(
                    "explanation_generated",
                    "Interpreting explanation produced and validated.",
                    provider=getattr(self.deps.llm, "name", "unknown"),
                    guard_warnings=len(guard_warnings),
                    mode=inference.mode,
                    online_succeeded=success,
                )
            ],
        }

    def assemble_recommendation(self, state: AgentState) -> dict[str, Any]:
        investigation = state["investigation"]
        explanation = state.get("explanation") or RecommendationExplanation()
        rationale = compose_rationale(investigation, explanation)
        generated_by = "agent" if state["inference"].online_succeeded else "offline"
        draft = RecommendationDraft(
            recommendation_id=str(uuid.uuid4()),
            case_id=state["case_id"],
            version_number=state.get("version_number") or 1,
            decision=investigation.decision,
            initial_recommended_quantity=state["case"].initial_recommended_quantity,
            baseline_required_quantity=investigation.baseline_required_quantity,
            recommended_quantity=investigation.recommended_quantity,
            chosen_supplier_offer_id=investigation.chosen_supplier_offer_id,
            supplier_name=investigation.supplier_name,
            unit_price=investigation.unit_price,
            currency=investigation.currency,
            total_cost=investigation.total_cost,
            lead_time_days=investigation.lead_time_days,
            moq=investigation.moq,
            rationale=rationale,
            evidence=investigation.evidence,
            assumptions=investigation.assumptions,
            missing_information=investigation.missing_information,
            inputs_fingerprint=investigation.inputs_fingerprint,
            constraint_checks=investigation.constraint_checks,
            generated_by=generated_by,  # type: ignore[arg-type]
            created_at=_now(),
        )
        return {
            "recommendation": draft,
            "events": [
                self._emit(
                    "recommendation_ready",
                    "Recommendation assembled and ready for buyer approval.",
                    decision=draft.decision,
                    recommended_quantity=draft.recommended_quantity,
                )
            ],
        }

    def finalize_recommendation(self, state: AgentState) -> dict[str, Any]:
        decision = state["recommendation"].decision
        if decision in ("accept", "modify"):
            status = "awaiting_approval"
        elif decision == "reject":
            status = "rejected"
        else:
            status = "needs_investigation"
        return {"status": status}

    def route_after_recommendation(self, state: AgentState) -> str:
        if state["recommendation"].decision == "investigate":
            return "escalate"
        return "end"

    # --------------------------------------------------------------- approval
    def guard_approval(self, state: AgentState) -> dict[str, Any]:
        case_id = state["case_id"]
        version = state.get("version_number")
        approval_id = state.get("approval_id")

        approval = None
        if approval_id:
            approval = self.deps.approvals.get_approval(approval_id)
        if approval is None and version is not None:
            approval = self.deps.approvals.get_active_approval(case_id, version)

        if approval is None:
            return self._guard_failure(
                state,
                "stale_approval",
                "No active buyer approval exists for the requested recommendation version.",
            )
        if approval.case_id != case_id:
            return self._guard_failure(
                state, "approval_mismatch", "Approval belongs to a different case."
            )
        if approval.decision != "approved":
            return self._guard_failure(
                state, "stale_approval", "The persisted decision is not an approval."
            )
        if approval.invalidated_at is not None:
            return self._guard_failure(
                state, "stale_approval", "The approval has been invalidated."
            )
        if version is not None and approval.recommendation_version_number != version:
            return self._guard_failure(
                state,
                "recommendation_superseded",
                "The approval is tied to a different recommendation version.",
            )
        if (
            approval.approved_quantity is None
            or approval.supplier_name is None
            or approval.unit_price is None
            or approval.total_cost is None
            or approval.currency is None
        ):
            return self._guard_failure(
                state, "approval_mismatch", "The approval is missing purchase details."
            )

        resolved_version = approval.recommendation_version_number
        return {
            "approval": approval,
            "version_number": resolved_version,
            "events": [
                self._emit(
                    "approval_verified",
                    "Approval verified for the exact recommendation version.",
                    approval_id=approval.approval_id,
                    recommendation_version_number=resolved_version,
                )
            ],
        }

    def _guard_failure(
        self, state: AgentState, code: str, message: str
    ) -> dict[str, Any]:
        return {
            "escalated": True,
            "status": "needs_investigation",
            "error_code": code,
            "error_message": message,
            "events": [
                self._emit(
                    "approval_rejected",
                    message,
                    error_code=code,
                    case_id=state.get("case_id"),
                )
            ],
        }

    def route_after_guard(self, state: AgentState) -> str:
        return "escalate" if state.get("escalated") else "execute"

    # -------------------------------------------------------------- execution
    def execute(self, state: AgentState) -> dict[str, Any]:
        approval = state["approval"]
        assert approval is not None  # guarded by route_after_guard
        case = state.get("case") or self.deps.evidence.get_case(state["case_id"])
        version = state["version_number"] or approval.recommendation_version_number
        idempotency_key = state.get("idempotency_key") or derive_execution_idempotency_key(
            case_id=state["case_id"],
            version_number=version,
            approval_id=approval.approval_id,
            quantity=approval.approved_quantity,
            supplier_offer_id=approval.supplier_offer_id,
        )

        request = ExecutionRequest(
            case_id=state["case_id"],
            recommendation_version_number=version,
            approval_id=approval.approval_id,
            sku=case.sku,
            supplier_offer_id=approval.supplier_offer_id,
            supplier_name=approval.supplier_name or "",
            quantity=approval.approved_quantity or 0,
            unit_price=approval.unit_price or 0,
            total_cost=approval.total_cost or 0,
            currency=approval.currency or case.currency,
            idempotency_key=idempotency_key,
        )

        result = self.deps.execution.execute(request)
        events = [
            self._emit(
                "execution_attempted",
                "Purchase execution attempted.",
                outcome=result.outcome,
                idempotency_key=idempotency_key,
                purchase_order_id=result.purchase_order_id,
            )
        ]

        if result.outcome == "succeeded":
            return {
                "execution": result,
                "case": case,
                "purchase_order_id": result.purchase_order_id,
                "status": "validating",
                "events": events,
            }
        if result.outcome == "failed":
            return {
                "execution": result,
                "case": case,
                "status": "execution_failed",
                "escalated": True,
                "error_code": result.error_code or "execution_failed",
                "error_message": result.error_message
                or "The purchasing service reported a failure before creating a purchase order.",
                "events": events,
            }
        # uncertain: park for reconciliation; never retry blindly.
        return {
            "execution": result,
            "case": case,
            "status": "validating",
            "escalated": True,
            "error_code": "execution_uncertain",
            "error_message": result.error_message
            or "The execution outcome could not be confirmed; reconcile before retrying.",
            "events": events,
        }

    def route_after_execute(self, state: AgentState) -> str:
        execution = state.get("execution")
        if execution is None or execution.outcome != "succeeded":
            return "escalate"
        if state.get("auto_validate"):
            return "validate"
        return "end"

    # ------------------------------------------------------------- validation
    def validate(self, state: AgentState) -> dict[str, Any]:
        purchase_order_id = state.get("purchase_order_id")
        if not purchase_order_id and state.get("execution") is not None:
            purchase_order_id = state["execution"].purchase_order_id
        if not purchase_order_id:
            return {
                "escalated": True,
                "status": state.get("status", "validating"),
                "error_code": "invalid_state",
                "error_message": "No purchase order is available to validate.",
                "events": [
                    self._emit(
                        "validation_skipped",
                        "Validation requested without a purchase order.",
                        case_id=state.get("case_id"),
                    )
                ],
            }

        result = self.deps.validation.validate(
            ValidationRequest(
                case_id=state["case_id"], purchase_order_id=purchase_order_id
            )
        )

        events = [
            self._emit(
                "validation_complete",
                "Independent validation finished.",
                status=result.status,
                purchase_order_id=purchase_order_id,
                mismatch_count=len(result.mismatches),
            )
        ]

        if result.status == "passed":
            return {
                "validation": result,
                "status": "validated",
                "escalated": False,
                "events": events,
            }
        if result.status == "failed":
            return {
                "validation": result,
                "status": "validation_failed",
                "escalated": True,
                "error_code": "validation_failed",
                "error_message": (
                    "Independent validation detected a mismatch between the persisted "
                    "purchase order and the approved request. Automatic purchasing for "
                    "this action has stopped; buyer intervention is required."
                ),
                "events": events,
            }
        return {
            "validation": result,
            "status": "validating",
            "escalated": True,
            "error_code": "validation_inconclusive",
            "error_message": (
                "Validation was inconclusive; the action remains blocked pending reconciliation."
            ),
            "events": events,
        }

    def route_after_validate(self, state: AgentState) -> str:
        return "end" if state.get("status") == "validated" else "escalate"

    # -------------------------------------------------------------- escalation
    def escalate(self, state: AgentState) -> dict[str, Any]:
        status = state.get("status") or "needs_investigation"
        return {
            "escalated": True,
            "status": status,
            "events": [
                self._emit(
                    "escalated",
                    state.get("error_message")
                    or "Action halted and escalated for human intervention.",
                    error_code=state.get("error_code"),
                    status=status,
                )
            ],
        }
