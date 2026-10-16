import type {
  Approval,
  CaseDetail,
  Evidence,
  PurchaseOrder,
  Recommendation,
  ValidationResult,
} from '../api/types'

export type BusyAction = 'bootstrap' | 'evidence' | 'investigate' | 'decision' | 'execute' | 'validate'

export interface WorkflowError {
  code: string
  message: string
  status?: number
  details?: Record<string, unknown>
}

export type LogLevel = 'info' | 'success' | 'warning' | 'error'

export interface LogEntry {
  id: number
  at: string
  level: LogLevel
  message: string
}

export interface WorkflowState {
  ready: boolean
  caseDetail: CaseDetail | null
  recommendation: Recommendation | null
  approval: Approval | null
  purchaseOrder: PurchaseOrder | null
  validation: ValidationResult | null
  idempotencyKey: string | null
  busy: BusyAction | null
  error: WorkflowError | null
  log: LogEntry[]
}

export type WorkflowAction =
  | { type: 'reset' }
  | { type: 'busy'; action: BusyAction | null }
  | { type: 'error'; error: WorkflowError | null }
  | { type: 'case'; value: CaseDetail }
  | { type: 'recommendation'; value: Recommendation | null }
  | { type: 'approval'; value: Approval | null }
  | { type: 'purchase_order'; value: PurchaseOrder | null }
  | { type: 'validation'; value: ValidationResult | null }
  | { type: 'idempotency_key'; value: string | null }
  | { type: 'log'; level: LogLevel; message: string }
  | { type: 'ready'; value: boolean }

let logSequence = 0

export const initialWorkflowState: WorkflowState = {
  ready: false,
  caseDetail: null,
  recommendation: null,
  approval: null,
  purchaseOrder: null,
  validation: null,
  idempotencyKey: null,
  busy: null,
  error: null,
  log: [],
}

export function workflowReducer(state: WorkflowState, action: WorkflowAction): WorkflowState {
  switch (action.type) {
    case 'reset':
      return { ...initialWorkflowState }
    case 'busy':
      return { ...state, busy: action.action }
    case 'error':
      return { ...state, error: action.error, busy: null }
    case 'case':
      return { ...state, caseDetail: action.value }
    case 'recommendation':
      return { ...state, recommendation: action.value }
    case 'approval':
      return { ...state, approval: action.value }
    case 'purchase_order':
      return { ...state, purchaseOrder: action.value }
    case 'validation':
      return { ...state, validation: action.value }
    case 'idempotency_key':
      return { ...state, idempotencyKey: action.value }
    case 'ready':
      return { ...state, ready: action.value }
    case 'log':
      logSequence += 1
      return {
        ...state,
        log: [
          ...state.log,
          {
            id: logSequence,
            at: new Date().toISOString(),
            level: action.level,
            message: action.message,
          },
        ],
      }
  }
}

// --- derived UI status ---------------------------------------------------

export type UiStatus =
  | 'loading'
  | 'draft'
  | 'awaiting_approval'
  | 'approved'
  | 'executing'
  | 'validating'
  | 'validated'
  | 'rejected'
  | 'needs_investigation'
  | 'execution_failed'
  | 'validation_failed'
  | 'error'

export interface StatusDescriptor {
  status: UiStatus
  label: string
  tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger'
  detail: string
}

