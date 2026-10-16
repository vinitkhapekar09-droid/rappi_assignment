# API Contract — AI Purchasing Agent

Status: **implemented in the integration workspace**. This document freezes the REST
contract for the first end-to-end purchasing workflow so backend, agent, and frontend
workers can build against one interface.

Companion document: `DATABASE_SCHEMA.md`. The two must stay consistent.

Source of truth: `PROJECT_PLAN.md` and `AGENTS.md`.

---

## 1. Scope

This contract covers exactly one workflow instance:

1. Create a purchasing case (one SKU, fixed seven-day planning horizon).
2. Supply or update the case evidence (inventory, forecast demand, incoming orders,
   supplier offers, MOQ, lead time, unit price, budget, storage).
3. Run the investigation and generate an immutable, versioned recommendation.
4. Retrieve the recommendation with its evidence, assumptions, missing information,
   constraint checks, and decision rationale.
5. Buyer approves or rejects a specific recommendation version.
6. Execute the approved purchase through the mock purchasing API.
7. Independently validate the persisted purchase order against the approved
   recommendation.
8. Retrieve execution and validation results.

Out of scope for this contract: authentication/authorization beyond a demo buyer
identity, multi-SKU carts, multiple concurrent recommendations per case, real supplier
or payment integrations, and pagination/listing beyond what is listed here.

---

## 2. Conventions

- Base path: `/api/v1`.
- Content type: `application/json` for all request and response bodies.
- Identifier fields: UUID v4 strings. Recommendation versions are additionally
  identified by a monotonically increasing `version_number` (integer, starting at 1)
  that is unique within a case.
- The API surfaces recommendation versions by `version_number` (e.g.
  `current_recommendation_version`), while the database stores a UUID pointer
  (`current_recommendation_version_id`). This is an intentional representation mapping.
- Timestamps: ISO-8601 UTC strings, e.g. `2026-10-15T12:00:00Z`.
- Dates (planning anchors, arrival dates): ISO-8601 calendar dates, e.g. `2026-10-16`.
- Quantities: integers (whole units).
- Monetary amounts and storage volumes: JSON **strings** (decimal, e.g. `"12.50"`) to
  avoid floating-point ambiguity. The database stores them as `NUMERIC`.
- Field naming: `snake_case`.
- Currency is fixed per case (`currency`, ISO-4217). A supplier offer must match it.
- The storage unit is fixed per case (`volume_unit`). Capacity and per-unit consumption
  use the same unit.
- The planning horizon is fixed at seven days. Exact day semantics are in §2.6.

### 2.1 Error format

All non-2xx responses use a single shape:

```json
{
  "error": {
    "code": "stale_approval",
    "message": "The approval is tied to recommendation version 2, but version 3 is current.",
    "details": { "approved_version": 2, "current_version": 3 }
  }
}
```

`code` is a stable machine-readable string. Known codes:

| code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | Request schema/values invalid |
| `not_found` | 404 | Case, recommendation, or PO does not exist |
| `invalid_state` | 409 | Action not allowed in the current case state |
| `missing_evidence` | 409 | Decision-critical evidence absent/contradictory |
| `recommendation_superseded` | 409 | The targeted version is no longer current |
| `stale_approval` | 409 | No valid approval for the current recommendation version |
| `approval_mismatch` | 409 | Approval request details differ from the targeted version |
| `idempotency_conflict` | 409 | Same idempotency key used with a different request |
| `execution_in_progress` | 409 | Duplicate request while the first attempt is unresolved |
| `execution_failed` | 502 | Mock purchasing service failed |
| `execution_uncertain` | 503 | Execution outcome could not be confirmed |
| `mock_unavailable` | 502 | Persisted PO could not be re-read for validation |

### 2.2 Idempotency and safe execution recovery

`POST /cases/{case_id}/purchases` **requires** an `Idempotency-Key` request header
(client-generated UUID). The key is the single source of truth for duplicate prevention:

- The mock purchasing service is called with the same key and is itself **idempotent on
  that key**: a repeated create with the same key returns the already-persisted PO and never
  stores a second one. Reconciliation relies on the mock's read/create-by-key behaviour.
- The database enforces `purchase_orders UNIQUE (idempotency_key)` and
  `idempotency_keys.key` as backstops.
- The `request_fingerprint` is a hash of method, path, and canonical request body.
- A repeated request must **never** create a second purchase order.
- Before any create or re-create, the request must **atomically claim** the key with a
  conditional update, so two concurrent recoveries cannot both call create:

  ```
  UPDATE idempotency_keys
     SET state = 'in_progress', updated_at = now()
   WHERE key = :key
     AND action = 'execute_purchase'
     AND (state = 'uncertain'
          OR (state = 'in_progress' AND updated_at < now() - :lease));
  ```

  Only the request that changes one row (rowcount = 1) proceeds to create. Every other
  concurrent request sees a fresh `in_progress` row and returns `409 execution_in_progress`.
  A fresh key is claimed with `INSERT ... ON CONFLICT DO NOTHING`, which is atomic in the
  same way.

