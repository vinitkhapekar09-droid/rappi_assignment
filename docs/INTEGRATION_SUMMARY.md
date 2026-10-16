# Integration Summary — 2026-10-16

Submission packaging places contracts, plan, history, and current evaluation artifacts
under `docs/`. Earlier command transcripts retain the output paths used at the time; the
current result files are under `docs/evaluation/`.

Implemented and verified the local end-to-end purchasing workflow in
`/workspaces/rappi-frontend`. The working Compose fallback serves the dashboard at
http://localhost:5175 and API docs at http://localhost:8003/docs.

## Changes and architecture

- Copied `backend/app/agent/` and `backend/tests/agent/` from the agent worktree after
  confirming those destination paths did not exist. Source worktrees were not edited.
- Extended the existing `backend/pyproject.toml` with pinned LangGraph and requests;
  retained the backend dependencies and generated `backend/uv.lock`.
- Added `backend/app/agent/adapters/backend.py`. Every live recommendation invokes the
  real LangGraph, which calls the existing deterministic domain builder. Only explanation
  text comes back from the graph; purchase terms remain authoritative backend output.
  Graph events/warnings persist in the data-quality constraint's existing JSON details.
- `backend/app/services/recommendations.py` uses this adapter. Agent evidence models
  preserve unknown incoming quantity as null. Reference and in-memory agent adapters
  remain test doubles and are not used by live endpoints.
- Existing PostgreSQL services remain authoritative for approval, execution, idempotency,
  reconciliation and independent validation. The graph's live recommendation adapter
  deliberately cannot perform purchase actions. PostgreSQL persists workflow state;
  no LangGraph checkpoint infrastructure was introduced.
- Replaced the backend mock's process-memory order dictionaries with an independent,
  durable SQLite store. Separate connections re-read committed orders; unique keys and
  write transactions deduplicate concurrent service instances. Store access/commit
  failures become uncertain outcomes requiring reconciliation.
- Closed buyer-decision reopening after execution, invalidated approvals on rejection,
  checked all exact approval terms, compared persisted cost/status/order identity, and
  allowed recovery from `executing` only after confirmed absence and an atomic key claim.
- Added migration `8b28a4b1c204` and `SupplierOffer.is_current`: evidence replacement
  archives supplier offers instead of clearing immutable version/approval references.
  Old purchase terms remain intact. Current-name uniqueness uses a partial unique index.
- Frontend defaults to HTTP, uses the Vite/nginx same-origin API proxy, and supports CORS
  for explicit backend origins. Replayed HTTP-200 failure envelopes surface as errors.
  Uncertain execution exposes reconciliation with the existing key.
- Added `backend/tests/test_integration.py`, `frontend/src/api/httpApi.test.ts`, isolated
  test mock storage, setup Dockerfiles/Compose/env examples, `scripts/demo_api.py`,
  `evaluation_results.json`, README instructions/architecture, and schema/status notes.

## Commands actually run and results

Backend commands below were run from `backend/` using Python 3.12.3; network/database
commands required execution outside the restricted sandbox.

```bash
UV_CACHE_DIR=/tmp/rappi-uv-cache uv pip install --python backend/.venv/bin/python -e 'backend[dev]'
# Above ran from repository root: installed the integrated package/dependencies.
UV_CACHE_DIR=/tmp/rappi-uv-cache uv lock --python .venv/bin/python
UV_CACHE_DIR=/tmp/rappi-uv-cache uv sync --frozen --extra dev --python .venv/bin/python
RAPPI_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi .venv/bin/alembic upgrade head
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q -o faulthandler_timeout=45
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:9/rappi_offline_test .venv/bin/python -m pytest -q tests/test_domain.py tests/agent
```

- Lock/sync and both migrations succeeded. Final full suite: **120 passed**, two dependency
  deprecation warnings, 31.89 seconds. Offline subset with PostgreSQL unavailable:
  **71 passed**, one dependency warning. The 46 copied agent tests are included.
- An initial sandbox full-suite run could not reach PostgreSQL and was interrupted;
  the unrestricted, isolated-database runs above are the reported successful results.
- No migration downgrade was run in this integration. The history migration intentionally
  refuses rollback if archived rows violate the old uniqueness rule, preserving history.

Frontend, from `frontend/`:

