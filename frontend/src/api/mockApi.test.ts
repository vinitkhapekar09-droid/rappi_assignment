import { describe, expect, it } from 'vitest'
import { createMockApi } from './mockApi'
import { DEFAULT_CASE } from './fixtures'
import { isApiClientError } from './errors'
async function bootstrapHappy(api = createMockApi('happy')) {
  const caseDetail = await api.createCase(DEFAULT_CASE)
  await api.putEvidence(caseDetail.case_id, api.getScenarioEvidence())
  const recommendation = await api.generateRecommendation(caseDetail.case_id)
  return { api, caseDetail, recommendation }
}

async function expectError(promise: Promise<unknown>, code: string) {
  try {
    await promise
    throw new Error(`Expected error with code ${code}`)
  } catch (error) {
    expect(isApiClientError(error)).toBe(true)
    if (isApiClientError(error)) expect(error.code).toBe(code)
  }
}

describe('mockApi — deterministic recommendation', () => {
  it('computes the 800-unit baseline from the sample evidence', async () => {
    const { recommendation } = await bootstrapHappy()
    expect(recommendation.baseline_required_quantity).toBe(800)
    expect(recommendation.initial_recommended_quantity).toBe(800)
    expect(recommendation.recommended_quantity).toBe(800)
    expect(recommendation.decision).toBe('accept')
    expect(recommendation.constraint_checks.every((c) => c.status === 'pass')).toBe(true)
    expect(recommendation.total_cost).toBe('10000.00')
  })

  it('flags a budget constraint and stops being executable', async () => {
    const { api, caseDetail, recommendation } = await bootstrapHappy(createMockApi('budget'))
    expect(recommendation.decision).toBe('investigate')
    expect(recommendation.recommended_quantity).toBeNull()
    const budget = recommendation.constraint_checks.find((c) => c.constraint_type === 'budget')
    expect(budget?.status).toBe('fail')
    const caseAfter = await api.getCase(caseDetail.case_id)
    expect(caseAfter.status).toBe('needs_investigation')
  })

  it('flags a storage constraint', async () => {
    const { recommendation } = await bootstrapHappy(createMockApi('storage'))
    const storage = recommendation.constraint_checks.find((c) => c.constraint_type === 'storage')
    expect(storage?.status).toBe('fail')
    expect(recommendation.decision).toBe('investigate')
  })

  it('escalates an unjustified MOQ overage instead of silently rounding up', async () => {
    const { recommendation } = await bootstrapHappy(createMockApi('moq'))
    expect(recommendation.baseline_required_quantity).toBe(100)
    expect(recommendation.decision).toBe('investigate')
    expect(recommendation.recommended_quantity).toBeNull()
    expect(recommendation.missing_information.length).toBeGreaterThan(0)
  })

  it('surfaces missing decision-critical data', async () => {
    const { recommendation } = await bootstrapHappy(createMockApi('missing'))
    expect(recommendation.decision).toBe('investigate')
    expect(recommendation.recommended_quantity).toBeNull()
    expect(recommendation.missing_information.join(' ')).toContain('forecast_demand')
    const dataQuality = recommendation.constraint_checks.find(
      (c) => c.constraint_type === 'data_quality',
    )
    expect(dataQuality?.status).toBe('fail')
  })
})

describe('mockApi — approval guards', () => {
  it('rejects an approval whose quantity differs from the recommendation', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    await expectError(
      api.decide(caseDetail.case_id, 1, {
        decision: 'approved',
        approved_quantity: 700,
        approver: 'buyer@rappi.example',
      }),
      'approval_mismatch',
    )
  })

  it('refuses to approve an investigate recommendation', async () => {
    const { api, caseDetail } = await bootstrapHappy(createMockApi('missing'))
    await expectError(
      api.decide(caseDetail.case_id, 1, {
        decision: 'approved',
        approver: 'buyer@rappi.example',
      }),
      'invalid_state',
    )
  })

  it('rejects a decision on a superseded version', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    await api.generateRecommendation(caseDetail.case_id)
    await expectError(
      api.decide(caseDetail.case_id, 1, {
        decision: 'approved',
        approved_quantity: 800,
        approver: 'buyer@rappi.example',
      }),
      'recommendation_superseded',
    )
  })

  it('refuses execution before approval', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    await expectError(
      api.executePurchase(
        caseDetail.case_id,
        { recommendation_version_number: 1, approval_id: 'does-not-exist' },
        'key-1',
      ),
      'invalid_state',
    )
  })
})

