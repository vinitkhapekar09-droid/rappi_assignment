import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import type { ApiBundle, ApiMode } from '../api'
import { isApiClientError } from '../api'
import type {
  Approval,
  CaseDetail,
  PurchaseOrder,
  Recommendation,
  ValidationResult,
} from '../api/types'
import type { MockPurchasingApi } from '../api/mockApi'
import { DEFAULT_CASE, SAMPLE_EVIDENCE, type ScenarioId } from '../api/fixtures'
import {
  initialWorkflowState,
  workflowReducer,
  type BusyAction,
  type WorkflowError,
  type WorkflowState,
} from './state'

export interface UsePurchasingWorkflowResult {
  state: WorkflowState
  apiMode: ApiMode
  mock?: MockPurchasingApi
  scenarioId?: ScenarioId
  bootstrap: () => Promise<void>
  changeScenario: (id: ScenarioId) => Promise<void>
  loadSampleEvidence: () => Promise<void>
  runInvestigation: () => Promise<void>
  approve: (note?: string) => Promise<void>
  reject: (note?: string) => Promise<void>
  execute: () => Promise<void>
  validate: () => Promise<void>
  refreshCase: () => Promise<void>
  dismissError: () => void
}

interface Options {
  approver: string
  autoBootstrap?: boolean
}

