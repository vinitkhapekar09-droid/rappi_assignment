# Development Log — AI Purchasing Agent

## Project Status

**Phase:** Planning review complete; implementation not started
**Last updated:** 2026-10-15

## 1. Assignment Goal

Build a full-stack AI Purchasing Agent that investigates a purchasing situation, evaluates an initial recommendation of 800 units, makes an explainable decision, requires explicit buyer approval for a purchase, executes through a mock purchasing API, and independently validates the persisted result.

The assignment prioritizes one complete end-to-end workflow and meaningful evaluation over implementing every scenario or building production infrastructure.

## 2. Environment Observations

The user-provided terminal output reports:

- Repository: `/workspaces/rappi_assignment`
- Branch: `main`
- Initial HEAD: `327fbbd` (`Initial commit`)
- Remote: `origin` → `https://github.com/vinitkhapekar09-droid/rappi_assignment`
- At the time of the captured output, `AGENTS.md`, `DEVELOPMENT_LOG.md`, and `PROJECT_PLAN.md` were untracked.
- Docker CLI/server available.
- Python 3.12 available as `python3.12` (3.12.3).
- `uv` 0.13.1.
- Node.js 24.21.0 and npm 11.19.0.
- OpenCode 1.19.0.
- PostgreSQL CLI was not initially available; use the planned Docker-based database setup unless the environment changes.

Recheck Git status and installed tools before implementation; these observations may become stale.

## 3. LLM and Coding Tool

DeepSeek V4.1 Flash Free was selected in OpenCode and the user confirmed that it works.

Keep the provider configurable. The application and automated tests must work in deterministic offline mode without external model credentials or network availability. The LLM must never be authoritative for arithmetic, hard constraints, approval, execution authorization, or validation.

## 4. Agreed Technical Direction

- Frontend: React + Vite.
- Backend: FastAPI + Python 3.12.
- Database: PostgreSQL.
- ORM/migrations: SQLAlchemy + Alembic.
- Agent orchestration: LangGraph.
- Typed data validation: Pydantic.
- Tests: pytest.
- Local infrastructure: Docker Compose.
- Planning horizon: fixed seven days.
- Approval: every purchase action requires explicit buyer approval.
- Incoming supply: only confirmed supply arriving within the seven-day horizon is subtracted from demand requirements.
- MOQ conflict: investigate the trade-off; escalate if unresolved rather than silently increasing the order.
- Validation failure: persist discrepancy, halt further automatic purchasing for that action, and escalate; do not automatically retry.
- LLM role: interpret evidence and explain trade-offs; deterministic code enforces business rules.

## 5. Workflow and Safety Decisions

1. Baseline quantity formula:

   `max(0, forecast_demand + safety_stock - usable_inventory - eligible_incoming_quantity)`.

2. Quantities are whole units of one SKU. Price and budget share one configured currency. Storage capacity and item storage consumption use a consistent configured unit.

3. Missing or contradictory decision-critical data must be surfaced; do not assume a constraint passed.

4. Recommendation versions are immutable or explicitly versioned. Approval is tied to the exact version and purchase details.

5. Any relevant change after approval invalidates that approval for the changed action.

6. Use a unique idempotency key per purchase action. Never blindly retry an uncertain or non-idempotent purchase operation.

7. Validation independently reads the persisted mock purchase order; a successful API response alone is not proof of success.

8. On execution failure or validation mismatch, record state and diagnostic information, stop automatic purchasing for that action, and request buyer intervention.

## 6. Planned Implementation Sequence

1. Finalize planning documents.
2. Freeze schemas, API contracts, state transitions, and file ownership.
3. Set up the environment and PostgreSQL.
4. Implement deterministic domain logic and tests.
5. Implement persistence, approval/version guards, idempotency, and mock purchasing operations.
6. Implement LangGraph orchestration and the configurable LLM adapter.
7. Implement the React frontend.
8. Integrate and test the successful and failed-validation paths.
9. Complete README, architecture diagram, `.env.example`, evaluation notes, and demo instructions.

## 7. Evaluation Scenarios

At minimum, evaluate:

- Review of the initial 800-unit recommendation.
- Budget or storage constraint.
- MOQ greater than calculated requirement.
- Missing or contradictory decision-critical data.
- Missing approval or stale recommendation version.
- Duplicate execution/idempotency.
- Mock execution failure.
- Persisted PO mismatch detected by independent validation.
- Offline operation without an LLM provider.

Record expected result, actual result, command, and pass/fail only after execution. No project tests have been run as of this planning entry.

## 8. Parallel Work Guidance

Use separate Git worktrees or branches for parallel coding sessions, not separate Codespaces. Publish API contracts and file ownership first.

Suggested ownership:

- **Backend:** API, models, migrations, domain services, approval, mock purchasing API.
- **Agent:** LangGraph workflow, LLM adapter, workflow orchestration and explanation.
- **Frontend:** React/Vite UI and API client.

Because backend and agent code are tightly coupled, combine those tasks if parallel coordination costs more time than it saves. Do not let workers independently redefine shared schemas.

## 9. Change Log

### 2026-10-15 — Planning review

- Reviewed the assignment and job description as the requirements source of truth.
- Selected a focused 800-unit recommendation review, constraint handling, and independent failed-action validation.
- Consolidated previously agreed purchasing, approval, incoming-supply, and validation policies.
- Prepared updated planning-document content for review.
- No project tests have been run; implementation status remains not started.

Update this file during implementation with actual changes, commands, test outcomes, and unresolved limitations. Never record an unexecuted test as passing.