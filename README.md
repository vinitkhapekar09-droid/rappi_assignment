# Rappi AI Purchasing Agent

A buyer-facing purchasing workflow that investigates an initial 800-unit suggestion, explains the evidence, records exact approval, executes one idempotent mock purchase, and independently validates the order persisted by a separate mock service.

## Implemented scope

- FastAPI, PostgreSQL, SQLAlchemy/Alembic, LangGraph, and a React/Vite dashboard.
- Deterministic requirement, supplier, lead-time, MOQ, budget, storage, versioning, approval, idempotency, execution, reconciliation, and validation rules.
- Groq online interpretation through the existing OpenAI-compatible Chat Completions adapter. Model output is untrusted and locally schema/numeric validated. Failed validation is shown as an explicit offline fallback.
- Independent SQLite storage for mock purchase orders; PostgreSQL stores workflow and approval records.
- Reproducible happy-path, approval-guard, constraint, duplicate, and persisted-mismatch demos.

## Quick start

Copy the example and privately edit the root `.env` before starting. Never put a real key in chat, source control, or an example file.

```bash
cp -n .env.example .env
# Set the Groq API key privately in .env; keep the documented provider/model/base URL.
docker compose up --build -d --wait
```

Open the dashboard at <http://localhost:5173> and API docs at <http://localhost:8000/docs>. This environment also uses a verified Linux host-network fallback on separate ports and volumes:

```bash
docker compose -p rappi-hostcheck -f compose.yaml -f compose.host.yaml up --build -d --wait
```

That dashboard is <http://localhost:5175>; its API is <http://localhost:8003/docs>. The stack binds to loopback and preserves its named database volumes across rebuilds. Do not use `down -v` when retaining demo data.

## Groq configuration and fallback

The root `.env.example` contains these names and safe defaults:

```dotenv
AGENT_LLM_PROVIDER=openai_compatible
AGENT_LLM_BASE_URL=https://api.groq.com/openai/v1
AGENT_LLM_MODEL=openai/gpt-oss-120b
AGENT_LLM_API_KEY=
AGENT_LLM_TIMEOUT_SECONDS=30
AGENT_OFFLINE=false
```

Groq documents that base URL and lists `openai/gpt-oss-120b` as a production model with strict Structured Outputs support. The adapter requests JSON object output and applies strict local Pydantic validation plus a numeric evidence guard. The model may interpret evidence and explain trade-offs; deterministic backend code alone sets quantities/costs and controls approval, execution, and validation. Compose passes the provider, base URL, model, and key to FastAPI.

For a credential-free fallback, run Compose with `AGENT_LLM_PROVIDER=offline` or set it in the private `.env`. The dashboard labels this **Offline fallback — no successful online inference**. Provider failures or invalid output use the same explicit fallback. Do not treat fallback as a successful online demo; before approving a purchase, confirm the dashboard says **Online LLM inference succeeded** and shows Groq plus the actual model.