```bash
npm run typecheck
npm test
npm run build
npm audit --omit=dev --json
```

All succeeded: **27 tests passed**, TypeScript clean, production bundle built.
Production-only audit: zero findings. Image `npm ci` reported six existing development
advisory findings (three moderate, one high, two critical); no major toolchain upgrade
was attempted. Initial restricted audit/download attempts failed DNS and were rerun
with network access.

Packaging, from repository root:

```bash
docker compose config --quiet
docker compose build
RAPPI_DB_PORT=55433 RAPPI_API_PORT=8001 RAPPI_WEB_PORT=5174 docker compose up -d --wait
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml config --quiet
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
python scripts/demo_api.py --base-url http://127.0.0.1:8003 --output evaluation_results.json
curl -fsS http://127.0.0.1:5175/api/v1/health
curl -fsS http://127.0.0.1:5175/
```

Both image builds and configuration checks succeeded. Standard Compose startup failed
readiness: Docker bridge DNS resolved `db`, but a five-second socket probe from backend
to `db:5432` timed out. PostgreSQL accepted connections inside its own container. The
Linux host-network fallback **started successfully, all three services healthy**. Its
nginx returned the production HTML and proxied API health successfully. The backend image
also passed a separate host-network run on port 8002. Early demo calls made before
readiness failed connection establishment; the final healthy-stack run above succeeded.

Final live results (persisted in `evaluation_results.json`):

| Scenario | Actual outcome |
|---|---|
| Initial 800 → persisted approval → purchase → independent validation | 800 units, MXN 10,000; `passed` |
| Forced persisted quantity mismatch | Approved 800; stored 500 / MXN 6,250; quantity and cost mismatches persisted; `validation_failed` |
| Purchase after mismatch | 409 `invalid_state`; no automatic correction/retry |
| Missing approval | 409 `invalid_state` |
| Old approval supplied for current version | 409 `stale_approval` |
| Repeated execution with same key | Same purchase order returned |
| Missing budget evidence | `investigate`, null recommended quantity |

Independent database confirmation:

```bash
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml exec -T db psql -p 55434 -U rappi -d rappi -c "SELECT idempotency_key, count(*) AS purchase_orders FROM purchase_orders GROUP BY idempotency_key; SELECT status, count(*) FROM validation_results GROUP BY status;"
```

Result: exactly one PO per each of the two execution keys; one passed and one failed
validation. Concurrent duplicate and restart durability checks also passed in pytest.
`git diff --check` and `python -m compileall -q backend/app backend/tests scripts` passed.

## Remaining limitations and blockers

- Visual browser verification remains outstanding: no browser tool or browser executable
  was available. Component/HTTP tests, HTML serving and live proxy requests were verified.
- Standard bridge-mode Compose cannot communicate between containers in this environment;
  the checked-in Linux host-network fallback is verified. Bridge mode remains unverified
  on an unrestricted host. No firewall or remote settings were changed.
- Assignment scope was subsequently reviewed externally by the user; see the final
  acceptance check below. No additional scenarios or infrastructure are required.
- Remote LLM operation is unverified. It is optional; offline mode is verified.
- Demo identities have no authentication, fault controls are global/process-local, UI
  refresh creates a new case, and no automatic evidence freshness policy is implemented.
  Live recommendations do not currently emit justified MOQ-driven `modify` decisions.
- Earlier local demo processes/containers and their data were preserved. The final
  supported stack is project `rappi-hostcheck`, ports 5175/8003/55434.

## Git status and proposed integration plan

Branch: `feature/frontend`; HEAD: `54f5983`, unchanged. Pre-existing backend/frontend and
log/summary work was uncommitted before this run and remains uncommitted. Current changes
include API/schema status notes, README/log/summaries and untracked backend/frontend,
Compose/env setup, script and evaluation artifacts. No commit, branch operation, merge,
push, reset, clean, source-worktree deletion or remote deployment was performed.
The agent source remains on `feature/ai-agent` with its original uncommitted work.

Proposed plan, **not executed**: review the complete untracked implementation and additive
migration; perform the browser walkthrough; after
explicit authorization, commit backend/agent integration and safety tests, frontend wiring,
then reproducibility/docs. Integrate reviewed commits into the submission branch only
after authorization. Do not overwrite or merge the source worktrees wholesale.

