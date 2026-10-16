# Database Schema — AI Purchasing Agent

Status: **implemented with Alembic migrations in the integration workspace**. Minimum
PostgreSQL schema for the single-SKU, seven-day purchasing workflow.

Companion document: `API_CONTRACT.md`. The two must stay consistent.

Source of truth: `PROJECT_PLAN.md` and `AGENTS.md`.

---

## 1. Conventions

- Engine: PostgreSQL.
- Primary keys: `UUID` with `DEFAULT gen_random_uuid()` (requires `pgcrypto`).
- Timestamps: `TIMESTAMPTZ NOT NULL DEFAULT now()`.
- Dates: `DATE`.
- Quantities: `INTEGER` (whole units, `>= 0`).
- Money and storage volume: `NUMERIC(14, 4)` (never floating point).
- Status/decision/type fields: `TEXT` with `CHECK (... IN (...))` rather than native
  enums, for simpler, additive migrations.
- Structured evidence: `JSONB` where the shape is variable (snapshots, check details,
  mismatches, assumptions).
- All foreign keys use `ON DELETE CASCADE` from the case downward unless stated; child
  history that must survive (recommendations, purchase orders) cascades with the case
  because a case is the aggregate root.
- One SKU per case; the planning horizon is fixed at seven days (§1.2).
- Cross-entity references use **composite foreign keys** that carry `case_id` (and, for
  purchase orders, `recommendation_version_id`) so a child row can never point at a parent
  from a different case or version (§3.1).
- Concurrency uses row locks inside a single PostgreSQL transaction (§5).

### 1.1 Entity relationship overview

```
purchasing_cases 1───1 case_evidence
purchasing_cases 1───* incoming_purchase_orders
purchasing_cases 1───* supplier_offers
purchasing_cases 1───* recommendation_versions ──< constraint_checks
purchasing_cases 1───1 (current) recommendation_versions
recommendation_versions 1───* approvals
approvals 1───* purchase_orders
purchase_orders 1───* purchase_order_line_items
purchase_orders 1───* execution_attempts
purchase_orders 1───* validation_results
idempotency_keys 1───0..1 purchase_orders
```

### 1.2 Seven-day horizon semantics

- `planning_horizon_start` — first day of the window, inclusive.
- `planning_horizon_end` — `planning_horizon_start + (planning_horizon_days - 1)`
  (start + 6 days), inclusive; stored as a generated column so it can never drift (§2.1).
- Forecast demand and safety stock cover exactly this seven-day window.
- Eligible incoming supply: `status = 'confirmed'` **and**
  `planning_horizon_start <= expected_arrival_date <= planning_horizon_end`.
- Eligible supplier: `is_available = true` **and**
  `lead_time_days <= planning_horizon_days - 1` (≤ 6 days), so an order placed on
  `planning_horizon_start` arrives by `planning_horizon_end`.
- `lead_time_days` and `expected_arrival_date` are calendar-day based.

This is the same definition used in `API_CONTRACT.md` §2.6, the formula, examples, and
tests.

---

## 2. Tables

### 2.1 `purchasing_cases`

Aggregate root. Holds the case identity, fixed units/currency, the initial 800-unit
recommendation for comparison, the workflow status, and a pointer to the current
recommendation version.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `sku` | `TEXT` | NOT NULL |
| `product_name` | `TEXT` | NULL |
| `currency` | `CHAR(3)` | NOT NULL |
| `volume_unit` | `TEXT` | NOT NULL |
| `planning_horizon_start` | `DATE` | NOT NULL |
| `planning_horizon_days` | `INTEGER` | NOT NULL, DEFAULT 7, CHECK `= 7` |
| `planning_horizon_end` | `DATE` | GENERATED ALWAYS AS `(planning_horizon_start + (planning_horizon_days - 1))` STORED |
| `initial_recommended_quantity` | `INTEGER` | NOT NULL, DEFAULT 800, CHECK `>= 0` |
| `status` | `TEXT` | NOT NULL, DEFAULT `'draft'`, CHECK in case statuses |
| `current_recommendation_version_id` | `UUID` | NULL, composite FK `(current_recommendation_version_id, id)` → `recommendation_versions(id, case_id)` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Case status check:

```
status IN ('draft','investigating','awaiting_approval','approved','rejected',
           'needs_investigation','executing','validating','validated',
           'execution_failed','validation_failed')
```

Indexes: `idx_cases_status (status)`, `idx_cases_sku (sku)`.

Note: `current_recommendation_version_id` and `recommendation_versions.case_id` form a
cycle. Create `purchasing_cases` first without the FK, then add the FK after
`recommendation_versions` exists (or use an `ALTER TABLE ... ADD CONSTRAINT` in the same
migration).

### 2.2 `case_evidence`

Exactly one row per case (scalar inputs). Nullable columns represent **missing evidence**
and must be surfaced, never assumed. Collections live in their own tables.

| Column | Type | Constraints |
|---|---|---|
| `case_id` | `UUID` | PK, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `usable_inventory` | `INTEGER` | NULL, CHECK `>= 0` |
| `forecast_demand` | `INTEGER` | NULL, CHECK `>= 0` |
| `safety_stock` | `INTEGER` | NULL, CHECK `>= 0` |
| `budget_amount` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `storage_capacity_units` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `storage_required_per_unit` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `evidence_as_of` | `TIMESTAMPTZ` | NULL |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

### 2.3 `incoming_purchase_orders`

Confirmed/unconfirmed inbound supply. Only `status = 'confirmed'` rows with
`planning_horizon_start <= expected_arrival_date <= planning_horizon_end` are eligible
supply (eligibility is computed by domain code against the case's generated
`planning_horizon_end`, not stored).

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `external_ref` | `TEXT` | NOT NULL |
| `quantity` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `expected_arrival_date` | `DATE` | NOT NULL |
| `status` | `TEXT` | NOT NULL, DEFAULT `'confirmed'`, CHECK in `('confirmed','unconfirmed','cancelled')` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints: `UNIQUE (case_id, external_ref)`.
Indexes: `idx_incoming_case (case_id)`, `idx_incoming_eligible (case_id, status, expected_arrival_date)`.

### 2.4 `supplier_offers`

Alternative supplier quotes for the case's SKU.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `supplier_name` | `TEXT` | NOT NULL |
| `unit_price` | `NUMERIC(14,4)` | NOT NULL, CHECK `>= 0` |
| `currency` | `CHAR(3)` | NOT NULL |
| `moq` | `INTEGER` | NOT NULL, DEFAULT 0, CHECK `>= 0` |
| `lead_time_days` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `is_available` | `BOOLEAN` | NOT NULL, DEFAULT true |
| `is_current` | `BOOLEAN` | NOT NULL, DEFAULT true; archived offers remain referenced by historical versions |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints: partial unique index `(case_id, supplier_name) WHERE is_current = true`
and `UNIQUE (id, case_id)` (the latter is the
alt key that backs the composite foreign keys from `recommendation_versions`, `approvals`,
and `purchase_orders`; see §3.1).
Indexes: `idx_supplier_case (case_id)`.
Evidence replacement archives existing offers (`is_current = false`) and inserts new
current offers. Historical recommendation/approval supplier references and purchase
terms remain unchanged. Migration `8b28a4b1c204` adds this lifecycle field/index; downgrading
to the old uniqueness rule refuses conflicting historical rows rather than deleting them.
Note: `currency` must equal the case currency; enforced in domain validation (a cross-table
CHECK would need a trigger and is not worth it in the monolith).

### 2.5 `recommendation_versions`

