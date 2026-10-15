# AI Purchasing Agent — Project Plan

## 1. Objective

Build a full-stack AI Purchasing Agent that investigates a purchasing situation, makes an explainable decision, requests buyer approval where an action is proposed, executes an approved purchase through a mock purchasing API, and independently validates the persisted outcome.

The goal is to demonstrate a reliable end-to-end purchasing decision loop—not merely a chatbot that explains a recommendation.

## 2. Assignment Scope

### Primary Scenario: Review an 800-Unit Recommendation

The system receives an initial recommendation to purchase **800 units**. It must not assume that recommendation is correct. It investigates and evaluates:

- Usable on-hand inventory.
- Forecast demand over a fixed seven-day planning horizon.
- Confirmed incoming supply expected to arrive within those seven days.
- Supplier availability and lead time.
- Minimum order quantity (MOQ) and relevant order constraints.
- Available purchasing budget and unit price.
- Storage capacity and the units used to measure it.
- Missing, stale, or inconsistent information.

The agent must choose and explain an appropriate outcome: accept, modify, reject, or investigate/escalate. A purchase recommendation is not permission to execute a purchase.

### Additional Scope: Constraints and Failed-Action Validation

The same end-to-end flow must demonstrate at least one constraint-driven outcome and a validation-failure path. Examples include:

- The calculated requirement conflicts with MOQ.
- The proposed order exceeds budget or available storage.
- Required evidence is missing or contradictory.
- The mock purchasing service persists a quantity or value different from the approved request.
- The purchase operation fails or its result cannot be verified.

When an MOQ-driven larger purchase is not clearly justified, investigate the trade-off and escalate to the buyer rather than silently increasing the quantity.

### Out of Scope

- Real supplier integrations or actual payments.
- Production-grade enterprise infrastructure.
- Implementing all four assignment scenarios independently.
- Autonomous purchases without explicit buyer approval.
- Complex multi-tenant administration or elaborate authentication.
- RAG/vector databases, distributed services, or other infrastructure that does not improve the demonstrated end-to-end flow.

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React + Vite |
| Backend | Python 3.12 + FastAPI |
| Database | PostgreSQL |
| ORM and migrations | SQLAlchemy + Alembic |
| Agent orchestration | LangGraph |
| Request/data validation | Pydantic |
| Testing | pytest |
| Local infrastructure | Docker Compose |
| LLM | Configurable provider with deterministic offline mode |

Use compatible dependency versions and pin them in project configuration. Automated tests must not depend on external LLM availability.

## 4. Architecture

Use a modular monolith. The frontend communicates with FastAPI. The API coordinates deterministic domain services, persistence, the agent workflow, and a mock purchasing service.

### Main Components

1. **React dashboard:** Shows the 800-unit initial recommendation, relevant evidence, calculated requirement, constraint checks, decision rationale, approval controls, execution status, and validation outcome.
2. **FastAPI application:** Exposes typed endpoints and coordinates the workflow.
3. **Purchasing domain service:** Calculates requirements and enforces supplier, MOQ, budget, storage, and data-quality rules.
4. **LangGraph workflow:** Coordinates evidence gathering, decision preparation, approval waiting, execution, and validation.
5. **LLM adapter:** Provides configurable model access and structured output. Model output is treated as untrusted and validated before use.
6. **PostgreSQL:** Persists input data, recommendations and their versions, approval decisions, workflow state, execution attempts, purchase orders, and validation results.
7. **Mock purchasing API:** Simulates purchase-order creation and retrieval. It must support a test mode that deliberately returns or persists a mismatched result.
8. **Independent validator:** Re-reads persisted purchase-order data and compares it with the exact approved request and applicable constraints.

## 5. Purchasing Logic and Explicit Assumptions

Use consistent units throughout. For this demo, quantities represent whole units of one SKU; prices and budgets use one configured currency; storage capacity and item storage consumption use the same configured volume unit. Reject invalid or missing required values rather than guessing.

For a product:

- **Forecast demand:** Expected demand during the seven-day planning horizon.
- **Safety stock:** Additional stock required as a buffer.
- **Usable inventory:** Available, usable on-hand units.
- **Eligible incoming quantity:** Only confirmed incoming supply with an expected arrival within the seven-day horizon.

Calculate the baseline requirement deterministically:

`required_quantity = max(0, forecast_demand + safety_stock - usable_inventory - eligible_incoming_quantity)`

This baseline is not an order authorization. Check supplier availability/lead time, MOQ, budget, and storage before creating a recommendation.

### Constraint Rules

- **Supplier and lead time:** Do not count supply that cannot arrive within the planning horizon as eligible incoming supply. If the chosen supplier cannot meet the need, investigate an alternative or escalate.
- **MOQ:** If MOQ is above the calculated requirement, assess the additional quantity and its budget/storage impact. Do not automatically round up without justification. If unresolved, mark the recommendation as requiring buyer investigation or decision.
- **Budget:** Calculate total purchase cost deterministically. A recommendation that exceeds the available budget must not be presented as executable without a changed, explicitly recorded decision.
- **Storage:** Check the proposed quantity against available capacity using consistent storage units. If the required capacity data is absent, report the uncertainty and do not claim the constraint passed.
- **Missing or inconsistent data:** Request or flag the missing evidence and pause a safe decision when the missing value could change the result.
- **Decision options:** Accept, modify, reject, or investigate/escalate. Always show the important evidence and reason.