## Final acceptance check — 2026-10-16

The user externally confirmed the assignment scope: at least one meaningful investigated,
explained, approved, executed and independently validated scenario, evaluation scenarios,
and a working demo. All four assignment scenarios and further infrastructure are unnecessary.

### Revised quantity and exact fresh approval

Existing `test_regeneration_supersedes_and_invalidates` already changed forecast from
1,000 to 1,200, producing a positive 1,000-unit requirement from an initial suggestion of
800. Extended that test through exact fresh approval, purchase and persisted validation.
Added `--acceptance-only` to the existing demo script rather than another demo framework.

Live API verification first ran against the existing Compose image on port 8003. It
confirmed the revised quantity, stale-approval protection and budget/storage/MOQ guards,
but exposed the old offline rationale's incorrect unconditional support statement.
The source was corrected without deploying or rebuilding that existing stack.
A temporary local source server verified the corrected implementation:

```bash
# From backend/, existing PostgreSQL instance and schema:
RAPPI_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi AGENT_LLM_PROVIDER=offline .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8004
# From repository root:
python scripts/demo_api.py --acceptance-only --base-url http://127.0.0.1:8004 --output acceptance_results.json
```

Result: passed. Initial suggestion 800; version 2 requires 1,000 units and MXN 12,500.
Execution before fresh approval returned 409 `invalid_state`; approval for the old quantity
returned 409 `approval_mismatch`; the old approval still returned 409 `stale_approval`
after a fresh approval existed. Exact fresh approval purchased 1,000 units and the
independent SQLite re-read validation passed. Results are in `acceptance_results.json`.
The temporary server is local verification, not a change to the Compose images.

### Blocked recommendations and frontend controls

- Budget: MXN 10,000 cost versus MXN 5,000 budget; `budget: fail`, decision `accept`,
  initial state `awaiting_approval`; execution returns 409 despite an API-recorded approval.
- Storage: 800 required versus 500 available; `storage: fail`, decision `accept`,
  initial state `awaiting_approval`; execution returns 409 despite an API-recorded approval.
- MOQ: 1,000 versus required 800; `investigate` / `needs_investigation`; approval returns
  409. Recommended quantity remains 800; no rounding/justified overbuying was implemented.
- Frozen `accept` semantics are preserved: accept the computed baseline, not purchase
  authorization or a guarantee of feasibility. Source explanations now explicitly say
  **Purchase blocked** for failed/unknown checks; the offline adapter no longer claims
  full constraint support and lowers confidence. Provider fallback text also states the block.
- Frontend executable checks now require all six required constraint checks to pass.
  Budget/storage proposals show **Purchase blocked**, with no purchase-approval control
  or “Approval is allowed” message; MOQ also withholds purchase approval. Warning status
  overrides a misleading approval-ready display. Component regressions cover all three.

Relevant checks actually run:

```bash
# backend/
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q tests/test_api_happy_path.py tests/test_api_constraints.py tests/agent
# frontend/
npm run typecheck
npm test
npm run build
```

Results: **56 backend/agent tests passed**, two existing dependency warnings; **30 frontend
tests passed**, typecheck and production build passed. The full 120-test suite was not
rerun during this bounded acceptance check; its result above belongs to the earlier run.
Current-source API acceptance also asserts blocked wording and absence of the previous
“fully supported” claim. No image rebuild, deployment or source-worktree edit occurred.
At the end of that acceptance task, Compose still served the prior build; source fixes
were then verified only in tests and a temporary API. This limitation is resolved by
the authorized local rebuild recorded below.

### README and submission-file review

README now documents the revised-quantity and budget/storage/MOQ acceptance command/table,
expected outcomes and conditional `accept` semantics. Confirmed it already explains actual
LangGraph responsibility, deterministic/remote LLM boundaries, unverified remote mode,
SQLite mock versus PostgreSQL workflow persistence, standard Compose and verified Linux
fallback, evaluation commands, demo flow and limitations. The external assignment review
resolves the former scope-artifact blocker. Visual browser verification remains outstanding.