Immutable, monotonically versioned recommendations. A row is created once and only its
`status` changes (`ready` → `superseded`/`invalidated`); recommendation content is never
mutated.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `version_number` | `INTEGER` | NOT NULL, CHECK `>= 1` |
| `status` | `TEXT` | NOT NULL, DEFAULT `'ready'`, CHECK in `('ready','superseded','invalidated')` |
| `decision` | `TEXT` | NOT NULL, CHECK in `('accept','modify','reject','investigate')` |
| `initial_recommended_quantity` | `INTEGER` | NOT NULL, CHECK `>= 0` |
| `baseline_required_quantity` | `INTEGER` | NULL, CHECK `>= 0` |
| `recommended_quantity` | `INTEGER` | NULL, CHECK `>= 0` |
| `chosen_supplier_offer_id` | `UUID` | NULL, composite FK `(chosen_supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` |
| `supplier_name` | `TEXT` | NULL |
| `unit_price` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `currency` | `CHAR(3)` | NULL |
| `total_cost` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `lead_time_days` | `INTEGER` | NULL, CHECK `>= 0` |
| `moq` | `INTEGER` | NULL, CHECK `>= 0` |
| `rationale` | `TEXT` | NOT NULL |
| `evidence_snapshot` | `JSONB` | NOT NULL, DEFAULT `'{}'` |
| `assumptions` | `JSONB` | NOT NULL, DEFAULT `'[]'` |
| `missing_information` | `JSONB` | NOT NULL, DEFAULT `'[]'` |
| `inputs_fingerprint` | `TEXT` | NOT NULL |
| `generated_by` | `TEXT` | NOT NULL, CHECK in `('agent','offline')` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints:

- `UNIQUE (case_id, version_number)` and `UNIQUE (id, case_id)` (the latter enables
  composite foreign keys from approvals/purchase orders/validation results).
- `CHECK (total_cost IS NULL OR unit_price IS NULL OR recommended_quantity IS NULL
  OR total_cost = unit_price * recommended_quantity)` — preserves deterministic cost.
- Executable decisions must have complete purchase terms:
  `CHECK (decision NOT IN ('accept','modify') OR (recommended_quantity IS NOT NULL AND
  unit_price IS NOT NULL AND total_cost IS NOT NULL AND supplier_name IS NOT NULL))`.
- `reject` carries no quantity: `CHECK (decision <> 'reject' OR recommended_quantity IS
  NULL)`.
- `investigate` must explain itself: `CHECK (decision <> 'investigate' OR
  jsonb_array_length(missing_information) > 0)`.

Missing-data rule: when any decision-critical evidence is missing/contradictory so the
baseline or a required constraint cannot be computed, the version is `investigate` with
`recommended_quantity = NULL` and non-empty `missing_information`. It is therefore never
executable (see the transactional checks in §3.2 and API §3.2). Only `accept`/`modify` may
be approved and executed.

Indexes: `idx_reco_case_version (case_id, version_number DESC)`,
`idx_reco_case_status (case_id, status)`.

`inputs_fingerprint` is a deterministic hash of the evidence used to build the version.
It is compared against the current evidence to detect staleness independent of the case
pointer.

The API exposes `evidence_snapshot` as `evidence` in the recommendation response; they are
the same value.

### 2.6 `constraint_checks`

One row per evaluated constraint per recommendation version.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `recommendation_version_id` | `UUID` | NOT NULL, FK → `recommendation_versions(id)` ON DELETE CASCADE |
| `constraint_type` | `TEXT` | NOT NULL, CHECK in `('supplier_availability','lead_time','moq','budget','storage','data_quality')` |
| `status` | `TEXT` | NOT NULL, CHECK in `('pass','fail','unknown','not_applicable')` |
| `evaluated_value` | `NUMERIC(14,4)` | NULL |
| `limit_value` | `NUMERIC(14,4)` | NULL |
| `unit` | `TEXT` | NULL |
| `message` | `TEXT` | NOT NULL |
| `details` | `JSONB` | NOT NULL, DEFAULT `'{}'` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints: `UNIQUE (recommendation_version_id, constraint_type)`.
Indexes: `idx_checks_reco (recommendation_version_id)`.

A missing-evidence constraint is stored as `status = 'unknown'` (never `pass`).

### 2.7 `approvals`

