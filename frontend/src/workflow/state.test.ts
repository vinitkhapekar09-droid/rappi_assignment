import { describe, expect, it } from 'vitest'
import type { CaseDetail, Recommendation } from '../api/types'
import {
  describeStatus,
  initialWorkflowState,
  isExecutable,
  reviewQuantity,
  workflowReducer,
} from './state'

const recommendation: Recommendation = {
  recommendation_id: 'rec-1',
  case_id: 'case-1',
  version_number: 1,
  status: 'ready',
  decision: 'accept',
  initial_recommended_quantity: 800,
  baseline_required_quantity: 800,
  recommended_quantity: 800,
  chosen_supplier_offer_id: null,
  supplier_name: 'Distribuidora Norte',
  unit_price: '12.50',
  currency: 'MXN',
  total_cost: '10000.00',
  lead_time_days: 3,
  moq: 500,
  rationale: '',
  evidence: null,
  assumptions: [],
  missing_information: [],
  inputs_fingerprint: null,
  constraint_checks: ['supplier_availability', 'lead_time', 'moq', 'budget', 'storage', 'data_quality'].map(
    constraint_type => ({ constraint_type, status: 'pass', message: 'Verified' } as Recommendation['constraint_checks'][number]),
  ),
  generated_by: 'test',
  created_at: '2026-10-15T12:00:00Z',
}

function caseWith(status: CaseDetail['status']): CaseDetail {
  return {
    case_id: 'case-1',
    sku: 'SKU-1234',
    product_name: 'Agua',
    currency: 'MXN',
    volume_unit: 'liter',
    planning_horizon_start: '2026-10-16',
    planning_horizon_days: 7,
    planning_horizon_end: '2026-10-22',
    initial_recommended_quantity: 800,
    status,
    current_recommendation_version: 1,
    evidence: null,
    created_at: '2026-10-15T12:00:00Z',
    updated_at: '2026-10-15T12:00:00Z',
  }
}

describe('workflowReducer', () => {
  it('records log entries', () => {
    const next = workflowReducer(initialWorkflowState, {
      type: 'log',
      level: 'info',
      message: 'hello',
    })
    expect(next.log).toHaveLength(1)
    expect(next.log[0].message).toBe('hello')
  })

  it('clears busy when an error is recorded', () => {
    const busy = workflowReducer(initialWorkflowState, { type: 'busy', action: 'execute' })
    const errored = workflowReducer(busy, {
      type: 'error',
      error: { code: 'execution_failed', message: 'boom' },
    })
    expect(errored.busy).toBeNull()
    expect(errored.error?.code).toBe('execution_failed')
  })

  it('reset returns to the initial state', () => {
    const dirty = workflowReducer(initialWorkflowState, { type: 'case', value: caseWith('approved') })
    expect(workflowReducer(dirty, { type: 'reset' })).toEqual(initialWorkflowState)
  })
})

describe('describeStatus', () => {
  it('reports loading before a case exists', () => {
    expect(describeStatus(initialWorkflowState).status).toBe('loading')
  })

  it('surfaces the error state first', () => {
    const state = {
      ...initialWorkflowState,
      error: { code: 'validation_error', message: 'bad input' },
    }
    const descriptor = describeStatus(state)
    expect(descriptor.status).toBe('error')
    expect(descriptor.detail).toBe('bad input')
  })

  it('maps awaiting_approval and validated', () => {
    expect(
      describeStatus({ ...initialWorkflowState, caseDetail: caseWith('awaiting_approval') }).status,
    ).toBe('awaiting_approval')
    expect(
      describeStatus({ ...initialWorkflowState, caseDetail: caseWith('validated') }).tone,
    ).toBe('success')
  })
})

describe('reviewQuantity', () => {
  const evidence = {
    budget_amount: '10000.00',
    storage_capacity_units: '1000.0',
    storage_required_per_unit: '1.0',
  }

  it('confirms an exact match', () => {
    const review = reviewQuantity(recommendation, evidence, 800)
    expect(review.matchesRecommendation).toBe(true)
    expect(review.delta).toBe(0)
    expect(review.previewCost).toBe('10000.00')
    expect(review.withinBudget).toBe(true)
    expect(review.withinStorage).toBe(true)
  })

  it('flags a changed quantity and the budget impact', () => {
    const review = reviewQuantity(recommendation, evidence, 900)
    expect(review.matchesRecommendation).toBe(false)
    expect(review.delta).toBe(100)
    expect(review.previewCost).toBe('11250.00')
    expect(review.withinBudget).toBe(false)
  })

  it('is executable only for accept/modify with a quantity', () => {
    expect(isExecutable(recommendation)).toBe(true)
    expect(isExecutable({ ...recommendation, recommended_quantity: null })).toBe(false)
    expect(isExecutable({ ...recommendation, decision: 'investigate' })).toBe(false)
    expect(isExecutable({ ...recommendation, constraint_checks: [] })).toBe(false)
    expect(isExecutable({ ...recommendation, constraint_checks: recommendation.constraint_checks.map(
      c => c.constraint_type === 'budget' ? { ...c, status: 'fail' } : c,
    ) })).toBe(false)
  })
})