Reviewed the union of tracked and untracked, nonignored files using
`git ls-files --cached --others --exclude-standard -z`, path/size checks and value-redacting
private-key/provider-token/secret-assignment patterns, plus inspection of key/config files.
An initial pattern overmatched blank example assignments; corrected the scanner to avoid
crossing line boundaries. Final scan: **130 submission files**, no secret-pattern findings,
no environment/runtime/dependency/cache paths, and no files over 1 MB. Intentional local
DB example credentials and dummy test keys remain; no real secret values were printed.
Lockfiles are intentional reproducibility files, not generated bulk to exclude.

Found and fixed one inclusion gap: `scripts/__pycache__/demo_api.cpython-314.pyc` was
untracked and eligible for submission. Root ignores now cover Python caches/bytecode;
root/backend/frontend ignores also cover environment variants while preserving `.env.example`.
Nothing was deleted. These existing local paths must stay excluded from submission:

- `backend/.venv/`, `frontend/node_modules/`, `frontend/dist/`;
- `backend/.pytest_cache/`, all backend `__pycache__/` directories, `scripts/__pycache__/`;
- `backend/mock-orders.sqlite3` and SQLite sidecars;
- real `.env` / `.env.*` files, if created; `.env.example` files remain included.

Git remains `feature/frontend` at `54f5983`, with all pre-existing and current edits
uncommitted. No commits, merges, pushes, deployments or source-worktree changes occurred.

## Authorized local rebuild and full regression — 2026-10-16

**Current verified state:** `rappi-hostcheck` now runs the latest source, including the
blocked-purchase presentation fixes. Dashboard: http://localhost:5175; API docs:
http://localhost:8003/docs. The preceding source-only acceptance limitation is resolved.

Commands actually run, from `/workspaces/rappi-frontend` unless stated:

```bash
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml ps
python scripts/demo_api.py --base-url http://127.0.0.1:8003 --output evaluation_results.json
python scripts/demo_api.py --acceptance-only --base-url http://127.0.0.1:8003 --output acceptance_results.json
# From backend/, isolated test database, separate from the running stack:
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q -o faulthandler_timeout=45
```

Actual results:

- Both images rebuilt successfully. The frontend image ran TypeScript checking and Vite
  production build and produced `/assets/index-BWI6ohe7.js`.
- Compose `up --build -d --wait` exited successfully. PostgreSQL remained running;
  backend/frontend containers were recreated. PostgreSQL and backend report healthy.
  Frontend is running; direct API health, nginx-proxied API health, HTML and the current
  JavaScript bundle all returned HTTP 200. The served bundle contains **Purchase blocked**.
- Full backend/agent suite: **120 passed, two existing dependency deprecation warnings,
  49.72 seconds**. This is a fresh full-suite run after the acceptance source fixes.
- Normal live demo passed: approved 800 / MXN 10,000 independently validated; duplicate
  execution returned the same PO; persisted 500-unit mismatch recorded quantity/cost
  discrepancies and blocked repurchase; missing/stale approvals refused; missing budget
  escalated. `evaluation_results.json` was refreshed with this run's results.
- Acceptance-only live demo passed on the rebuilt port 8003 service: initial 800 became
  required 1,000 / MXN 12,500; no fresh approval, old-quantity approval and stale approval
  were refused; exact new approval purchased 1,000 and independent validation passed.
  Budget/storage executions and MOQ approval were blocked. All three blocked rationales
  explicitly state **Purchase blocked** and omit the prior unconditional support claim.
  `acceptance_results.json` was refreshed with this run's results.

Volume/data preservation was verified before and after the rebuild:

- Mounts retained `rappi-hostcheck_postgres_data` and `rappi-hostcheck_mock_data`.
- PostgreSQL counts: cases **8 → 16**, purchase orders **3 → 6**, validations **3 → 6**,
  matching eight new demo cases and three new purchased/validated orders.
- Independent SQLite mock orders: **3 → 6**. A historical 800-unit mock order was re-read
  successfully after restart, and historical PostgreSQL order
  `8179e13c-daf4-4d88-b974-14b826b2db98` still exists.
- SQL grouping across all purchase orders found a maximum of **one order per idempotency
  key**. Only the isolated test database was truncated by pytest fixtures.
- No `down`, volume deletion, reset, clean or source-worktree edit was performed.

Read-only verification used `docker inspect` for mount names, `psql` SELECT counts and
historical-order existence, `sqlite3` SELECT counts inside the backend, and Python
`urllib.request` HTTP reads. No secret values were printed.