Buyer decisions tied to an **exact** recommendation version. Approval stores a snapshot of
the approved quantity/supplier/cost so execution does not rely on later-mutable rows.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `recommendation_version_id` | `UUID` | NOT NULL, composite FK `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` ON DELETE CASCADE |
| `decision` | `TEXT` | NOT NULL, CHECK in `('approved','rejected')` |
| `approved_quantity` | `INTEGER` | NULL, CHECK `>= 0` |
| `supplier_offer_id` | `UUID` | NULL, composite FK `(supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` |
| `supplier_name` | `TEXT` | NULL |
| `unit_price` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `total_cost` | `NUMERIC(14,4)` | NULL, CHECK `>= 0` |
| `currency` | `CHAR(3)` | NULL |
| `approver` | `TEXT` | NOT NULL |
| `note` | `TEXT` | NULL |
| `invalidated_at` | `TIMESTAMPTZ` | NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints:

- `CHECK (decision <> 'approved' OR (approved_quantity IS NOT NULL AND
  total_cost IS NOT NULL AND supplier_name IS NOT NULL))`.
- `UNIQUE (id, case_id)` and `UNIQUE (id, recommendation_version_id)` — enable composite
  foreign keys from `purchase_orders` and `execution_attempts`, preventing them from
  referencing an approval of a different case or version.
- Auto-approval guard: `CHECK (approver <> 'system')` is a possible extra guard; not
  required, so omitted. (Buyer identity is a demo value.)

Indexes:

- `idx_approvals_case (case_id)`.
- **Partial unique** `uq_active_approval_per_version ON approvals(recommendation_version_id)
  WHERE decision = 'approved' AND invalidated_at IS NULL` — at most one active approval per
  version.

### 2.8 `purchase_orders`

The system's mirror of the purchase order persisted by the mock purchasing service.
Created only from an approved recommendation version.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `recommendation_version_id` | `UUID` | NOT NULL, composite FK `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` |
| `approval_id` | `UUID` | NOT NULL, composite FK `(approval_id, recommendation_version_id)` → `approvals(id, recommendation_version_id)` |
| `supplier_offer_id` | `UUID` | NULL, composite FK `(supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` |
| `supplier_name` | `TEXT` | NOT NULL |
| `sku` | `TEXT` | NOT NULL |
| `quantity` | `INTEGER` | NOT NULL, CHECK `>= 1` |
| `unit_price` | `NUMERIC(14,4)` | NOT NULL, CHECK `>= 0` |
| `total_cost` | `NUMERIC(14,4)` | NOT NULL, CHECK `>= 0` |
| `currency` | `CHAR(3)` | NOT NULL |
| `status` | `TEXT` | NOT NULL, DEFAULT `'created'`, CHECK in `('created','failed','validated','validation_failed')` |
| `idempotency_key` | `TEXT` | NOT NULL |
| `external_po_id` | `TEXT` | NULL |
| `persisted_snapshot` | `JSONB` | NOT NULL, DEFAULT `'{}'` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints:

- `UNIQUE (idempotency_key)` — one purchase order per idempotency key (duplicate-execution
  backstop).
- `UNIQUE (external_po_id)` — a mock PO cannot back two mirror rows.
- `UNIQUE (id, case_id)` — enables composite foreign keys from `execution_attempts`,
  `validation_results`, and `idempotency_keys`.
- `CHECK (total_cost = quantity * unit_price)`.

The composite FK on `approval_id` guarantees the approval belongs to the same
`recommendation_version_id`; the composite FK on `recommendation_version_id` guarantees the
version belongs to the same case.

Indexes: `idx_po_case (case_id)`, `idx_po_status (status)`.

### 2.9 `purchase_order_line_items`

One line per SKU (one row today; the table exists so the schema is not artificially
single-line).

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `purchase_order_id` | `UUID` | NOT NULL, FK → `purchase_orders(id)` ON DELETE CASCADE |
| `sku` | `TEXT` | NOT NULL |
| `quantity` | `INTEGER` | NOT NULL, CHECK `>= 1` |
| `unit_price` | `NUMERIC(14,4)` | NOT NULL, CHECK `>= 0` |
| `line_total` | `NUMERIC(14,4)` | NOT NULL, CHECK `>= 0` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints: `UNIQUE (purchase_order_id, sku)`,
`CHECK (line_total = quantity * unit_price)`.
Indexes: `idx_po_lines_po (purchase_order_id)`.