State machine for a request `(key, fingerprint)`:

| Stored state | Fingerprint | Behaviour |
|---|---|---|
| none | — | Insert key `in_progress` (owner = this request) and proceed to one create attempt |
| any | different | `409 idempotency_conflict` — no attempt |
| `completed` | same | `200 OK` replaying the stored response body — no attempt |
| `failed` | same | `200 OK` replaying the stored terminal outcome (`outcome: "failed"`, stored `error_code`) — no attempt |
| `uncertain` | same | Reconcile against the mock by key (§ below) before anything else |
| `in_progress`, lease fresh | same | `409 execution_in_progress` (concurrent duplicate) — no attempt |
| `in_progress`, lease expired | same | Reconcile against the mock by key (§ below) — no blind attempt |

A lease is considered expired when `idempotency_keys.updated_at` is older than the
configured lease timeout (default 60 s). The lease exists only to recover from a crashed
worker; it never authorizes skipping reconciliation.

Reconciliation (for `uncertain`, or an expired `in_progress`), in this exact order:

1. Query the mock purchasing service by idempotency key (read-only).
2. Mock has a PO for the key: mirror it, set the key `completed`, move the case to
   `validating`, and return the reconciled result (`200 OK`). **No new create.**
3. Mock confirms no PO exists for the key: the create is safe to (re)attempt, but only
   after the atomic claim above succeeds. The claimant sets the key `in_progress`,
   increments `attempt_number`, and calls create **once**. Concurrent requests that lose the
   claim see a fresh `in_progress` row and return `409 execution_in_progress`; they never
   call create. This is the only permitted re-attempt, and it follows a confirmed
   reconciliation — never a blind retry.
4. Mock cannot be reached: leave the key `uncertain`, return `503 execution_uncertain`,
   and keep the case blocked from automatic purchasing. Do not create.

Two independent guards prevent duplicate POs: (a) the atomic claim ensures at most one
request is creating for a key at a time, even across expired-lease recovery; (b) even if a
create somehow races, `purchase_orders UNIQUE (idempotency_key)` persists at most one PO
and the mock's own key-idempotency returns the existing record. The loser reads and returns
the winner's row instead of inserting.

`POST .../decision` is naturally guarded: at most one active approval exists per
recommendation version, so a repeated approval returns the existing approval.

### 2.3 Recommendation versioning

- Generating a recommendation always creates a new immutable `version_number`.
- A newly generated version supersedes the previous one. Superseded versions remain
  readable but can no longer be approved or executed.
- Relevant case evidence changes (via `PUT .../evidence`) supersede the current
  recommendation and invalidate any approval tied to it.
- Only the case's **current** version may be approved or executed.

### 2.4 Stale-approval rejection

Execution requires an active approval whose `recommendation_version_id` equals the
case's current recommendation version, whose `invalidated_at` is null, and whose approved
purchase details match the version. Otherwise execution returns `409 stale_approval`.
An old approval can never authorize a newer recommendation.

### 2.5 Purchase validation outcomes and representation

- Validation is a **separate, explicit step**. The execution endpoint creates the
  purchase order and moves the case to `validating`; it does **not** declare success.
- The validate endpoint independently re-reads the persisted purchase order from the mock
  purchasing API (not from the execution response) and compares it with the approved
  recommendation and constraints. The read is always by the stored `external_po_id` /
  idempotency key.
- A mismatch is data, not a transport error. `200 OK` is returned whenever the validation
  step itself ran; the outcome is in `status`.

Outcome matrix — persistence and case state after the validate call:

| `status` | HTTP | Validation result persisted? | PO status | Case status | Blocked? |
|---|---|---|---|---|---|
| `passed` | `200` | yes (`validation_results`, `mismatches = []`) | `validated` | `validated` | no |
| `failed` | `200` | yes (`mismatches` non-empty) | `validation_failed` | `validation_failed` | yes — buyer intervention; no auto-retry |
| `inconclusive` | `200` | yes (mock reachable but PO missing/unreadable) | stays `created` | stays `validating` | yes — reconcile, re-validate allowed |
| transport failure | `502 mock_unavailable` | **no** (source could not be read) | stays `created` | stays `validating` | yes — reconcile, re-validate allowed |

Rules:

- A `200` never means "purchase verified"; only `status: "passed"` does.
- A transport failure is the only case where no validation row is written, because there
  was nothing trustworthy to record.
- While the case is `validating` (blocked), execution for the action is refused; a new
  create attempt requires the idempotency reconciliation path in §2.2, not a blind retry.
- Validation is idempotent for terminal outcomes (`passed`/`failed`): repeating the call
  returns the stored result (`200 OK`) and writes no new row. An `inconclusive` result does
  not finalize the PO, so re-validation is allowed.

Failing example (`200 OK`):

