import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHttpApi } from './httpApi'
import { SAMPLE_EVIDENCE } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

describe('HTTP purchasing adapter', () => {
  it('omits response-only IDs from the actual sample evidence request', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await createHttpApi('').putEvidence('case', SAMPLE_EVIDENCE)
    const sent = JSON.parse(fetch.mock.calls[0][1].body)
    expect(sent.incoming_orders[0]).not.toHaveProperty('incoming_order_id')
    expect(sent.supplier_offers[0]).not.toHaveProperty('supplier_offer_id')
    expect(sent.incoming_orders[0].external_ref).toBe(SAMPLE_EVIDENCE.incoming_orders[0].external_ref)
    expect(SAMPLE_EVIDENCE.incoming_orders[0]).toHaveProperty('incoming_order_id')
  })
  it('sends the approval version and idempotency key through the frozen route', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ purchase_order_id: 'po' })))
    vi.stubGlobal('fetch', fetch)
    await createHttpApi('').executePurchase('case', {
      recommendation_version_number: 2, approval_id: 'approval',
    }, 'same-key')
    expect(fetch).toHaveBeenCalledWith('/api/v1/cases/case/purchases', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'Idempotency-Key': 'same-key' }),
      body: JSON.stringify({ recommendation_version_number: 2, approval_id: 'approval' }),
    }))
  })

  it('surfaces a replayed terminal failure even when the replay HTTP status is 200', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'execution_failed', message: 'Persisted failure' },
    }))))
    await expect(createHttpApi('').executePurchase('case', {
      recommendation_version_number: 1, approval_id: 'approval',
    }, 'same-key')).rejects.toMatchObject({ code: 'execution_failed' })
  })
})
