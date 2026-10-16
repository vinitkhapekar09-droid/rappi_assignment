import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { createMockApi } from '../api/mockApi'
import { DEFAULT_CASE, SAMPLE_EVIDENCE } from '../api/fixtures'
import type { InferenceInfo } from '../api/types'
import { RationalePanel } from './RationalePanel'

describe('Inference provenance', () => {
  it.each(['online', 'offline_fallback', 'provider_fallback'] as const)('accurately labels %s', async mode => {
    const api = createMockApi('happy')
    const c = await api.createCase(DEFAULT_CASE)
    await api.putEvidence(c.case_id, SAMPLE_EVIDENCE)
    const rec = await api.generateRecommendation(c.case_id)
    const inference: InferenceInfo = {
      mode, online_succeeded: mode === 'online',
      provider: mode === 'online' ? 'provider.example' : 'offline',
      model: mode === 'online' ? 'test-model-resolved' : null,
      requested_provider: 'provider.example', requested_model: 'test-model',
      fallback_reason: mode === 'online' ? null : 'Deterministic fallback used.',
    }
    rec.constraint_checks = rec.constraint_checks.map(check => check.constraint_type === 'data_quality'
      ? { ...check, details: { agent_workflow: { inference } } } : check)
    render(<RationalePanel recommendation={rec} />)
    if (mode === 'online') {
      expect(screen.getByText('Online LLM inference succeeded')).toBeInTheDocument()
      expect(screen.getByText(/test-model-resolved/)).toBeInTheDocument()
    } else {
      expect(screen.getByText('Offline fallback — no successful online inference')).toBeInTheDocument()
      expect(screen.queryByText('Online LLM inference succeeded')).not.toBeInTheDocument()
      expect(screen.getByText('Deterministic fallback used.')).toBeInTheDocument()
    }
  })
})