```json
{
  "validation_result_id": "99999999-9999-9999-9999-999999999999",
  "purchase_order_id": "55555555-5555-5555-5555-555555555555",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "recommendation_version_number": 1,
  "status": "failed",
  "checked_fields": { "quantity": 800, "supplier_name": "Distribuidora Norte", "total_cost": "10000.00" },
  "mismatches": [
    { "field": "quantity", "approved_value": 800, "persisted_value": 500 }
  ],
  "persisted_snapshot": { "external_po_id": "MOCK-PO-7781", "quantity": 500, "supplier_name": "Distribuidora Norte", "total_cost": "6250.00" },
  "created_at": "2026-10-15T12:08:00Z"
}
```

- On `failed`, automatic purchasing stops for that action and buyer intervention is
  required. No automatic retry or silent correction.

### 2.6 Seven-day horizon semantics

The planning horizon is always seven consecutive days:

- `planning_horizon_start` — the first day of the window, inclusive.
- `planning_horizon_end` — `planning_horizon_start + (planning_horizon_days - 1)` =
  start + 6 days, inclusive (seven days total).
- Forecast demand and safety stock cover exactly this window.
- An incoming order is **eligible supply** iff `status == "confirmed"` **and**
  `planning_horizon_start <= expected_arrival_date <= planning_horizon_end`.
- A supplier offer is **eligible** iff `is_available == true` **and**
  `lead_time_days <= planning_horizon_days - 1` (≤ 6 calendar days), so an order placed on
  `planning_horizon_start` arrives by `planning_horizon_end`. This matches the
  `expected_arrival_date <= planning_horizon_end` rule above.
- `lead_time_days` and `expected_arrival_date` are calendar-day based.

The same window definition is used in the formula (§5.4), examples, tests, and
`DATABASE_SCHEMA.md`.

---

## 3. Resource model (summary)

| Resource | ID | Notes |
|---|---|---|
| Purchasing case | `case_id` (UUID) | Aggregate root; holds status and evidence |
| Incoming order | `incoming_order_id` (UUID) | Confirmed/unconfirmed supply lines |
| Supplier offer | `supplier_offer_id` (UUID) | Alternative suppliers |
| Recommendation version | `recommendation_id` (UUID) + `version_number` | Immutable snapshot |
| Constraint check | embedded in recommendation | One per constraint type |
| Approval | `approval_id` (UUID) | Tied to one recommendation version |
| Purchase order | `purchase_order_id` (UUID) | Created by mock service mirror |
| Execution attempt | `execution_attempt_id` (UUID) | One per mock call |
| Validation result | `validation_result_id` (UUID) | Independent re-read comparison |

### 3.1 Case workflow states

```
draft
  → investigating
  → awaiting_approval        (recommendation ready and current)
  → approved
  → executing
  → validating
  → validated

alternatives: rejected, needs_investigation, execution_failed, validation_failed
```

- `recommendation_ready` and `awaiting_approval` are the same case state: the current
  recommendation exists and awaits a buyer decision.
- New evidence while in `awaiting_approval` or `approved` supersedes the recommendation,
  invalidates approvals, and returns the case to `draft`.

### 3.2 Recommendation decision values

`accept` | `modify` | `reject` | `investigate`. Only `accept` and `modify` are
**executable** (a purchase may be approved). `reject` and `investigate` never authorize a
purchase.

| decision | `recommended_quantity` | Notes |
|---|---|---|
| `accept` | required, non-null | equals the deterministic baseline requirement; supplier/price/cost present |
| `modify` | required, non-null | differs from baseline (e.g. MOQ-driven) and is explicitly justified; supplier/price/cost present |
| `reject` | must be null | no purchase is justified (e.g. baseline and MOQ both zero) |
| `investigate` | may be null | decision-critical evidence missing/contradictory, or MOQ overage unjustified; not executable |

Missing-data rule: if any decision-critical evidence (§6) is missing or contradictory such
that the baseline or a required constraint cannot be computed deterministically, the
version **must** be `investigate` with `missing_information` non-empty and
`recommended_quantity = null`. It must never be `accept`/`modify`, and it can never be
approved or executed. A constraint with absent evidence is `status: "unknown"` (never
`pass`).

---

## 4. Endpoint index

| # | Method | Path | Purpose |
|---|---|---|---|
| 1 | POST | `/cases` | Create a purchasing case |
| 2 | GET | `/cases/{case_id}` | Retrieve a case with evidence and status |
| 3 | PUT | `/cases/{case_id}/evidence` | Submit/replace case evidence |
| 4 | POST | `/cases/{case_id}/recommendations` | Run investigation and generate a version |
| 5 | GET | `/cases/{case_id}/recommendations` | List recommendation versions |
| 6 | GET | `/cases/{case_id}/recommendations/{version_number}` | Retrieve one version with details |
| 7 | POST | `/cases/{case_id}/recommendations/{version_number}/decision` | Approve or reject a version |
| 8 | POST | `/cases/{case_id}/purchases` | Execute the approved purchase |
| 9 | POST | `/cases/{case_id}/purchase-orders/{purchase_order_id}/validate` | Independently validate persisted PO |
| 10 | GET | `/cases/{case_id}/purchase-orders/{purchase_order_id}` | Retrieve execution + validation results |