describe('mockApi — execution, idempotency and validation', () => {
  it('runs the happy path and validates the persisted order', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    const approval = await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    const order = await api.executePurchase(
      caseDetail.case_id,
      { recommendation_version_number: 1, approval_id: approval.approval_id },
      'key-happy',
    )
    expect(order.status).toBe('created')

    const validation = await api.validatePurchase(caseDetail.case_id, order.purchase_order_id)
    expect(validation.status).toBe('passed')
    expect(validation.mismatches).toHaveLength(0)

    const caseAfter = await api.getCase(caseDetail.case_id)
    expect(caseAfter.status).toBe('validated')
  })

  it('creates only one purchase order for a repeated idempotency key', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    const approval = await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    const body = { recommendation_version_number: 1, approval_id: approval.approval_id }
    const first = await api.executePurchase(caseDetail.case_id, body, 'key-dup')
    const second = await api.executePurchase(caseDetail.case_id, body, 'key-dup')
    expect(second.purchase_order_id).toBe(first.purchase_order_id)
  })

  it('rejects reusing an idempotency key with a different request', async () => {
    const { api, caseDetail } = await bootstrapHappy()
    const approval = await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    await api.executePurchase(
      caseDetail.case_id,
      { recommendation_version_number: 1, approval_id: approval.approval_id },
      'key-conflict',
    )
    await expectError(
      api.executePurchase(
        caseDetail.case_id,
        { recommendation_version_number: 2, approval_id: approval.approval_id },
        'key-conflict',
      ),
      'idempotency_conflict',
    )
  })

  it('detects a persisted-result mismatch and blocks the case', async () => {
    const api = createMockApi('mismatch')
    const caseDetail = await api.createCase(DEFAULT_CASE)
    await api.putEvidence(caseDetail.case_id, api.getScenarioEvidence())
    await api.generateRecommendation(caseDetail.case_id)
    const approval = await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    const order = await api.executePurchase(
      caseDetail.case_id,
      { recommendation_version_number: 1, approval_id: approval.approval_id },
      'key-mismatch',
    )
    expect(order.quantity).toBe(500)

    const validation = await api.validatePurchase(caseDetail.case_id, order.purchase_order_id)
    expect(validation.status).toBe('failed')
    expect(validation.mismatches[0]).toEqual({
      field: 'quantity',
      approved_value: 800,
      persisted_value: 500,
    })
    const caseAfter = await api.getCase(caseDetail.case_id)
    expect(caseAfter.status).toBe('validation_failed')
  })

  it('records an execution failure and does not create a purchase order', async () => {
    const api = createMockApi('execution_failure')
    const caseDetail = await api.createCase(DEFAULT_CASE)
    await api.putEvidence(caseDetail.case_id, api.getScenarioEvidence())
    await api.generateRecommendation(caseDetail.case_id)
    const approval = await api.decide(caseDetail.case_id, 1, {
      decision: 'approved',
      approved_quantity: 800,
      approver: 'buyer@rappi.example',
    })
    await expectError(
      api.executePurchase(
        caseDetail.case_id,
        { recommendation_version_number: 1, approval_id: approval.approval_id },
        'key-fail',
      ),
      'execution_failed',
    )
    const caseAfter = await api.getCase(caseDetail.case_id)
    expect(caseAfter.status).toBe('execution_failed')
  })
})