### Browser walkthrough on port 5175

1. Open http://localhost:5175 and hard-refresh to load the new bundle. In Codespaces,
   open the forwarded **5175** port. Confirm the **Live API** badge.
2. Wait for the fresh case, click **Load sample evidence**, then **Run investigation**.
3. Verify initial suggestion and calculated requirement are both **800**, the supplier
   is Distribuidora Norte, total cost is **MXN 10,000**, and all six checks pass.
4. In **Buyer decision**, keep **Review quantity** at **800**. Optionally enter **900**
   first to confirm approval is disabled, then restore **800**. Click **Approve purchase**.
5. Confirm the persisted approval shows the exact version/quantity/cost. Click
   **Execute approved purchase**. The created PO must still await independent validation.
6. Click **Run independent validation**. Expect **Validated**, a passed validation result,
   no mismatches, and the persisted mock snapshot showing **800** / **MXN 10,000**.

The live UI has no case picker or live fault/scenario selector; CLI demos cover revised
quantity and blocked/mismatch cases. Refreshing creates a new case while preserving old
records. Visual browser execution remains outstanding; HTTP and served-bundle checks
were run. Remote LLM operation remains unverified and was not required for these offline
runs. No further source changes were needed in this rebuild task.

Files updated this task: `evaluation_results.json`, `acceptance_results.json`, this summary,
append-only `DEVELOPMENT_LOG.md`, and latest-only `RUN_SUMMARY.md`.
Git remains `feature/frontend`, unchanged HEAD `54f5983`, with existing/new work uncommitted.
No commit, merge, push or remote deployment occurred.

## Browser blocker: confirmed sample request mismatch — 2026-10-16

Reproduced the user's failure through **http://127.0.0.1:5175**, executing the actual
`SAMPLE_EVIDENCE`, `DEFAULT_CASE` and `createHttpApi` TypeScript modules with vite-node.
The unmodified adapter sent the response-shaped fixture verbatim:

| Request before fix | Actual status / details |
|---|---|
| `POST /api/v1/cases` | 201; case created without evidence |
| `PUT /api/v1/cases/{id}/evidence` | 422 `validation_error`; `body.incoming_orders[0].incoming_order_id`: `extra_forbidden`; `body.supplier_offers[0].supplier_offer_id`: `extra_forbidden`; both messages: “Extra inputs are not permitted” |
| `POST /api/v1/cases/{id}/recommendations` with `{}` | 409 `missing_evidence`, because the previous request never saved evidence |

**Root cause:** frontend evidence types/fixtures included server-generated response IDs,
while backend `IncomingOrderRequest` and `SupplierOfferRequest` correctly forbid extra
request fields. The investigation body itself matches `RecommendationRequest`; it was
not the schema mismatch. Prior CLI demos used a separate Python fixture without these
IDs, while mock UI tests did not cross the strict backend boundary.

Smallest confirmed fix and changed source/test files:

- `frontend/src/api/httpApi.ts`: omit only the nested response IDs when serializing PUT
  evidence; leave fixture objects intact. Backend schemas and strict validation unchanged.
- `frontend/src/workflow/state.ts`: new empty cases display **Evidence needed**, with
  truthful instructions to load evidence, rather than claiming evidence was submitted.
- `frontend/src/App.tsx` and `frontend/src/workflow/usePurchasingWorkflow.ts`: disable
  investigation and guard its handler until successfully persisted/refreshed evidence exists.
- `frontend/src/api/httpApi.test.ts`: regression using the real sample fixture checks the
  wire payload omits IDs and the fixture remains unchanged.
- `frontend/src/App.test.tsx`: HTTP-mode tests cover the initial empty banner, disabled
  investigation, successful load/investigation, and continued blocking after failed loading.
- `backend/tests/test_frontend_contract.py`: runs the actual frontend fixture/adapter via
  vite-node, validates generated requests against Pydantic, and exercises case → evidence
  → recommendation through FastAPI. It also asserts the original response-shaped fixture
  still gets 422 at exactly the two reported fields. Requires frontend Node/dev tools;
  explicitly skips when unavailable. Both tests ran successfully here, without skips.
- README documents the cross-language regression/setup. `proxy_verification_results.json`
  preserves the actual before/after request bodies, statuses and field-level errors.