---

## 5. Endpoint details

Shared example identifiers (stable across this document):

- `case_id`: `11111111-1111-1111-1111-111111111111`
- `supplier_offer_id`: `22222222-2222-2222-2222-222222222222`
- `recommendation_id`: `33333333-3333-3333-3333-333333333333`
- `approval_id`: `44444444-4444-4444-4444-444444444444`
- `purchase_order_id`: `55555555-5555-5555-5555-555555555555`
- `idempotency-key`: `66666666-6666-6666-6666-666666666666`

---

### 5.1 Create a purchasing case

`POST /api/v1/cases`

Request:

```json
{
  "sku": "SKU-1234",
  "product_name": "Agua Embotellada 1L",
  "currency": "MXN",
  "volume_unit": "liter",
  "initial_recommended_quantity": 800,
  "planning_horizon_start": "2026-10-16"
}
```

Rules:

- `initial_recommended_quantity` defaults to `800` and is stored for comparison only. It
  is never treated as authorization.
- `currency` and `volume_unit` are fixed for the case.
- `planning_horizon_start` defaults to the current date if omitted; the horizon is
  always seven days.

Response `201 Created`:

```json
{
  "case_id": "11111111-1111-1111-1111-111111111111",
  "sku": "SKU-1234",
  "product_name": "Agua Embotellada 1L",
  "currency": "MXN",
  "volume_unit": "liter",
  "planning_horizon_start": "2026-10-16",
  "planning_horizon_days": 7,
  "planning_horizon_end": "2026-10-22",
  "initial_recommended_quantity": 800,
  "status": "draft",
  "current_recommendation_version": null,
  "created_at": "2026-10-15T12:00:00Z",
  "updated_at": "2026-10-15T12:00:00Z"
}
```

Status codes: `201`, `422 validation_error`.

---

### 5.2 Retrieve a purchasing case

`GET /api/v1/cases/{case_id}`

Response `200 OK` — case core fields plus the current evidence block and the current
recommendation version number (null if none):

```json
{
  "case_id": "11111111-1111-1111-1111-111111111111",
  "sku": "SKU-1234",
  "product_name": "Agua Embotellada 1L",
  "currency": "MXN",
  "volume_unit": "liter",
  "planning_horizon_start": "2026-10-16",
  "planning_horizon_days": 7,
  "planning_horizon_end": "2026-10-22",
  "initial_recommended_quantity": 800,
  "status": "awaiting_approval",
  "current_recommendation_version": 1,
  "evidence": {
    "usable_inventory": 200,
    "forecast_demand": 1000,
    "safety_stock": 100,
    "budget_amount": "15000.00",
    "storage_capacity_units": "1000.0",
    "storage_required_per_unit": "1.0",
    "evidence_as_of": "2026-10-15T11:00:00Z",
    "incoming_orders": [
      {
        "incoming_order_id": "77777777-7777-7777-7777-777777777777",
        "external_ref": "PO-IN-1",
        "quantity": 100,
        "expected_arrival_date": "2026-10-18",
        "status": "confirmed"
      }
    ],
    "supplier_offers": [
      {
        "supplier_offer_id": "22222222-2222-2222-2222-222222222222",
        "supplier_name": "Distribuidora Norte",
        "unit_price": "12.50",
        "currency": "MXN",
        "moq": 500,
        "lead_time_days": 3,
        "is_available": true
      }
    ]
  },
  "created_at": "2026-10-15T12:00:00Z",
  "updated_at": "2026-10-15T12:05:00Z"
}
```

Status codes: `200`, `404 not_found`.

---

### 5.3 Submit or replace case evidence

`PUT /api/v1/cases/{case_id}/evidence`

Full replacement of the case's scalar evidence and collections. Any omitted/incoherent
field is stored as missing and **must** be surfaced by the investigation rather than
assumed.

Request:

```json
{
  "usable_inventory": 200,
  "forecast_demand": 1000,
  "safety_stock": 100,
  "budget_amount": "15000.00",
  "storage_capacity_units": "1000.0",
  "storage_required_per_unit": "1.0",
  "evidence_as_of": "2026-10-15T11:00:00Z",
  "incoming_orders": [
    {
      "external_ref": "PO-IN-1",
      "quantity": 100,
      "expected_arrival_date": "2026-10-18",
      "status": "confirmed"
    }
  ],
  "supplier_offers": [
    {
      "supplier_name": "Distribuidora Norte",
      "unit_price": "12.50",
      "currency": "MXN",
      "moq": 500,
      "lead_time_days": 3,
      "is_available": true
    }
  ]
}
```

Rules:

- All numeric fields accept `null`/omission to represent missing evidence.
- `usable_inventory`, `forecast_demand`, `safety_stock`, `quantity`, `moq`,
  `lead_time_days` must be non-negative integers if present.
- `unit_price`, `budget_amount`, `storage_capacity_units`, `storage_required_per_unit`
  must be non-negative decimals if present.
- `supplier_offers[].currency` must equal the case currency.
- Incoming orders with `status != "confirmed"` are persisted but are never eligible
  supply.

Response `200 OK`: the same shape as the `evidence` block in 5.2, with the replaced
`incoming_order_id` / `supplier_offer_id` values and `updated_at`.

Side effects:

- If the case was in `awaiting_approval` or `approved`, the current recommendation is
  superseded, its approvals are invalidated, and the case returns to `draft`.
- Superseded versions and approvals remain readable.

Status codes: `200`, `404 not_found`, `409 invalid_state` (case not in an editable state:
`executing`, `validating`, `validated`, `execution_failed`, or `validation_failed`),
`422 validation_error`.

---

### 5.4 Run the investigation and generate a recommendation

`POST /api/v1/cases/{case_id}/recommendations`

Request body: empty object `{}` (investigation is server-driven). Optional
`{"force": true}` is reserved and not required by this workflow.

Deterministic behaviour (authoritative, not LLM):

1. Compute `baseline_required_quantity = max(0, forecast_demand + safety_stock - usable_inventory - eligible_incoming_quantity)`
   where `eligible_incoming_quantity` counts only `confirmed` incoming orders with
   `planning_horizon_start <= expected_arrival_date <= planning_horizon_end` (see §2.6).
2. Evaluate supplier availability/lead time, MOQ, budget, and storage, producing
   `constraint_checks`; absent evidence yields `status: "unknown"`.
3. Choose `accept` / `modify` / `reject` / `investigate` per §3.2. If any decision-critical
   evidence is missing/contradictory so step 1 or a required check cannot be computed, the
   decision is `investigate` with `recommended_quantity = null` and `missing_information`
   populated.
4. The LLM may produce only `rationale` and interpretive text; its output is validated
   and cannot alter quantities or override a constraint.

Response `201 Created`:

```json
{
  "recommendation_id": "33333333-3333-3333-3333-333333333333",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "version_number": 1,
  "status": "ready",
  "decision": "accept",
  "initial_recommended_quantity": 800,
  "baseline_required_quantity": 800,
  "recommended_quantity": 800,
  "chosen_supplier_offer_id": "22222222-2222-2222-2222-222222222222",
  "supplier_name": "Distribuidora Norte",
  "unit_price": "12.50",
  "currency": "MXN",
  "total_cost": "10000.00",
  "lead_time_days": 3,
  "moq": 500,
  "rationale": "The deterministic requirement is 800 units. Supplier lead time (3 days) is within the 7-day horizon; MOQ 500 is below the requirement; total cost 10000.00 is within budget 15000.00; storage 800.0 is within capacity 1000.0.",
  "evidence": {
    "usable_inventory": 200,
    "forecast_demand": 1000,
    "safety_stock": 100,
    "eligible_incoming_quantity": 100,
    "budget_amount": "15000.00",
    "storage_capacity_units": "1000.0",
    "storage_required_per_unit": "1.0"
  },
  "assumptions": [
    "Planning horizon is 2026-10-16 through 2026-10-22 inclusive (seven days).",
    "Only confirmed incoming orders arriving within the horizon count (PO-IN-1, 100 units)."
  ],
  "missing_information": [],
  "inputs_fingerprint": "sha256:8f14e45fceea167a5a36dedd4bea2543",
  "constraint_checks": [
    { "constraint_type": "supplier_availability", "status": "pass", "message": "Supplier available." },
    { "constraint_type": "lead_time", "status": "pass", "evaluated_value": 3, "limit_value": 6, "unit": "days", "message": "Lead time within horizon." },
    { "constraint_type": "moq", "status": "pass", "evaluated_value": 500, "limit_value": 800, "unit": "units", "message": "MOQ satisfied by quantity." },
    { "constraint_type": "budget", "status": "pass", "evaluated_value": "10000.00", "limit_value": "15000.00", "unit": "MXN", "message": "Within budget." },
    { "constraint_type": "storage", "status": "pass", "evaluated_value": "800.0", "limit_value": "1000.0", "unit": "liter", "message": "Within storage capacity." },
    { "constraint_type": "data_quality", "status": "pass", "message": "All decision-critical evidence present." }
  ],
  "generated_by": "agent",
  "created_at": "2026-10-15T12:05:00Z"
}
```

Side effects: case moves to `awaiting_approval` (or `needs_investigation` for
`investigate`, or `rejected` for `reject`). Any previous version becomes `superseded` and
its approvals are invalidated.

Status codes:

- `201` success.
- `404 not_found`.
- `409 invalid_state` (case not in an editable state: `executing`, `validating`,
  `validated`, `execution_failed`, or `validation_failed`).
