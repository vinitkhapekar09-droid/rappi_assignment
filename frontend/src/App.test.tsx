import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { createMockApi } from './api/mockApi'
import { SAMPLE_EVIDENCE } from './api/fixtures'

afterEach(() => vi.unstubAllGlobals())

describe('App with the mock adapter', () => {
  beforeEach(() => vi.stubEnv('VITE_API_MODE', 'mock'))
  it('renders the 800-unit recommendation and its constraint results', async () => {
    render(<App />)

    expect(await screen.findByText('Calculated requirement')).toBeInTheDocument()
    expect(await screen.findByText('Accept recommendation')).toBeInTheDocument()
    expect(await screen.findByText('Data quality')).toBeInTheDocument()
    expect(await screen.findByText('Supplier availability')).toBeInTheDocument()

    const quantities = await screen.findAllByText('800 units')
    expect(quantities.length).toBeGreaterThan(0)
  })

  it('blocks approval when the reviewed quantity differs from the recommendation', async () => {
    const user = userEvent.setup()
    render(<App />)

    const input = await screen.findByLabelText('Review quantity')
    await user.clear(input)
    await user.type(input, '900')

    expect(screen.getByRole('button', { name: /approve purchase/i })).toBeDisabled()
    expect(
      screen.getByText(/requires a new recommendation version/i, { selector: 'p' }),
    ).toBeInTheDocument()
  })

  it.each(['budget', 'storage', 'moq'])('does not offer approval for a %s-blocked purchase', async scenario => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findByLabelText('Review quantity')
    await user.selectOptions(screen.getByRole('combobox'), scenario)
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue(scenario))
    await screen.findByText(/Purchase blocked\. This/)
    expect(screen.queryByRole('button', { name: /approve purchase/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/Approval is allowed/)).not.toBeInTheDocument()
    expect(screen.queryByText('Accept recommendation')).not.toBeInTheDocument()
  })
})

describe('App with the HTTP adapter', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_MODE', 'http')
    vi.stubEnv('VITE_API_BASE_URL', '')
  })

  it.each([false, true])('gates investigation on successfully loaded evidence (failure=%s)', async failEvidence => {
    const fixture = createMockApi('happy')
    const fetch = vi.fn(async (url: string, options?: RequestInit) => {
      const path = url.replace('/api/v1', '')
      const body = options?.body ? JSON.parse(String(options.body)) : null
      if (path === '/cases') return Response.json(await fixture.createCase(body), { status: 201 })
      const cid = path.split('/')[2]
      if (options?.method === 'GET') return Response.json(await fixture.getCase(cid))
      if (path.endsWith('/evidence')) {
        if (failEvidence) return Response.json({ error: { code: 'validation_error', message: 'Request validation failed.' } }, { status: 422 })
        expect(body.incoming_orders[0]).not.toHaveProperty('incoming_order_id')
        expect(body.supplier_offers[0]).not.toHaveProperty('supplier_offer_id')
        return Response.json(await fixture.putEvidence(cid, SAMPLE_EVIDENCE))
      }
      if (path.endsWith('/recommendations')) return Response.json(await fixture.generateRecommendation(cid), { status: 201 })
      throw new Error(`Unexpected request ${path}`)
    })
    vi.stubGlobal('fetch', fetch)
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('Evidence needed')
    expect(screen.getByText('No evidence has been submitted. Load sample evidence before running the investigation.')).toBeInTheDocument()
    const investigate = screen.getByRole('button', { name: 'Run investigation' })
    expect(investigate).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Load sample evidence' }))
    if (failEvidence) {
      await screen.findByText('Request validation failed.')
      expect(investigate).toBeDisabled()
      expect(fetch.mock.calls.some(([url]) => url.endsWith('/recommendations'))).toBe(false)
    } else {
      await waitFor(() => expect(investigate).toBeEnabled())
      await user.click(investigate)
      await screen.findByText('Accept recommendation')
    }
  })
})
