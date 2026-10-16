import type {
  CaseStatus,
  ConstraintCheck,
  RecommendationDecision,
} from '../api/types'

export function formatMoney(value: string | number | null | undefined, currency = 'MXN'): string {
  if (value === null || value === undefined || value === '') return '—'
  const amount = Number(value)
  if (Number.isNaN(amount)) return String(value)
  return `${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`
}

export function formatNumber(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const amount = Number(value)
  if (Number.isNaN(amount)) return String(value)
  return amount.toLocaleString('en-US')
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return value
}

export function decisionLabel(decision: RecommendationDecision): string {
  return {
    accept: 'Accept recommendation',
    modify: 'Modify quantity',
    reject: 'Reject — no purchase',
    investigate: 'Investigate / escalate',
  }[decision]
}

export function caseStatusLabel(status: CaseStatus): string {
  return status.replace(/_/g, ' ')
}

export function constraintLabel(type: ConstraintCheck['constraint_type']): string {
  return {
    supplier_availability: 'Supplier availability',
    lead_time: 'Lead time',
    moq: 'MOQ',
    budget: 'Budget',
    storage: 'Storage',
    data_quality: 'Data quality',
  }[type]
}

export function constraintStatusLabel(status: ConstraintCheck['status']): string {
  return { pass: 'Pass', fail: 'Fail', unknown: 'Unknown' }[status]
}

export function titleCase(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}