- `409 missing_evidence` — reserved for a case with **no usable evidence at all** (for
  example, no `case_evidence` row yet). When only some decision-critical fields are
  missing, a version is still created with `decision: "investigate"`,
  `recommended_quantity: null`, and `missing_information` populated.
- `422 validation_error`.

---

### 5.5 List recommendation versions

`GET /api/v1/cases/{case_id}/recommendations`

Optional query: `?latest=true` returns at most one element (the highest version).

Response `200 OK`:

```json
{
  "case_id": "11111111-1111-1111-1111-111111111111",
  "current_recommendation_version": 1,
  "recommendations": [
    { "recommendation_id": "33333333-3333-3333-3333-333333333333", "version_number": 1, "status": "ready", "decision": "accept", "recommended_quantity": 800, "total_cost": "10000.00", "created_at": "2026-10-15T12:05:00Z" }
  ]
}
```

Status codes: `200`, `404 not_found`.

---

### 5.6 Retrieve one recommendation version

`GET /api/v1/cases/{case_id}/recommendations/{version_number}`

Response `200 OK`: the full recommendation object from 5.4, including `evidence`,
`assumptions`, `missing_information`, `constraint_checks`, and `rationale`.

Status codes: `200`, `404 not_found` (unknown case or version).

---

### 5.7 Approve or reject a recommendation version

`POST /api/v1/cases/{case_id}/recommendations/{version_number}/decision`

Request (approve):

```json
{
  "decision": "approved",
  "approved_quantity": 800,
  "approver": "buyer@rappi.example",
  "note": "Approved for the seven-day horizon."
}
```

Request (reject):

```json
{
  "decision": "rejected",
  "approver": "buyer@rappi.example",
  "note": "Demand figures are stale; regenerate."
}
```

Rules:

- `approved_quantity` is required for `approved` and must equal the version's
  `recommended_quantity`. A mismatch returns `409 approval_mismatch` (the buyer is
  approving a different purchase than the version describes); a changed quantity requires
  a regenerated version.
- Approved price/cost are taken from the immutable version (not from the request) and
  recomputed deterministically as `quantity * unit_price`.
- Approval is tied to this exact `version_number`. Only the case's current version can be
  decided; a superseded/invalidated version returns `409 recommendation_superseded`.
- Only an executable version (`decision` `accept` or `modify`) can be `approved`. Approving
  a `reject`/`investigate` version returns `409 invalid_state`.
- The decision is written while holding a `SELECT ... FOR UPDATE` lock on the case row, so
  concurrent decisions and regeneration cannot race (§7).
- The approval remains valid until the version is superseded/invalidated (no time expiry).

Response `201 Created`:

```json
{
  "approval_id": "44444444-4444-4444-4444-444444444444",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "recommendation_version_number": 1,
  "decision": "approved",
  "approved_quantity": 800,
  "supplier_offer_id": "22222222-2222-2222-2222-222222222222",
  "supplier_name": "Distribuidora Norte",
  "unit_price": "12.50",
  "total_cost": "10000.00",
  "currency": "MXN",
  "approver": "buyer@rappi.example",
  "note": "Approved for the seven-day horizon.",
  "created_at": "2026-10-15T12:06:00Z"
}
```

Side effects: on `approved`, case → `approved`; on `rejected`, case → `rejected`.

Status codes: `201`, `404 not_found`, `409 approval_mismatch`,
`409 recommendation_superseded`, `409 invalid_state`, `422 validation_error`.

---

### 5.8 Execute the approved purchase

`POST /api/v1/cases/{case_id}/purchases`

Headers:

```
Idempotency-Key: 66666666-6666-6666-6666-666666666666
Content-Type: application/json
```

Request:

```json
{
  "recommendation_version_number": 1,
  "approval_id": "44444444-4444-4444-4444-444444444444"
}
```

Server-side guards (all deterministic):

1. Claim the idempotency key per §2.2 (insert/lease; reconcile an existing `uncertain` or
   expired `in_progress` key before any create).
2. In one transaction, `SELECT ... FOR UPDATE` the case row and verify: case is `approved`
   and `recommendation_version_number` is the current version; otherwise `409
   recommendation_superseded`.
3. An active approval exists for that exact version with matching quantity/supplier/cost;
   otherwise `409 stale_approval`.
4. The version's required constraint checks all `pass` (no `fail`/`unknown`); otherwise
   `409 invalid_state`. Only `accept`/`modify` versions are executable.
5. Call the mock purchasing API with the idempotency key and approved details (outside the
   lock, after the claim is committed).
6. Persist the attempt and the mirrored purchase order; move case to `validating`. Do
   **not** yet mark success. A unique-key violation on `purchase_orders` means a racing
   attempt already won; return its row.

Response `201 Created` (first successful attempt):

