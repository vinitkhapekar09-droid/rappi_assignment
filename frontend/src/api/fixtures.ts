import type { Evidence } from './types'

/**
 * Deterministic demo fixtures for the mock adapter.
 *
 * These values reproduce the contract's worked example: usable inventory 200,
 * forecast demand 1000, safety stock 100, one confirmed incoming order of 100
 * arriving inside the seven-day horizon -> baseline requirement 800. This is the
 * same "initial 800-unit recommendation" scenario the assignment starts from.
 */

export const FIXTURE_IDS = {
  caseId: '11111111-1111-1111-1111-111111111111',
  supplierOfferId: '22222222-2222-2222-2222-222222222222',
  recommendationId: '33333333-3333-3333-3333-333333333333',
  approvalId: '44444444-4444-4444-4444-444444444444',
  purchaseOrderId: '55555555-5555-5555-5555-555555555555',
  idempotencyKey: '66666666-6666-6666-6666-666666666666',
  incomingOrderId: '77777777-7777-7777-7777-777777777777',
  executionAttemptId: '88888888-8888-8888-8888-888888888888',
  validationResultId: '99999999-9999-9999-9999-999999999999',
} as const

export const PLANNING_HORIZON_START = '2026-10-16'
export const PLANNING_HORIZON_DAYS = 7

export type ScenarioId =
  | 'happy'
  | 'budget'
  | 'storage'
  | 'moq'
  | 'missing'
  | 'mismatch'
  | 'execution_failure'

/** Fields the mock may deliberately persist differently from the approved request. */
export interface PersistedOverride {
  field: 'quantity' | 'supplier_name' | 'unit_price' | 'total_cost' | 'sku' | 'currency'
  value: string | number
}

export interface Scenario {
  id: ScenarioId
  label: string
  description: string
  evidence: Evidence
  /** Force the mock purchasing service to persist a mismatched PO (validation failure). */
  persistOverrides?: PersistedOverride[]
  /** Force the mock purchasing service to fail before creating a PO. */
  failExecution?: boolean
  /** Make the persisted PO unreadable during validation (transport failure). */
  mockUnavailableOnValidate?: boolean
}

const BASE_SUPPLIER = {
  supplier_offer_id: FIXTURE_IDS.supplierOfferId,
  supplier_name: 'Distribuidora Norte',
  unit_price: '12.50',
  currency: 'MXN',
  moq: 500,
  lead_time_days: 3,
  is_available: true,
}

function baseEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    usable_inventory: 200,
    forecast_demand: 1000,
    safety_stock: 100,
    budget_amount: '15000.00',
    storage_capacity_units: '1000.0',
    storage_required_per_unit: '1.0',
    evidence_as_of: '2026-10-15T11:00:00Z',
    incoming_orders: [
      {
        incoming_order_id: FIXTURE_IDS.incomingOrderId,
        external_ref: 'PO-IN-1',
        quantity: 100,
        expected_arrival_date: '2026-10-18',
        status: 'confirmed',
      },
    ],
    supplier_offers: [{ ...BASE_SUPPLIER }],
    ...overrides,
  }
}

export const SCENARIOS: Scenario[] = [
  {
    id: 'happy',
    label: '1. Valid recommendation review',
    description:
      'Evidence supports the 800-unit recommendation. All constraints pass.',
    evidence: baseEvidence(),
  },
  {
    id: 'budget',
    label: '2. Budget constraint',
    description:
      'Available budget (5000.00) is below the 10000.00 cost of the 800-unit requirement.',
    evidence: baseEvidence({ budget_amount: '5000.00' }),
  },
  {
    id: 'storage',
    label: '3. Storage constraint',
    description:
      'Storage capacity (500.0) cannot hold the 800-unit requirement (800.0).',
    evidence: baseEvidence({ storage_capacity_units: '500.0' }),
  },
  {
    id: 'moq',
    label: '4. MOQ conflict (escalate)',
    description:
      'Baseline is 100 units but MOQ is 500; the 500 units would exceed storage, so the extra does not fit.',
    evidence: baseEvidence({
      forecast_demand: 300,
      storage_capacity_units: '300.0',
    }),
  },
  {
    id: 'missing',
    label: '5. Missing decision-critical data',
    description: 'Forecast demand is absent, so the baseline cannot be computed.',
    evidence: baseEvidence({ forecast_demand: null }),
  },
  {
    id: 'mismatch',
    label: '6. Persisted-result mismatch',
    description:
      'The mock persists 500 units while 800 were approved; validation must detect it.',
    evidence: baseEvidence(),
    persistOverrides: [{ field: 'quantity', value: 500 }],
  },
  {
    id: 'execution_failure',
    label: '7. Execution failure',
    description:
      'The mock purchasing service fails before creating a purchase order.',
    evidence: baseEvidence(),
    failExecution: true,
  },
]

export function getScenario(id: ScenarioId): Scenario {
  const found = SCENARIOS.find((scenario) => scenario.id === id)
  if (!found) throw new Error(`Unknown scenario: ${id}`)
  return found
}

/** Sample evidence used to seed the case while the backend is being implemented. */
export const SAMPLE_EVIDENCE: Evidence = baseEvidence()

export const DEFAULT_CASE = {
  sku: 'SKU-1234',
  product_name: 'Agua Embotellada 1L',
  currency: 'MXN',
  volume_unit: 'liter',
  initial_recommended_quantity: 800,
  planning_horizon_start: PLANNING_HORIZON_START,
}

export function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}