export function usePurchasingWorkflow(
  bundle: ApiBundle,
  options: Options,
): UsePurchasingWorkflowResult {
  const { api, mode, mock } = bundle
  const [state, dispatch] = useReducer(workflowReducer, initialWorkflowState)
  const stateRef = useRef(state)
  stateRef.current = state
  const bootstrapped = useRef(false)

  const log = useCallback(
    (level: 'info' | 'success' | 'warning' | 'error', message: string) => {
      dispatch({ type: 'log', level, message })
    },
    [],
  )

  const fail = useCallback(
    (error: unknown) => {
      const workflowError: WorkflowError = isApiClientError(error)
        ? {
            code: error.code,
            message: error.message,
            status: error.status,
            details: error.details,
          }
        : { code: 'unexpected_error', message: 'An unexpected error occurred.' }
      dispatch({ type: 'error', error: workflowError })
      log('error', `[${workflowError.code}] ${workflowError.message}`)
    },
    [log],
  )

  const withBusy = useCallback(
    async (action: BusyAction, fn: () => Promise<void>) => {
      dispatch({ type: 'busy', action })
      try {
        await fn()
      } catch (error) {
        fail(error)
      } finally {
        dispatch({ type: 'busy', action: null })
      }
    },
    [fail],
  )

  const refreshCase = useCallback(async () => {
    const current = stateRef.current.caseDetail
    if (!current) return
    const fresh = await api.getCase(current.case_id)
    dispatch({ type: 'case', value: fresh })
  }, [api])

  const generate = useCallback(
    async (caseId: string): Promise<Recommendation> => {
      const recommendation = await api.generateRecommendation(caseId)
      dispatch({ type: 'recommendation', value: recommendation })
      dispatch({ type: 'approval', value: null })
      dispatch({ type: 'purchase_order', value: null })
      dispatch({ type: 'validation', value: null })
      dispatch({ type: 'idempotency_key', value: null })
      log(
        'info',
        `Recommendation v${recommendation.version_number} generated: ${recommendation.decision}` +
          (recommendation.recommended_quantity !== null
            ? ` ${recommendation.recommended_quantity} units`
            : ''),
      )
      return recommendation
    },
    [api, log],
  )

  const bootstrap = useCallback(async () => {
    await withBusy('bootstrap', async () => {
      dispatch({ type: 'reset' })
      dispatch({ type: 'error', error: null })
      log('info', 'Creating purchasing case…')
      const created = await api.createCase(DEFAULT_CASE)
      dispatch({ type: 'case', value: created })
      log('info', `Case ${created.case_id.slice(0, 8)} created (${created.sku}).`)

      let caseDetail: CaseDetail = created
      if (mode === 'mock') {
        const evidence = mock!.getScenarioEvidence()
        await api.putEvidence(created.case_id, evidence)
        log('info', 'Submitted gathered evidence.')
        caseDetail = await api.getCase(created.case_id)
        dispatch({ type: 'case', value: caseDetail })
      }

      if (caseDetail.evidence) {
        await generate(caseDetail.case_id)
        const refreshed = await api.getCase(caseDetail.case_id)
        dispatch({ type: 'case', value: refreshed })
      } else {
        log(
          'warning',
          'No evidence is present. Submit evidence before running the investigation.',
        )
      }
      dispatch({ type: 'ready', value: true })
    })
  }, [api, generate, log, mock, mode, withBusy])

  const changeScenario = useCallback(
    async (id: ScenarioId) => {
      if (!mock) return
      mock.setScenario(id)
      bootstrapped.current = false
      await bootstrap()
    },
    [bootstrap, mock],
  )

  const loadSampleEvidence = useCallback(async () => {
    await withBusy('evidence', async () => {
      const current = stateRef.current.caseDetail
      if (!current) return
      const evidence = mode === 'mock' ? mock!.getScenarioEvidence() : SAMPLE_EVIDENCE
      await api.putEvidence(current.case_id, evidence)
      log('info', 'Evidence submitted (sample fixture).')
      await refreshCase()
    })
  }, [api, log, mock, mode, refreshCase, withBusy])

  const runInvestigation = useCallback(async () => {
    await withBusy('investigate', async () => {
      const current = stateRef.current.caseDetail
      if (!current?.evidence) return
      log('info', 'Running the investigation…')
      await generate(current.case_id)
      await refreshCase()
    })
  }, [generate, log, refreshCase, withBusy])

  const decide = useCallback(
    async (decision: Approval['decision'], note?: string) => {
      await withBusy('decision', async () => {
        const { caseDetail, recommendation } = stateRef.current
        if (!caseDetail || !recommendation) return
        const approval = await api.decide(caseDetail.case_id, recommendation.version_number, {
          decision,
          approved_quantity:
            decision === 'approved' ? recommendation.recommended_quantity ?? undefined : undefined,
          approver: options.approver,
          note,
        })
        dispatch({ type: 'approval', value: approval })
        log(
          decision === 'approved' ? 'success' : 'warning',
          `Buyer ${decision} recommendation v${recommendation.version_number}.`,
        )
        await refreshCase()
      })
    },
    [api, log, options.approver, refreshCase, withBusy],
  )

  const execute = useCallback(async () => {
    await withBusy('execute', async () => {
      const { caseDetail, recommendation, approval, idempotencyKey } = stateRef.current
      if (!caseDetail || !recommendation || !approval) return
      const key = idempotencyKey ?? newIdempotencyKey()
      if (!idempotencyKey) dispatch({ type: 'idempotency_key', value: key })
      try {
        const order: PurchaseOrder = await api.executePurchase(
          caseDetail.case_id,
          {
            recommendation_version_number: recommendation.version_number,
            approval_id: approval.approval_id,
          },
          key,
        )
        dispatch({ type: 'purchase_order', value: order })
        log('success', `Mock purchase order ${order.external_po_id ?? order.purchase_order_id} created.`)
      } finally {
        // Always refresh: on failure the case moves to execution_failed.
        await refreshCase()
      }
    })
  }, [api, log, refreshCase, withBusy])

  const validate = useCallback(async () => {
    await withBusy('validate', async () => {
      const { caseDetail, purchaseOrder } = stateRef.current
      if (!caseDetail || !purchaseOrder) return
      const result: ValidationResult = await api.validatePurchase(
        caseDetail.case_id,
        purchaseOrder.purchase_order_id,
      )
      dispatch({ type: 'validation', value: result })
      log(
        result.status === 'passed' ? 'success' : 'error',
        `Independent validation: ${result.status}.`,
      )
      await refreshCase()
    })
  }, [api, log, refreshCase, withBusy])

  const dismissError = useCallback(() => {
    dispatch({ type: 'error', error: null })
  }, [])

  useEffect(() => {
    if (options.autoBootstrap !== false && !bootstrapped.current) {
      bootstrapped.current = true
      void bootstrap()
    }
  }, [bootstrap, options.autoBootstrap])

  return useMemo(
    () => ({
      state,
      apiMode: mode,
      mock,
      scenarioId: mock?.getScenarioId(),
      bootstrap,
      changeScenario,
      loadSampleEvidence,
      runInvestigation,
      approve: (note?: string) => decide('approved', note),
      reject: (note?: string) => decide('rejected', note),
      execute,
      validate,
      refreshCase,
      dismissError,
    }),
    [
      state,
      mode,
      mock,
      bootstrap,
      changeScenario,
      loadSampleEvidence,
      runInvestigation,
      decide,
      execute,
      validate,
      refreshCase,
      dismissError,
    ],
  )
}

function newIdempotencyKey(): string {
  const globalCrypto = globalThis.crypto
  if (globalCrypto && typeof globalCrypto.randomUUID === 'function') {
    return globalCrypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0
    const value = char === 'x' ? random : (random & 0x3) | 0x8
    return value.toString(16)
  })
}