```json
{
  "purchase_order_id": "55555555-5555-5555-5555-555555555555",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "approval_id": "44444444-4444-4444-4444-444444444444",
  "recommendation_version_number": 1,
  "supplier_name": "Distribuidora Norte",
  "sku": "SKU-1234",
  "quantity": 800,
  "unit_price": "12.50",
  "total_cost": "10000.00",
  "currency": "MXN",
  "status": "created",
  "external_po_id": "MOCK-PO-7781",
  "execution_attempt": {
    "execution_attempt_id": "88888888-8888-8888-8888-888888888888",
    "attempt_number": 1,
    "outcome": "succeeded",
    "http_status": 201,
    "created_at": "2026-10-15T12:07:00Z"
  },
  "validation_status": "pending",
  "created_at": "2026-10-15T12:07:00Z"
}
```

Repeat handling follows §2.2 exactly: same key + same fingerprint after completion returns
`200 OK` with the stored body (no second PO); while the first attempt is unresolved and the
lease is fresh, `409 execution_in_progress`; a confirmed failure replays its stored outcome
with `200 OK` and no new attempt; an `uncertain` key is reconciled against the mock before
any create.

Status codes:

- `201` first success; `200` replayed/reconciled idempotent result.
- `409 recommendation_superseded` (requested version is not current),
  `409 stale_approval` (no active approval matches the version / approval id),
  `409 invalid_state` (case not `approved`, or constraints not all `pass`).
- `409 idempotency_conflict`, `409 execution_in_progress`.
- `422 validation_error`.
- `502 execution_failed` — mock service confirmed failure before persisting a PO; case →
  `execution_failed`; recorded in `execution_attempts`; no automatic retry. Repeating the
  same key replays this outcome (`200 OK`).
- `503 execution_uncertain` — outcome unconfirmed; the action is parked for reconciliation
  in `idempotency_keys.state = 'uncertain'`; reconcile by idempotency key before any retry.

---

### 5.9 Independently validate the persisted purchase order

`POST /api/v1/cases/{case_id}/purchase-orders/{purchase_order_id}/validate`

Request body: empty `{}`.

Behaviour (outcome/persistence matrix in §2.5):

1. Re-read the purchase order from the mock purchasing API, by the stored
   `external_po_id` / idempotency key — independent of the execution response.
2. Compare persisted `quantity`, `supplier_name`, `unit_price`, `total_cost`, `sku`, and
   currency against the approval + recommendation version, and re-check budget and
   storage constraints against the persisted quantity.
3. Persist one validation result per run with `status`, `checked_fields`, `mismatches`, and
   the raw `persisted_snapshot`. Transport failure (mock unreachable) persists nothing and
   returns `502`.
4. Apply the case/PO state per §2.5. On `passed`, case → `validated`. On `failed`, case →
   `validation_failed`; stop automatic purchasing for this action and escalate. On
   `inconclusive`, the case stays `validating` (blocked) and re-validation is allowed. No
   auto-retry or silent correction in any case.

Response `200 OK` (passing):

```json
{
  "validation_result_id": "99999999-9999-9999-9999-999999999999",
  "purchase_order_id": "55555555-5555-5555-5555-555555555555",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "recommendation_version_number": 1,
  "status": "passed",
  "checked_fields": { "sku": "SKU-1234", "quantity": 800, "supplier_name": "Distribuidora Norte", "unit_price": "12.50", "total_cost": "10000.00", "currency": "MXN" },
  "mismatches": [],
  "persisted_snapshot": { "external_po_id": "MOCK-PO-7781", "sku": "SKU-1234", "quantity": 800, "supplier_name": "Distribuidora Norte", "unit_price": "12.50", "total_cost": "10000.00", "currency": "MXN" },
  "created_at": "2026-10-15T12:08:00Z"
}
```

Response `200 OK` (failing) — see §2.5 for the mismatch example.

Status codes:

- `200` whether `passed`, `failed`, or `inconclusive` (the validation step itself ran and a
  result was persisted).
- `200` replayed for an already-final PO (`validated`/`validation_failed`): returns the
  stored terminal result and writes no new row.
- `404 not_found`.
- `409 invalid_state` if the PO is in `failed` status (a mirrored PO later determined to
  have failed during reconciliation), or is otherwise neither `created` nor already
  finalizable (e.g. no execution has produced persisted data yet). No validation result is
  persisted in this case; a `failed` PO is terminal for automatic purchasing and requires
  buyer intervention. This matches the PO state machine (`created` is the only status that
  may transition into `validated`/`validation_failed`; §2.5, `DATABASE_SCHEMA.md` §4.3).
- `502 mock_unavailable` (persisted PO could not be re-read; **no** validation result is
  persisted; the case stays `validating` and blocked; re-validate later).

---

### 5.10 Retrieve execution and validation results

`GET /api/v1/cases/{case_id}/purchase-orders/{purchase_order_id}`

Response `200 OK`:

```json
{
  "purchase_order_id": "55555555-5555-5555-5555-555555555555",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "status": "validated",
  "supplier_name": "Distribuidora Norte",
  "sku": "SKU-1234",
  "quantity": 800,
  "unit_price": "12.50",
  "total_cost": "10000.00",
  "currency": "MXN",
  "external_po_id": "MOCK-PO-7781",
  "line_items": [
    { "sku": "SKU-1234", "quantity": 800, "unit_price": "12.50", "line_total": "10000.00" }
  ],
  "execution_attempts": [
    { "execution_attempt_id": "88888888-8888-8888-8888-888888888888", "attempt_number": 1, "outcome": "succeeded", "http_status": 201, "error_code": null, "created_at": "2026-10-15T12:07:00Z" }
  ],
  "validation_results": [
    { "validation_result_id": "99999999-9999-9999-9999-999999999999", "status": "passed", "mismatches": [], "created_at": "2026-10-15T12:08:00Z" }
  ],
  "created_at": "2026-10-15T12:07:00Z"
}
```

PO `status` values: `created`, `failed`, `validated`, `validation_failed`. An
`inconclusive` validation does not change the PO status (it stays `created`); the case
stays `validating` until a later validation reaches a terminal outcome. `failed` applies
only to a mirrored PO later judged failed; a mock failure that never created a PO appears
only in `execution_attempts` (`purchase_order_id: null`). `execution_attempts` and
`validation_results` are returned in creation order (oldest first).

Status codes: `200`, `404 not_found`.

---

## 6. Decision-critical evidence and missing data

The investigation must treat these as decision-critical. If any is missing or
contradictory, it surfaces `missing_information` and, when it could change the result,
sets `decision: "investigate"` (case → `needs_investigation`) instead of guessing:

- `forecast_demand`, `safety_stock`, `usable_inventory`.
- At least one eligible supplier offer (`is_available = true`,
  `lead_time_days <= planning_horizon_days - 1`, i.e. ≤ 6 days).
- `budget_amount` for the budget check.
- `storage_capacity_units` and `storage_required_per_unit` for the storage check.
- Incoming orders with inconsistent status/arrival or negative quantities.

A constraint whose evidence is absent is reported as `status: "unknown"`, never `pass`.

---

## 7. Concurrency control

All coordination uses a single PostgreSQL database and transactions with row locks; no
extra infrastructure is required. The concrete constraints and locks are defined in
`DATABASE_SCHEMA.md` §5 and summarized here:

- **Recommendation version allocation** (`POST .../recommendations`): the handler locks the
  case row (`SELECT ... FOR UPDATE`), computes `version_number = COALESCE(MAX(version_number), 0) + 1`
  for that case, inserts the new immutable version, supersedes the previous version,
  invalidates its approvals, and updates `current_recommendation_version_id` — all in one
  transaction. `UNIQUE (case_id, version_number)` is the backstop against a duplicate
  number.
- **Approval decision** (`POST .../decision`): the case row is locked; the version is
  re-checked as current inside the lock. `uq_active_approval_per_version` (partial unique)
  prevents two active approvals for one version.
- **Evidence update** (`PUT .../evidence`): locks the case row and supersedes the current
  recommendation/approvals in the same transaction, so an approval cannot be created
  against evidence that just changed.
- **Purchase execution** (`POST .../purchases`): the idempotency key is claimed first
  (§2.2); the case row is then locked to validate `approved` state and the current version.
  The external mock call happens after the claim is committed, not while holding the case
  lock. `purchase_orders UNIQUE (idempotency_key)` is the final duplicate backstop.
- **Validation** (`POST .../validate`): the purchase-order row is locked; an existing
  terminal result is replayed instead of inserting a second final result.

If a lock is held by another transaction, the request waits on the lock; it does not fall
back to an unlocked read.

---

## 8. Assumptions not settled by the planning documents

Marked here so reviewers can confirm or correct them:

1. **Buyer identity**: `approver` is a free-text identity; no auth is implemented.
2. **Buyer modification**: buyers only `approved`/`rejected`. Changing quantity requires the
   agent to generate a new version with `decision: "modify"`.
3. **Planning anchor and window**: `planning_horizon_start` is a case field (defaults to
   today). The window is the inclusive range `[planning_horizon_start,
   planning_horizon_start + 6 days]` (seven days); the documents fix seven days but do not
   name the anchor or the inclusive-end convention.
4. **Storage conversion**: `storage_required_per_unit` is introduced to convert units to
   storage volume; the documents require consistent storage units but do not name this
   field.
5. **Validation step**: validation is explicit and separate from execution (execution does
   not report success).
6. **Idempotency key source**: client-supplied header rather than server-derived, with a
   database-backed lease (default 60 s) used only to recover a crashed in-progress attempt
   via reconciliation.
7. **Mock service**: exposes create + read by idempotency key / external PO id; its create
   is idempotent by key (repeats return the existing PO), and it has a test mode that
   persists a mismatched quantity.
8. **Recommendation decision naming**: `investigate` is used for "investigate/escalate".