Commands/checks actually run:

```bash
# Before source edits; runner imports real frontend fixture and adapter:
frontend/node_modules/.bin/vite-node /tmp/rappi-proxy-repro.ts
# frontend/
npm run typecheck
npm test
npm run build
# backend/, isolated test database:
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q -o faulthandler_timeout=45
# repository root:
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
frontend/node_modules/.bin/vite-node /tmp/rappi-proxy-verify.ts
```

The two temporary TypeScript runners intercept fetch only to record requests; requests
are sent to the real port 5175 proxy. The second additionally asserts persisted evidence,
800 recommended units, all passing checks, and HTTP statuses `201,200,200,201`.
Results: **122 backend/agent tests passed**, two existing warnings, 43.80 seconds;
**33 frontend tests passed**, typecheck and build passed. An initial frontend run had two
ambiguous banner-text queries; narrowed them to the exact banner and reran successfully.
An initial npm command from repository root found no package; correct frontend-directory
commands above passed. No tests or backend validation were weakened.

Checked for existing Docker commands before rebuilding; none were active. One coordinated
Compose build/start/wait completed successfully. PostgreSQL and backend stayed running;
frontend was recreated. Both named volumes remain mounted unchanged:
`rappi-hostcheck_postgres_data` and `rappi-hostcheck_mock_data`. No down/reset/volume deletion.

**After rebuilding**, actual frontend-generated requests through port 5175 passed:
case creation **201** → sample evidence **200** → case refresh with persisted evidence
**200** → investigation **201**, 800 units and all checks passing. Frontend HTML serves
`/assets/index-etRiVspY.js`; HTML/bundle/proxied API health returned HTTP 200 and the served
bundle contains **Evidence needed**. Database/backend report healthy; frontend is running.

Hard-refresh http://localhost:5175, then click **Load sample evidence** and
**Run investigation** once it becomes enabled. Manual browser interaction was not performed
here; actual proxy requests and HTTP-mode component behavior were verified.

Updated this summary, README, append-only DEVELOPMENT_LOG.md and latest RUN_SUMMARY.md.
All prior work and source worktrees preserved; no commit, merge, push or remote deployment.

## User-confirmed browser success and mismatch walkthrough — 2026-10-16

The user confirmed the live browser happy path reached **Passed** validation with matching
approved/persisted fields. This resolves the happy-path manual browser verification item.
For the next demonstration, implementation inspection confirms POST
`http://127.0.0.1:8003/mock/v1/_control` accepts `create_mode`, `read_mode` and `mismatch`.
A new port-5175 tab creates a fresh case. Prepare/approve its 800-unit recommendation,
set `{ "create_mode": "ok", "read_mode": "ok", "mismatch": "quantity" }` immediately
before execution, wait for order creation, then restore the same fields with
`"mismatch": "none"` before validation. Settings affect new creates globally within the
backend process, so do not execute other cases in that interval. Restore even if aborting.
Restoration does not change the stored order: validation reads 500 units / MXN 6,250
against approved 800 / MXN 10,000, persists discrepancies and validation_failed, and shows
buyer intervention required. Further new execution is refused; an original-key replay
only returns the existing operation. Escalation is the persisted failure and UI warning.
No fault commands, purchases, tests, rebuilds or deployment were run for this guidance;
completed happy-path records and data volumes were left untouched. No new features added.

## Online-inference requirement update — implementation ready, live proof pending

**Final online acceptance is not complete. No real online inference succeeded or was
attempted in this run: provider/model and credentials are absent.** Process settings,
private env-file existence and the running backend were checked without printing keys.
The user was asked asynchronously for their provider and exact accessible model, with
instructions to configure the key privately. No model was guessed and no key was created.

Supported implementation: `openai_compatible` uses Chat Completions JSON-object mode at
OpenAI's default endpoint or a compatible configured base URL; `offline` is the labelled
deterministic fallback. There are no native Anthropic or Gemini adapters. A given custom
service/model's compatibility still needs real verification. Required online environment
names are `AGENT_LLM_PROVIDER`, `AGENT_LLM_MODEL`, `AGENT_LLM_API_KEY`; optional settings
are `AGENT_LLM_BASE_URL`, `AGENT_LLM_TIMEOUT_SECONDS`, and `AGENT_OFFLINE`.