### 2.10 `execution_attempts`

Every call to the mock purchasing API, including failures and uncertain outcomes. Kept
even when no purchase order was created.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `purchase_order_id` | `UUID` | NULL, composite FK `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` |
| `approval_id` | `UUID` | NULL, composite FK `(approval_id, case_id)` → `approvals(id, case_id)` |
| `idempotency_key` | `TEXT` | NOT NULL |
| `attempt_number` | `INTEGER` | NOT NULL, CHECK `>= 1` |
| `request_payload` | `JSONB` | NOT NULL |
| `http_status` | `INTEGER` | NULL |
| `outcome` | `TEXT` | NOT NULL, CHECK in `('succeeded','failed','uncertain')` |
| `error_code` | `TEXT` | NULL |
| `error_message` | `TEXT` | NULL |
| `external_po_id` | `TEXT` | NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints: `UNIQUE (idempotency_key, attempt_number)`, `UNIQUE (id, purchase_order_id)`
(enables the composite FK from `validation_results`).
Indexes: `idx_attempts_po (purchase_order_id)`, `idx_attempts_case (case_id)`.

### 2.11 `validation_results`

Independent post-execution comparison of the persisted purchase order against the
approved recommendation.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `purchase_order_id` | `UUID` | NOT NULL, composite FK `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` ON DELETE CASCADE |
| `execution_attempt_id` | `UUID` | NULL, composite FK `(execution_attempt_id, purchase_order_id)` → `execution_attempts(id, purchase_order_id)` |
| `recommendation_version_id` | `UUID` | NOT NULL, composite FK `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` |
| `status` | `TEXT` | NOT NULL, CHECK in `('passed','failed','inconclusive')` |
| `checked_fields` | `JSONB` | NOT NULL, DEFAULT `'{}'` |
| `mismatches` | `JSONB` | NOT NULL, DEFAULT `'[]'` |
| `persisted_snapshot` | `JSONB` | NOT NULL, DEFAULT `'{}'` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

Constraints:

- `CHECK (status <> 'failed' OR jsonb_array_length(mismatches) > 0)`.
- Partial unique `uq_terminal_validation ON validation_results(purchase_order_id) WHERE
  status IN ('passed','failed')` — at most one terminal validation per purchase order, so a
  concurrent re-validation cannot record two final outcomes.

Indexes: `idx_validation_po (purchase_order_id)`.

### 2.12 `idempotency_keys`

Records purchase actions for safe duplicate prevention, separate from the purchase order.