const CASE_STATUS_DESCRIPTORS: Record<string, StatusDescriptor> = {
  draft: {
    status: 'draft',
    label: 'Draft',
    tone: 'neutral',
    detail: 'Evidence has been submitted. Run the investigation to generate a recommendation.',
  },
  investigating: {
    status: 'loading',
    label: 'Investigating',
    tone: 'info',
    detail: 'Gathering evidence and evaluating constraints.',
  },
  awaiting_approval: {
    status: 'awaiting_approval',
    label: 'Awaiting buyer approval',
    tone: 'warning',
    detail: 'A purchase cannot proceed until the buyer explicitly approves this version.',
  },
  approved: {
    status: 'approved',
    label: 'Approved',
    tone: 'success',
    detail: 'An approval is tied to this exact recommendation version and quantity.',
  },
  executing: {
    status: 'executing',
    label: 'Executing',
    tone: 'info',
    detail: 'Submitting the approved purchase to the mock purchasing service.',
  },
  validating: {
    status: 'validating',
    label: 'Validating',
    tone: 'info',
    detail: 'Independently re-reading the persisted purchase order.',
  },
  validated: {
    status: 'validated',
    label: 'Validated',
    tone: 'success',
    detail: 'The persisted purchase order matches the approved recommendation.',
  },
  rejected: {
    status: 'rejected',
    label: 'Rejected',
    tone: 'danger',
    detail: 'The buyer rejected the recommendation. No purchase will be made.',
  },
  needs_investigation: {
    status: 'needs_investigation',
    label: 'Needs investigation',
    tone: 'warning',
    detail: 'The recommendation is not executable and requires buyer investigation.',
  },
  execution_failed: {
    status: 'execution_failed',
    label: 'Execution failed',
    tone: 'danger',
    detail: 'The purchase failed. Automatic purchasing is stopped for this action.',
  },
  validation_failed: {
    status: 'validation_failed',
    label: 'Validation failed',
    tone: 'danger',
    detail: 'The persisted purchase order does not match the approval. Buyer intervention required.',
  },
}

export function describeStatus(state: WorkflowState): StatusDescriptor {
  if (state.error) {
    return {
      status: 'error',
      label: 'Error',
      tone: 'danger',
      detail: state.error.message,
    }
  }
  if (!state.caseDetail) {
    return {
      status: 'loading',
      label: 'Loading',
      tone: 'info',
      detail: 'Preparing the purchasing case.',
    }
  }
  if (state.caseDetail.status === 'draft' && !state.caseDetail.evidence) {
    return {
      status: 'draft', label: 'Evidence needed', tone: 'warning',
      detail: 'No evidence has been submitted. Load sample evidence before running the investigation.',
    }
  }
  if (state.recommendation && !isExecutable(state.recommendation) &&
      ['awaiting_approval', 'approved'].includes(state.caseDetail.status)) {
    return {
      status: 'needs_investigation', label: 'Purchase blocked', tone: 'warning',
      detail: 'Resolve failed, unknown or missing required checks before purchase approval.',
    }
  }
  return (
    CASE_STATUS_DESCRIPTORS[state.caseDetail.status] ?? {
      status: 'draft',
      label: state.caseDetail.status,
      tone: 'neutral',
      detail: 'Unknown case status.',
    }
  )
}

export function isExecutable(recommendation: Recommendation | null): boolean {
  if (!recommendation) return false
  return (
    (recommendation.decision === 'accept' || recommendation.decision === 'modify') &&
    recommendation.recommended_quantity !== null &&
    ['supplier_availability', 'lead_time', 'moq', 'budget', 'storage', 'data_quality'].every(
      type => recommendation.constraint_checks.some(c => c.constraint_type === type && c.status === 'pass'),
    )
  )
}

export interface QuantityReview {
  targetQuantity: number
  matchesRecommendation: boolean
  delta: number
  /** Client-side preview only; never authoritative. */
  previewCost: string | null
  withinBudget: boolean | null
  withinStorage: boolean | null
}

export type QuantityReviewEvidence = Pick<
  Evidence,
  'budget_amount' | 'storage_capacity_units' | 'storage_required_per_unit'
>

export function reviewQuantity(
  recommendation: Recommendation,
  evidence: QuantityReviewEvidence | null,
  targetQuantity: number,
): QuantityReview {
  const recommended = recommendation.recommended_quantity
  const delta = recommended === null ? targetQuantity : targetQuantity - recommended
  const unitPrice = recommendation.unit_price ? Number(recommendation.unit_price) : null
  const previewCost =
    unitPrice !== null ? (targetQuantity * unitPrice).toFixed(2) : null

  let withinBudget: boolean | null = null
  if (previewCost !== null && evidence?.budget_amount != null) {
    withinBudget = Number(previewCost) <= Number(evidence.budget_amount)
  }

  let withinStorage: boolean | null = null
  if (
    evidence?.storage_capacity_units != null &&
    evidence?.storage_required_per_unit != null
  ) {
    withinStorage =
      targetQuantity * Number(evidence.storage_required_per_unit) <=
      Number(evidence.storage_capacity_units)
  }

  return {
    targetQuantity,
    matchesRecommendation: recommended !== null && targetQuantity === recommended,
    delta,
    previewCost,
    withinBudget,
    withinStorage,
  }
}