Implemented within the existing architecture:

- The model receives the actual case evidence (inventory, forecast, safety, incoming dates/
  statuses/quantities, supplier offers, budget/storage) and deterministic results with check
  values, limits, messages, units and assumptions. It supplies structured interpretation,
  key evidence, trade-offs, caveats and confidence only. No purchase authority was added.
- Strict explanation schema forbids extra/executable fields, wrong types and overly long
  summaries; the remote adapter rejects empty summaries, malformed/truncated/refused
  responses. Numeric validation remains in place. Timeouts/provider/validation failures
  return an explicitly labelled deterministic fallback with safe exception-type diagnostics.
- Requested and actual inference provenance is persisted with the existing graph trace,
  along with the structured explanation. A model-reported identity is used when available;
  otherwise the configured request model is retained. Keys/base-URL credentials are never
  included in provenance. Only endpoint host is recorded as online provider identity.
- `generated_by=agent` now requires a validated online result. Provider configuration alone
  no longer marks a failed or fallback explanation as online. Runtime modes are `online`,
  `offline_fallback`, and `provider_fallback`, with explicit `online_succeeded`.
- The dashboard displays actual explanation provider/model and requested provider/model
  on fallback, structured interpretation, warnings and a safe fallback reason. It labels
  success only when both online mode and success flag agree. Historical versions without
  metadata show status unavailable rather than claiming online inference.
- Compose and env examples now make the final demo's primary setting `openai_compatible`,
  with model/key blank until privately configured; missing required settings fail startup.
  Python's credential-free library default and CI remain offline. The test fixture forces
  offline unless an individual mocked test explicitly opts into an online adapter.
- Purchasing arithmetic, supplier/constraint evaluation, approval, idempotency, execution,
  reconciliation and independent persisted-result validation remain authoritative backend code.

Changed source/config/test files: backend agent models/config/state/service/nodes,
`agent/llm/openai_compatible.py`, `agent/explanation.py`, `agent/adapters/backend.py`;
`tests/agent/test_online_inference.py`, `tests/test_integration.py`, `tests/conftest.py`;
frontend `api/types.ts`, `components/RationalePanel.tsx` and its new test;
`compose.yaml`, root/backend `.env.example`, README and this run's records.
No database migration or dependency additions were needed.

Checks actually run:

```bash
# backend/
.venv/bin/python -m pytest -q tests/agent
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q -o faulthandler_timeout=45
# frontend/
npm run typecheck
npm test
npm run build
# repository root: explicit fallback, NOT the final online demo
AGENT_LLM_PROVIDER=offline docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
frontend/node_modules/.bin/vite-node /tmp/rappi-llm-verify.ts
```

Results: **57 agent tests passed**; **134 full backend/agent tests passed**, two existing
warnings, 36.03 seconds; **36 frontend tests passed**, typecheck/build passed. Mocked online
success, actual prompt evidence, timeout, HTTP failure, bad JSON, malformed types,
unauthorized fields, empty content, truncation, unsupported numbers, persistence and UI
online/fallback labels are covered without real credentials. These are mocked provider
results and do not prove real online inference.

The explicit fallback Compose rebuild/start/wait succeeded. Database/backend report
healthy and frontend is running. Existing mounts retain `rappi-hostcheck_postgres_data`
and `rappi-hostcheck_mock_data`; no down/reset/volume deletion. The live port-5175 proxy
verification created a fresh case and generated 800 / MXN 10,000 with persisted
`offline_fallback`, `online_succeeded=false`, actual provider `offline`, model null, and
structured interpretation. `llm_verification_results.json` records this truthfully.
No purchasing or fault-control mutations were performed for that verification.

The new provenance panel was component-tested and built, but not visually driven in a
browser. The previous user-confirmed happy-path browser test predates this new panel.
Real online inference and its live dashboard verification remain pending credentials.
Configure the key only in the ignored root `.env` (never chat or a tracked/example file),
set the exact accessible model and compatible provider/base URL, then run the normal
Compose command without the temporary offline prefix. A fallback result cannot satisfy
this acceptance requirement. See README for private setup and the online verification steps.