Official references: [Groq OpenAI compatibility](https://console.groq.com/docs/openai), [Structured Outputs](https://console.groq.com/docs/structured-outputs), and [supported models](https://console.groq.com/docs/models).

## Browser demo

1. Open the dashboard and confirm it is using the live API. Load sample evidence, then run investigation.
2. Review the evidence and six deterministic checks. For the standard fixture, the baseline and recommended quantity are 800 units and total cost is MXN 10,000.
3. Confirm successful online inference is shown. If it shows fallback, the recommendation remains safe to inspect but this run does not prove online inference.
4. Approve the exact displayed recommendation version, supplier, quantity, and cost. Execute once, then run independent validation.
5. The happy path shows `Passed validation` when independently persisted fields match the approved request.

### Persisted-mismatch demo

Use a fresh case; retain any completed happy-path case. The existing mock fault control is global for that backend process. Set it after approval and immediately before the one execution, then restore it as soon as the order is created and before validation:

```bash
curl -fsS -X POST http://127.0.0.1:8003/mock/v1/_control \
  -H 'Content-Type: application/json' \
  -d '{"create_mode":"ok","read_mode":"ok","mismatch":"quantity"}'
# Execute the approved purchase once in the dashboard.
curl -fsS -X POST http://127.0.0.1:8003/mock/v1/_control \
  -H 'Content-Type: application/json' \
  -d '{"create_mode":"ok","read_mode":"ok","mismatch":"none"}'
# Run validation in the dashboard.
```

The approved request is 800 units / MXN 10,000. The mock persists a different quantity and cost (the current quantity fault persists 500 / MXN 6,250). Validation independently reads the mock service's persisted order details, reports both values, sets `validation_failed`, escalates for human review, and blocks another execution. The create response and approved request are **requested** values; they do not prove what was **persisted**. Keep other demos/users off this local instance while the global fault is enabled, and restore normal controls even if you stop the demo early.

The same successful and mismatch scenarios can be run from a terminal without browser interaction:

```bash
python3 scripts/demo_api.py --base-url http://127.0.0.1:8003 --output /tmp/rappi-demo.json
```

The script creates fresh cases, proves one order despite duplicate execution, validates the happy path, forces and restores a persisted mismatch, and checks approval guards. It never resets or deletes data. Run only against an isolated demo instance because the mock fault control is global.

## Architecture

```mermaid
flowchart LR
  UI[React dashboard] --> API[FastAPI]
  API --> GRAPH[LangGraph investigation and explanation]
  GRAPH --> DOMAIN[Deterministic purchasing domain]
  GRAPH --> LLM[Groq interpretation or labelled fallback]
  API --> PG[(PostgreSQL workflow and approvals)]
  API --> GUARD[Approval and idempotent execution guards]
  GUARD --> MOCK[Mock purchasing API]
  MOCK --> SQLITE[(Independent SQLite order store)]
  API --> VALIDATE[Independent persisted-order validation]
  VALIDATE --> MOCK
  VALIDATE --> PG
```

For every SKU, required quantity is `max(0, forecast + safety stock - usable inventory - confirmed incoming supply within the inclusive seven-day horizon)`. Eligible arrival dates are start through start + 6 days; supplier lead time must fit. MOQ never silently rounds quantity upward. Missing evidence and failed constraints block approval/execution as applicable. See [API contract](docs/API_CONTRACT.md), [database design](docs/DATABASE_SCHEMA.md), and [architecture/decision history](docs/INTEGRATION_SUMMARY.md).

## Evaluation

Install dependencies using the pinned files in `backend/uv.lock` and `frontend/package-lock.json`. Run the backend suite against an isolated PostgreSQL test database (never point it at a database containing demo work):

```bash
cd backend
uv sync --frozen --extra dev --python 3.12
RAPPI_TEST_DATABASE_URL=postgresql+psycopg://rappi:rappi@localhost:55432/rappi_integration_test \
  .venv/bin/python -m pytest -q -o faulthandler_timeout=45
```

Frontend checks:

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

Acceptance-only API scenarios (revised requirement, fresh approval, budget, storage, and MOQ):

```bash
python3 scripts/demo_api.py --acceptance-only --base-url http://127.0.0.1:8003 --output /tmp/rappi-acceptance.json
```

Latest verified checks: **137 backend/agent tests passed** with two dependency deprecation warnings. The frontend's **36 tests**, typecheck, and production build passed before this final backend-only correction; frontend source did not change. The live persisted-mismatch and acceptance scenarios are recorded under [`docs/evaluation/`](docs/evaluation/). The final live Groq request through port 5175 passed output validation: provider `api.groq.com`, model `openai/gpt-oss-120b`, `online_succeeded=true`, 800 units / MXN 10,000. The earlier fallback and the accepted response are both recorded in [`llm_verification_results.json`](docs/evaluation/llm_verification_results.json).

## Limitations

- No authentication, real supplier ordering, payments, or multi-tenant controls; buyer identity is demo data.
- Mock fault controls are process-global. Run one backend process and an isolated local demo.
- Numeric validation prevents unsupported numeric claims but is not a semantic fact checker. Model prose cannot alter deterministic order fields.
- The dashboard does not restore a previous case after refresh. The app has no durable LangGraph checkpoint; PostgreSQL is the workflow source of truth.
- `AGENT_OFFLINE=true` and an unavailable or rejected provider result produce fallback, never online-success status.

## Future improvements

These are not implemented in this demo:

- Authenticate buyers and enforce role-based approval permissions.
- Check evidence freshness and resume an existing case after a dashboard refresh.
- Scope mock fault controls to individual cases instead of the process.
- Add broader supplier-shortfall and forecast-change scenarios.
- Expand LLM evaluation across constrained cases and provider failures.

Supporting planning, API, schema, integration, and development-history documents are in [`docs/`](docs/).
