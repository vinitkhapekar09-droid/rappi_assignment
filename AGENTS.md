# Agent Instructions — AI Purchasing Agent

## 1. Goal and Source of Truth

Implement the Rappi AI Purchasing Agent assignment as a reliable, explainable, full-stack workflow—not a chatbot-only demonstration.

Before starting, read `docs/PROJECT_PLAN.md`, `docs/DEVELOPMENT_LOG.md`, and the original assignment requirements. Treat the assignment as the source of truth for scope and submission requirements.

Prioritize a working end-to-end flow, safe business rules, independent validation, reproducible tests, and interview-ready explanations over unnecessary features.

## 2. General Working Rules

- Inspect the current repository, branch, and Git status before changing files.
- Work only within the assigned task and owned files.
- Follow agreed API contracts, schemas, state transitions, and module boundaries.
- Prefer small, reviewable changes over broad rewrites.
- Inspect existing configuration before replacing it.
- Add dependencies only when justified; keep versions compatible and reproducible.
- Never hardcode secrets or commit credentials. Maintain `.env.example` without real secrets.
- Do not commit, push, merge, deploy, or change remote settings without explicit user permission.
- Never claim a test, build, or manual check passed unless it was actually run.
- Record design decisions, assumptions, limitations, and unresolved issues in `docs/DEVELOPMENT_LOG.md`.

## 3. Non-Negotiable Business Rules

The following must be enforced by deterministic application code, not by trusting the LLM:

- Quantity and monetary calculations.
- Unit consistency and input validation.
- Supplier availability and lead-time eligibility.
- MOQ, budget, and storage constraints.
- Buyer approval before every purchase action.
- Approval tied to the exact recommendation version and purchase details.
- Authorization of workflow state transitions.
- Idempotency and duplicate-execution protection.
- Independent post-execution validation of persisted purchase-order data.

The LLM may interpret evidence, identify trade-offs, and generate structured recommendations or explanations. Treat all model output as untrusted input and validate it before use. Never allow model output to bypass a constraint, invent missing evidence, or authorize a purchase.

## 4. Planning Assumptions

- The main demo starts with the company's example recommendation to buy 800 units.
- Planning horizon is seven days.
- Only confirmed incoming supply expected to arrive within the seven-day horizon reduces the requirement.
- Baseline requirement is `max(0, forecast_demand + safety_stock - usable_inventory - eligible_incoming_quantity)`.
- If MOQ exceeds the baseline requirement, investigate whether the additional quantity is justified; escalate if unresolved. Do not silently round up.
- All quantities, prices, budgets, and storage units must have consistent, explicit meanings.
- Decision-critical missing or contradictory data must be surfaced instead of being assumed valid.

## 5. Approval and Execution Safety

- Every purchase requires explicit buyer approval.
- Approval must refer to the exact immutable/versioned recommendation and approved purchase details.
- Any relevant change after approval requires a new recommendation version and new approval.
- Use a unique idempotency key for each purchase action. A repeated request must not create a second purchase order.
- If execution status is uncertain, query/reconcile the existing operation before considering a retry. Never blindly repeat a potentially non-idempotent operation.
- A successful HTTP response alone is not proof that the purchase succeeded.
- Independently re-read persisted purchase-order data and compare it with the approved request and constraints.
- If execution fails or validation detects a mismatch, persist the discrepancy and diagnostic details, stop further automatic purchasing for that action, and escalate to a human. Do not silently correct or automatically retry the action.

## 6. Testing Rules

- Test arithmetic and constraints deterministically.
- Include offline tests that do not require external LLM credentials or network access.
- Test happy paths, boundary cases, malformed/missing data, approval guards, stale recommendation versions, duplicate execution, execution failure, and persisted-result mismatch.
- Include a reproducible scenario that starts from the 800-unit recommendation.
- Prefer testing the persisted result over trusting a mocked response body.
- Run relevant tests after changes and report exact commands and outcomes.
- Do not weaken or delete tests merely to obtain a passing suite.

## 7. Parallel Work and Ownership

Before parallel implementation begins, the coordinator must publish shared schemas, API contracts, state transitions, and file ownership.

Suggested ownership:

- **Backend/domain worker:** FastAPI routes, typed schemas, SQLAlchemy models, migrations, domain rules, approval enforcement, idempotency, mock purchasing API.
- **Agent worker:** LangGraph nodes/transitions, LLM adapter, explanation generation, workflow integration.
- **Frontend worker:** React/Vite interface and API client, using only the agreed API contract.

Rules:

- Use separate Git branches or worktrees.
- Do not edit files owned by another worker without coordination.
- Do not independently redefine shared request/response schemas.
- Do not integrate unreviewed changes directly into the primary branch.
- Report changed files, design choices, tests run, and unresolved issues.
- If a contract is unclear or unstable, stop and coordinate before implementing dependent code.
- Combine backend and agent ownership if coordination overhead outweighs the benefit of parallel work.

## 8. Code Quality and Observability

- Keep business logic separate from HTTP handlers and UI components.
- Use typed request/response models and clear error responses.
- Use small functions and descriptive names.
- Record meaningful workflow transitions and failure reasons.
- Avoid logging secrets or sensitive configuration.
- Keep frontend loading, approval-pending, success, rejection, execution-failure, and validation-failure states explicit.
- Provide clear local setup instructions and fail visibly when required configuration is missing.

## 9. Handoff Requirements

When finishing a task, report:

1. What changed and which files changed.
2. Important design decisions and their rationale.
3. Tests and commands actually run, with results.
4. Known limitations and unresolved issues.
5. Any integration work still required.

Update `docs/DEVELOPMENT_LOG.md` with factual progress. Keep the README, architecture diagram, `.env.example`, evaluation results, and demo instructions aligned with the actual implementation.

## 10. Run Summary Workflow

Maintain two distinct records:

- `docs/DEVELOPMENT_LOG.md` — the complete, append-only historical timeline. Never move,
  delete, or duplicate its history elsewhere.
- `docs/RUN_SUMMARY.md` — the **latest** OpenCode task/run summary only, shared with ChatGPT for
  review.

After every task or meaningful run, **overwrite** `docs/RUN_SUMMARY.md` with only the latest
run's report (do not append). Use this format:

```
# Latest Run Summary

- **Task:** What was requested.
- **Status:** Completed / Partially completed / Blocked / Failed.
- **Files changed:** File paths and a brief description.
- **Implementation decisions:** Important choices and reasons.
- **Verification:** Commands/tests actually run and their results. Clearly state if nothing was tested.
- **Unresolved issues:** Bugs, risks, or unanswered questions. Write "None identified" only if that is justified.
- **Assignment impact:** Any relevant effect on the Rappi assignment requirements.
- **Recommended next step:** One clear next action.
- **Git status:** Current branch, commit status, and whether anything was pushed.
```

Rules:

1. Overwrite `docs/RUN_SUMMARY.md` after every run; never append.
2. Keep it concise, preferably under 60 lines.
3. Report actual outcomes; never invent successful tests or completed work.
4. Update the summary even if the task fails or makes no changes.
5. Never include secrets, API keys, or environment variable values.
6. Do not commit, push, or deploy unless explicitly authorized.
7. At the end of each task, mention that `docs/RUN_SUMMARY.md` was updated.