The LLM may interpret evidence, identify trade-offs, and produce a human-readable explanation. Deterministic application code remains authoritative for arithmetic, constraint checks, approval, state transitions, and validation.

## 6. Approval, Versioning, and Execution Safety

Every purchase action requires explicit buyer approval.

The workflow should distinguish between these states:

`investigating → recommendation_ready → awaiting_approval → approved → executing → validating → validated`

Alternative states should include `rejected`, `needs_investigation`, `execution_failed`, and `validation_failed`, as appropriate.

- Each recommendation has a stable ID and monotonically increasing version or equivalent immutable snapshot.
- Approval records the exact recommendation version and approved quantity, supplier, and total cost.
- If relevant inputs or the recommendation change after approval, the prior approval cannot authorize the changed request. A new version requires approval.
- Approval is single-use for its exact action. No time-based expiry is required for this demo; invalidation on version change is required.
- Each execution has a unique idempotency key derived from the purchase action. Repeating the same request returns or reconciles the existing result rather than creating a second purchase order.
- If the execution outcome is uncertain, query the mock service using the idempotency key before considering another attempt. Never blindly retry a potentially non-idempotent operation.
- The validator reads the persisted order independently; a successful HTTP response alone is insufficient.
- If execution fails or validation detects a mismatch, record the discrepancy, mark the action failed, stop further automatic purchasing for that action, and surface the issue for buyer intervention. Do not silently correct or retry it.

## 7. Evaluation Plan

Provide a small, reproducible scenario set. Use deterministic fixtures and an offline agent mode so tests run without API credentials.

| Scenario | Setup | Expected Result |
|---|---|---|
| 1. Valid recommendation review | Initial recommendation is 800 units; evidence supports a feasible quantity | Explain whether 800 is accepted or modified; require approval; create one mock PO; independently validate persisted values |
| 2. Budget constraint | Proposed quantity exceeds available budget | Flag the constraint; do not execute without an explicitly approved feasible decision |
| 3. Storage constraint | Proposed quantity exceeds available storage | Flag the constraint; do not treat the recommendation as executable |
| 4. MOQ conflict | MOQ exceeds baseline requirement | Explain the excess and investigate/escalate if not justified; do not silently round up |
| 5. Missing or contradictory data | A decision-critical field is absent or inconsistent | Request investigation or escalate; do not claim the check passed |
| 6. Approval/version guard | No approval exists, or approval is for an older recommendation version | Reject execution |
| 7. Duplicate execution | Same approved action is submitted twice | Only one PO exists; repeated call reconciles to the same result |
| 8. Persisted-result mismatch | Mock service persists a different quantity or supplier than approved | Validator detects mismatch, records failure, and stops further automatic purchasing for that action |
| 9. Execution failure | Mock service fails before creating a PO | Record failure; do not blindly retry |
| 10. Offline mode | LLM provider is unavailable or disabled | Deterministic workflow and tests still run |

Document expected outcomes, actual outcomes, and any known limitations. Do not claim tests passed unless they were run.

## 8. API and Module Boundaries

Freeze request/response contracts before parallel implementation. Keep domain rules independent of HTTP handlers and UI code.

Suggested backend modules:

- `api/`: FastAPI routes and typed schemas.
- `domain/`: quantity calculations and constraint evaluation.
- `services/`: persistence, approval, purchasing, and validation.
- `agent/`: LangGraph state, nodes, and LLM adapter.
- `models/`: SQLAlchemy models.
- `tests/`: deterministic domain and end-to-end tests.

The frontend should depend only on the published API contract. Adjust paths if implementation needs a simpler structure, but record the decision before parallel workers depend on it.

## 9. Implementation Sequence

1. Finalize this plan, `DEVELOPMENT_LOG.md`, and `AGENTS.md`.
2. Freeze domain schemas, API contracts, state transitions, and file ownership.
3. Set up Python 3.12, frontend dependencies, and PostgreSQL via Docker Compose.
4. Implement deterministic domain rules and unit tests.
5. Implement persistence, approval/version checks, idempotency, and the mock purchasing API.
6. Implement the LangGraph workflow and configurable LLM adapter.
7. Implement the React dashboard and API client.
8. Integrate the happy path and forced validation-failure path.
9. Run regression tests and prepare the demo.
10. Complete README, architecture diagram, setup instructions, `.env.example`, test results, and evaluation notes.

## 10. Submission Checklist

- Complete source code in the public GitHub repository.
- README with setup/run instructions and a working demo path.
- Architecture diagram and explanation of the approach.
- Mock APIs, data, and services.
- Test scenarios and evaluation approach with actual results.
- Explanation of approval and independent post-execution validation.
- `.env.example` without secrets.
- Clear Git history and no secrets committed.

## 11. Success Criteria

The project is successful if a reviewer can run the demo, inspect the evidence behind the 800-unit recommendation, understand why the agent accepts, modifies, rejects, or escalates, see buyer approval enforced, observe a mock purchase, and verify that the persisted outcome is independently checked. A forced mismatch must be detected and safely escalated.