| Column | Type | Constraints |
|---|---|---|
| `key` | `TEXT` | PK |
| `case_id` | `UUID` | NOT NULL, FK → `purchasing_cases(id)` ON DELETE CASCADE |
| `action` | `TEXT` | NOT NULL, CHECK `= 'execute_purchase'` |
| `request_fingerprint` | `TEXT` | NOT NULL |
| `state` | `TEXT` | NOT NULL, CHECK in `('in_progress','completed','failed','uncertain')` |
| `response_status` | `INTEGER` | NULL |
| `response_body` | `JSONB` | NULL |
| `purchase_order_id` | `UUID` | NULL, composite FK `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT `now()` |

`updated_at` is the lease timestamp: an `in_progress` row older than the configured lease
timeout (default 60 s) is treated as stale and must be reconciled against the mock before
any create. `state = 'uncertain'` means the mock outcome could not be confirmed; it is
resolved by reading the mock by `key` (API §2.2), never by a blind create.
`response_body` stores the exact body replayed for `completed`/`failed` idempotent
requests.

Take-over of a stale or `uncertain` key must be atomic: the creating request first performs
the conditional `UPDATE ... WHERE state = 'uncertain' OR (state = 'in_progress' AND
updated_at < now() - :lease)` (§5, step 4) and only proceeds on rowcount = 1. This makes
concurrent create attempts for the same key impossible, not merely detected after the fact.

Indexes: `idx_idem_case (case_id)`.

---

## 3. Relationships and referential rules

- A case owns exactly one `case_evidence` row, and many incoming orders, supplier offers,
  recommendation versions, approvals, purchase orders, execution attempts, and validation
  results.
- `purchasing_cases.current_recommendation_version_id` points at the latest `ready`
  version. On regeneration, the previous version becomes `superseded` and the pointer
  moves.
- `recommendation_versions.chosen_supplier_offer_id` and the approval/PO
  `supplier_offer_id` reference the supplier chosen at generation time.
- An approval references exactly one recommendation version; a purchase order references
  exactly one approval and that version.
- `execution_attempts.purchase_order_id` is nullable so a failed call that never created a
  PO is still recorded.
- `idempotency_keys.purchase_order_id` links the key to the single resulting PO.

### 3.1 Referential integrity (composite constraints)

Single-column FKs only prove the parent exists, not that it belongs to the same case or
version. The following composite constraints (all backed by the `UNIQUE` keys below) make
cross-case / cross-version references impossible:

Alt keys enabling the composite FKs:

- `supplier_offers UNIQUE (id, case_id)`
- `recommendation_versions UNIQUE (id, case_id)`
- `approvals UNIQUE (id, case_id)` and `UNIQUE (id, recommendation_version_id)`
- `purchase_orders UNIQUE (id, case_id)`
- `execution_attempts UNIQUE (id, purchase_order_id)`

Composite foreign keys:

| Child | Composite FK | Guarantees |
|---|---|---|
| `purchasing_cases` | `(current_recommendation_version_id, id)` → `recommendation_versions(id, case_id)` | the current-version pointer names a version of this same case |
| `recommendation_versions` | `(chosen_supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` | the chosen supplier is in the same case |
| `approvals` | `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` | approval's version is in the same case |
| `approvals` | `(supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` | chosen supplier is in the same case |
| `purchase_orders` | `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` | PO version is in the same case |
| `purchase_orders` | `(approval_id, recommendation_version_id)` → `approvals(id, recommendation_version_id)` | PO approval is for the exact PO version |
| `purchase_orders` | `(supplier_offer_id, case_id)` → `supplier_offers(id, case_id)` | PO supplier is in the same case |
| `execution_attempts` | `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` | attempt's PO is in the same case |
| `execution_attempts` | `(approval_id, case_id)` → `approvals(id, case_id)` | attempt's approval is in the same case |
| `validation_results` | `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` | validation's PO is in the same case |
| `validation_results` | `(execution_attempt_id, purchase_order_id)` → `execution_attempts(id, purchase_order_id)` | validation's attempt is for the same PO |
| `validation_results` | `(recommendation_version_id, case_id)` → `recommendation_versions(id, case_id)` | validation's version is in the same case |
| `idempotency_keys` | `(purchase_order_id, case_id)` → `purchase_orders(id, case_id)` | key's PO is in the same case |

Nullable components use default `MATCH SIMPLE` semantics, so a composite FK is not enforced
while any component is null (for example, an `execution_attempt` that never produced a PO).
The non-null case and version components are always enforced. Where a rule cannot be
expressed as a constraint (for example, "an executable version requires all required checks
to pass"), an explicit check runs inside the same transaction as the write and refuses the
operation; such checks are listed in §3.2.

### 3.2 Transactional checks that are not expressible as FKs

- Creating an approval requires the case to be `awaiting_approval` and the version to be the
  case's current, `ready`, executable version.
- Creating a purchase order requires an active approval for the exact version and all
  required `constraint_checks = 'pass'`.
- Recording a validation result requires the PO to be `created`. Re-read values that match
  yield `passed`; a mismatch yields a persisted `failed` (never refused, never treated as a
  success).
- Superseding a version marks old approvals `invalidated_at = now()` in the same
  transaction.

---

## 4. State transitions

### 4.1 Case status

```
draft ──generate──▶ investigating ──recommendation built──▶ awaiting_approval
awaiting_approval ──approve──▶ approved ──execute──▶ executing ──▶ validating ──▶ validated
awaiting_approval ──reject──▶ rejected
investigating/awaiting_approval ──missing/inconclusive──▶ needs_investigation
executing ──mock failure──▶ execution_failed
validating ──mismatch──▶ validation_failed

any editable state ──evidence PUT──▶ draft (supersedes recommendation, invalidates approvals)
```

Editable states are `draft`, `investigating`, `awaiting_approval`, `approved`, `rejected`,
and `needs_investigation`. Non-editable (locked) states are `executing`, `validating`,
`validated`, `execution_failed`, and `validation_failed`.

Rules enforced in domain code (guarded by DB checks on status values):

- A new recommendation may be generated from any editable state. Generating from
  `awaiting_approval` or `approved` supersedes the current version and invalidates its
  approvals (a changed purchase needs a new version and new approval). Generating from
  `executing`/`validating` is rejected with `409 invalid_state`.
- `awaiting_approval` is required to create an approval.
- `approved` is required to execute.
- `validation_failed` and `execution_failed` block further automatic purchasing for the
  action until buyer intervention.

### 4.2 Recommendation version status

```
ready ──new version generated / evidence changed──▶ superseded
ready ──explicit invalidation──▶ invalidated
```

Only a `ready` version may be approved or executed.

### 4.3 Purchase order status

```
created ──validate passed──▶ validated
created ──validate failed──▶ validation_failed
created ──validate inconclusive──▶ remains created (case stays validating; reconcile)
created ──post-mirror execution failure──▶ failed
```

An `inconclusive` validation (e.g. the mock service could not be re-read) does not mark
the PO validated or failed; the case stays `validating` and the action is parked for
reconciliation. It never auto-retries.

A mock failure that never produced a PO is recorded only in `execution_attempts`
(`outcome = 'failed'`, `purchase_order_id = NULL`); there is no PO row and therefore no PO
status to set. `purchase_orders.status = 'failed'` is reserved for a mirrored PO that is
later determined to have failed (for example, during reconciliation).

A `failed` PO is terminal and cannot be validated: `POST .../validate` returns
`409 invalid_state` and writes no validation result, because `created` is the only status
that may transition into `validated`/`validation_failed` (see `API_CONTRACT.md` §5.9). It
requires buyer intervention.

---

## 5. Concurrency control

All coordination uses this single PostgreSQL database and row-level locks inside
transactions. No advisory locks, queues, or extra infrastructure are introduced.

Locking protocol per write path (mirrors `API_CONTRACT.md` §7):

1. **Recommendation version allocation** — `BEGIN`; `SELECT ... FROM purchasing_cases WHERE
   id = :case FOR UPDATE`; if the case is editable, compute
   `version_number = COALESCE(MAX(version_number), 0) + 1` for the case; insert the new
   immutable version; set the previous version `superseded`; set old approvals
   `invalidated_at = now()`; update `current_recommendation_version_id`; `COMMIT`.
   `UNIQUE (case_id, version_number)` is the backstop if the lock is ever bypassed.
2. **Evidence replacement** — `SELECT ... FOR UPDATE` the case row; replace `case_evidence`
   and collections; supersede the current version and invalidate its approvals; set case
   `draft`; `COMMIT`. This prevents an approval being created concurrently against evidence
   that has just changed.
3. **Approval decision** — `SELECT ... FOR UPDATE` the case row; verify the version is
   current and `ready`; insert the decision; `COMMIT`. `uq_active_approval_per_version`
   prevents two active approvals.
4. **Purchase execution** — claim the idempotency key **atomically**, so two concurrent
   recoveries cannot both create:
   - fresh key: `INSERT ... ON CONFLICT DO NOTHING` (rowcount = 1 means this request owns the
     create);
   - recovery (`uncertain`, or expired-lease `in_progress`): a conditional update
     `UPDATE idempotency_keys SET state = 'in_progress', updated_at = now() WHERE key = :key
     AND action = 'execute_purchase' AND (state = 'uncertain' OR (state = 'in_progress' AND
     updated_at < now() - :lease))` — rowcount = 1 means this request owns the create.

   Only the owner proceeds. Commit the claim; then `SELECT ... FOR UPDATE` the case row to
   verify `approved` state and current version; commit; then call the external mock **after
   releasing the case lock**. Persist the attempt and mirror the PO. The mock's own
   key-idempotency plus `purchase_orders UNIQUE (idempotency_key)` are the final duplicate
   backstops if two attempts ever race.
5. **Validation** — `SELECT ... FOR UPDATE` the purchase-order row; if a terminal result
   already exists, replay it; otherwise re-read the mock, insert exactly one result, and
   update PO/case state; `COMMIT`. `uq_terminal_validation` prevents two terminal results.

Requests that contend on a row lock wait for the lock; they never fall back to an unlocked
read or proceed speculatively. External calls are made outside long-lived row locks;
idempotency plus the unique constraints provide the correctness guarantee.

---

## 6. Requirement-to-schema mapping

| Requirement | Enforcement |
|---|---|
| Old approval cannot authorize a newer recommendation | Approval's composite FK pins the exact `(recommendation_version_id, case_id)`; execution requires `recommendation_version_id = cases.current_recommendation_version_id`; regeneration supersedes old versions and sets `invalidated_at`; `uq_active_approval_per_version` keeps one active approval per version |
| Repeated execution cannot create duplicate POs | `idempotency_keys` PK + `purchase_orders UNIQUE (idempotency_key)`; `request_fingerprint` rejects key reuse with a different body; `state='uncertain'` reconciled via mock before any create |
| Cross-case/cross-version references blocked | Composite FKs in §3.1 require `case_id` (and PO `recommendation_version_id`) to match the parent |
| Every purchase requires explicit approval | `purchase_orders.approval_id NOT NULL` + composite FK to the same version; PO rows only created from an active approval tied to the current version |
| Approval matches exact purchase details | Approval stores `approved_quantity`, `supplier_name`, `unit_price`, `total_cost`; PO `CHECK (total_cost = quantity * unit_price)` |
| Deterministic arithmetic | `NUMERIC` totals with `CHECK` equalities on recommendation, PO, and line items; domain code computes values |
| Missing evidence surfaced, not assumed | Nullable evidence columns; constraint `status = 'unknown'` when data absent; `investigate` requires non-empty `missing_information` and null quantity, so it is never executable |
| Independent validation of persisted PO | `validation_results` re-read from mock service, separate from `purchase_orders.persisted_snapshot`; mismatch persisted; failed validation flips case to `validation_failed`; `uq_terminal_validation` prevents duplicate finals |
| One SKU, seven-day horizon | `planning_horizon_days CHECK (= 7)` + generated `planning_horizon_end`; `purchase_order_line_items UNIQUE (purchase_order_id, sku)`; one SKU column on case |

---

## 7. Assumptions not settled by the planning documents

1. `planning_horizon_start` anchors the seven-day window; the window is the inclusive range
   `[planning_horizon_start, planning_horizon_start + 6 days]`. The documents fix seven days
   but do not name the anchor or the inclusive-end convention.
2. `storage_required_per_unit` converts quantities to storage volume (the documents require
   consistent units but do not name the conversion field).
3. `version_number` is per-case and monotonic; `id` is an independent UUID.
4. Native PostgreSQL enums are intentionally avoided in favor of `TEXT` + `CHECK`.
5. Currency consistency across offers is enforced in domain code, not via a cross-table DB
   constraint.
6. `case_evidence` is a single row per case (scalar inputs); a full column-level evidence
   history is out of scope. Recommendation versions snapshot evidence via
   `evidence_snapshot`/`inputs_fingerprint`.
7. Buyer identity (`approver`) is free text for the demo; no auth/roles table.
8. The mock purchasing service persists its own PO; `purchase_orders` is the system mirror,
   reconciled by `external_po_id` and `idempotency_key`.
9. The idempotency lease timeout is a configurable constant (default 60 s); a stale
   `in_progress` key is recovered only by reconciling against the mock, never by a direct
   create.
10. `planning_horizon_end` is a stored generated column rather than an application-computed
    value, to prevent the horizon from drifting between reads.