Official API protocol/schema guidance was checked through the OpenAI Docs skill:
https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat
JSON mode is followed by local schema validation; no claim of schema enforcement by JSON
mode alone is made. No commits, merges, pushes, source-worktree edits or remote deployment.

Follow-up verification in the same online-integration task: added an explicit mocked
provider-refusal test, then reran the full isolated suite with the same command above:
**135 passed**, two existing warnings, **40.73 seconds** (supersedes 134 as the latest
full-suite count). A first attempt to append that test used a wrong relative path and
started a restricted run; no file was changed by that append and the restricted run was
interrupted before API tests. The correct append and PostgreSQL-enabled run succeeded.
Only test code changed after the image rebuild; no runtime source changed. The real
online-inference requirement remained pending provider/model and private credentials at
that earlier run.

## Groq live inference attempt and rebuild — 2026-10-16

The user configured the root ignored `.env`. The key was checked for presence only and
never printed. Rebuilt/restarted the existing stack while preserving volumes:

```bash
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
```

The command succeeded. Backend and database were healthy; frontend was running. Health
returned HTTP 200 on `http://127.0.0.1:8003/api/v1/health` and through
`http://127.0.0.1:5175/api/v1/health`. The original `rappi-hostcheck_postgres_data` and
`rappi-hostcheck_mock_data` volume creation times remained `2026-10-16T04:35:48Z`.

One fresh live recommendation was made with `frontend/node_modules/.bin/vite-node
/tmp/rappi-groq-live-verify.ts`, using the actual frontend HTTP adapter and proxy at port
5175. Case `282e322f-2397-4000-a783-9797fe6d876f`, version 1, retained the deterministic
accept decision, quantity 800, and MXN 10,000 cost. Groq was called with requested model
`openai/gpt-oss-120b`; its returned explanation contained unsupported numbers in summary
and trade-off sentences, so local output validation rejected it. Persisted metadata reports
`provider_fallback`, `online_succeeded=false`, and `generated_by=offline`. Thus the real
request reached Groq, but successful online inference was **not** achieved. The dashboard
must not present this as online success. The validation warning and provenance were saved
in `llm_verification_results.json`. No purchase was executed and no second model call was
made. Browser visual verification was not performed; the API call did traverse the live
frontend proxy.

## Final packaging and Groq acceptance — 2026-10-16

A private diagnostic request using the standard sample facts showed the prior numeric
rejection was mixed: the old regex split evidence-supported comma-grouped thousands into
separate tokens, and a trade-off also contained an unsupported grouped amount. Raw model
text was captured temporarily with mode `0600`, inspected in-process, and deleted. No raw
text or credentials were printed or committed.

The smallest correction normalizes standard thousands separators before exact Decimal
comparison and instructs the model to use exact plain digits and qualitative trade-offs.
New tests cover both accepted evidence-equivalent formatting and rejected unsupported
grouped values. Verification after the correction:

- `cd backend && .venv/bin/python -m pytest -q tests/agent/test_explanation_guard.py tests/agent/test_online_inference.py`: **22 passed**, one LangGraph deprecation warning.
- `cd backend && RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test .venv/bin/python -m pytest -q -o faulthandler_timeout=45`: **137 passed**, two dependency deprecation warnings, 37.30 seconds.
- Rebuilt with the standard host-network command above; backend, database, and frontend became healthy. Existing PostgreSQL/mock-order volume timestamps remained unchanged.
- One final live request through the actual `frontend/node_modules/.bin/vite-node /tmp/rappi-groq-live-verify.ts` adapter/proxy path succeeded: HTTP recommendation created, provider `api.groq.com`, actual model `openai/gpt-oss-120b`, mode `online`, `online_succeeded=true`, `generated_by=agent`, 800 units / MXN 10,000. No purchase was executed. Earlier fallback result remains in `docs/evaluation/llm_verification_results.json` alongside the accepted result.
- `git diff --check` and evaluation JSON parsing passed. Frontend source behavior was unchanged; its previous 36-test, typecheck and production build results remain applicable.

Submission layout moves supporting contracts, plan, history and evaluation artifacts into
`docs/`; code remains in `backend/`, dashboard in `frontend/`, and demo/evaluation script
in `scripts/`. Root README now leads with purpose, quick start, Groq configuration, fallback,
browser success/mismatch walkthroughs, architecture, actual test results and limitations.
