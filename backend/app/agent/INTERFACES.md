# Agent module — required backend interfaces (adapter boundary)

The agent module (`app.agent`) owns the LangGraph workflow, the configurable LLM
adapter, structured-output validation, explanation generation, and escalation.
It depends on the deterministic backend **only** through the protocols below.
No routes, tables, or request/response contracts are defined here; those remain
owned by the backend worker and the frozen `docs/API_CONTRACT.md`.

Implementations are injected via `AgentDependencies`
(`app/agent/dependencies.py`). In-memory doubles exist in
`app/agent/adapters/inmemory.py` and a deterministic reference domain service in
`app/agent/adapters/reference_domain.py` (offline tests only — replace with the
real `app/domain` service).

## Ports (`app/agent/ports.py`)

### `EvidenceProvider` — read-only evidence load

```python
def get_case(case_id: str) -> CaseContext              # raise EvidenceNotFoundError
def get_evidence(case_id: str) -> CaseEvidence
```

Maps to `GET /cases/{case_id}` (§5.2). `None` on an evidence field must mean
genuinely missing, never zero.

### `DomainService` — authoritative deterministic rules

```python
def investigate(case: CaseContext, evidence: CaseEvidence) -> InvestigationResult
```

Maps to `POST /cases/{case_id}/recommendations` steps 1–3 (§5.4). Owns the
baseline formula, eligible-supply filtering, supplier selection, constraint
checks, and the `accept|modify|reject|investigate` decision. The agent treats the
result as immutable truth.

### `ApprovalService` — read-only approval lookup

```python
def get_approval(approval_id: str) -> Approval | None
def get_active_approval(case_id: str, version_number: int) -> Approval | None
```

Maps to the persisted result of `POST /cases/{case_id}/recommendations/{version}/decision`
(§5.7). "Active" means `decision == approved`, `invalidated_at is None`, and the
version is the case's current version. The agent never creates approvals.

### `ExecutionService` — idempotent purchase execution

```python
def execute(request: ExecutionRequest) -> ExecutionResult
def reconcile(idempotency_key: str) -> ExecutionResult | None
```

Maps to `POST /cases/{case_id}/purchases` (§5.8). **Must** be idempotent on
`request.idempotency_key`: a repeated request returns the existing result and
never creates a second purchase order. `outcome` must be `succeeded`, `failed`,
or `uncertain`; `uncertain` must not be retried blindly.

### `ValidationService` — independent post-execution validation

```python
def validate(request: ValidationRequest) -> ValidationResult
```

Maps to `POST /cases/{case_id}/purchase-orders/{po_id}/validate` (§5.9). Must
re-read the persisted purchase order independently of the execution response and
return `passed | failed | inconclusive` plus `mismatches`.

### `EventRecorder` — observability (optional)

```python
def record(event: WorkflowEvent) -> None
```

Persists meaningful transitions and failure reasons. Never log secrets.

### `LLMClient` — configurable model adapter

```python
name: str
is_offline: bool
def generate_explanation(context: ExplanationContext) -> RecommendationExplanation | str
```

Implementations: `OfflineLLMClient` (default, deterministic, no network) and
`OpenAICompatibleLLMClient` (opt-in via `AGENT_LLM_PROVIDER=openai_compatible`).
Output is untrusted and always passed through the numeric guard in
`app/agent/explanation.py`.

## Outcome contract

`AgentWorkflow` exposes `recommend`, `execute`, `execute_and_validate`, and
`validate`, each returning a `WorkflowOutcome` with:

`action, status, escalated, recommendation, approval, execution, validation,
error_code, error_message, warnings, events`.

`error_code` values reuse the stable codes in `docs/API_CONTRACT.md` §2.1
(`stale_approval`, `recommendation_superseded`, `approval_mismatch`,
`execution_failed`, `execution_uncertain`, `validation_failed`).

## Configuration

| Env var | Meaning |
|---|---|
| `AGENT_LLM_PROVIDER` | `offline` (default) or `openai_compatible` |
| `AGENT_LLM_MODEL` | model id (required for the HTTP provider) |
| `AGENT_LLM_API_KEY` | credential (required for the HTTP provider) |
| `AGENT_LLM_BASE_URL` | optional base URL for an OpenAI-compatible gateway |
| `AGENT_LLM_TIMEOUT_SECONDS` | request timeout (default 30) |
| `AGENT_OFFLINE` | `1` forces offline mode regardless of provider |

Selecting a real provider without `AGENT_LLM_MODEL`/`AGENT_LLM_API_KEY` raises
`ConfigurationError` immediately (no silent offline fallback).

## Integration note

The package is written at `backend/app/agent/` to match the monorepo module
layout in `docs/PROJECT_PLAN.md` §8. The backend imports `app.agent` and injects the
real port implementations. The coordinator should confirm this path when merging
the worktrees.
