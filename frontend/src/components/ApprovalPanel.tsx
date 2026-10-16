import { useEffect, useState } from 'react'
import type { Approval, CaseStatus, Recommendation } from '../api/types'
import { formatDateTime, formatMoney, formatNumber } from '../utils/format'
import { isExecutable, reviewQuantity } from '../workflow/state'
import { Panel } from './Panel'
import { StatusPill } from './StatusPill'

interface ApprovalPanelProps {
  recommendation: Recommendation
  approval: Approval | null
  caseStatus: CaseStatus
  busy: boolean
  onApprove: (note: string) => void
  onReject: (note: string) => void
}

export function ApprovalPanel({
  recommendation,
  approval,
  caseStatus,
  busy,
  onApprove,
  onReject,
}: ApprovalPanelProps) {
  const recommended = recommendation.recommended_quantity
  const [note, setNote] = useState('')
  const [targetQuantity, setTargetQuantity] = useState(
    recommended === null ? '' : String(recommended),
  )

  useEffect(() => {
    setTargetQuantity(recommended === null ? '' : String(recommended))
    setNote('')
  }, [recommendation.version_number, recommended])

  const executable = isExecutable(recommendation)
  const isCurrent = caseStatus === 'awaiting_approval'

  const parsedTarget = Number(targetQuantity)
  const validTarget = targetQuantity.trim() !== '' && !Number.isNaN(parsedTarget)
  const review = validTarget
    ? reviewQuantity(recommendation, recommendation.evidence ?? null, parsedTarget)
    : null

  return (
    <Panel
      title="Buyer decision"
      subtitle={`Tied to recommendation version ${recommendation.version_number}`}
    >
      {!executable ? (
        <div className="callout callout--warning">
          Purchase blocked. This <strong>{recommendation.decision}</strong> recommendation
          cannot be approved here while required checks fail, are unknown or missing, or
          the decision is not executable. Resolve the investigation first.
        </div>
      ) : null}

      {approval ? (
        <div className={`callout ${approval.decision === 'approved' ? 'callout--success' : 'callout--danger'}`}>
          <h4>
            {approval.decision === 'approved' ? 'Approved' : 'Rejected'} by {approval.approver}
          </h4>
          <ul className="bullets">
            <li>Approval ID: <code className="mono">{approval.approval_id}</code></li>
            <li>Version: {approval.recommendation_version_number}</li>
            {approval.approved_quantity !== null && approval.approved_quantity !== undefined ? (
              <li>Approved quantity: {formatNumber(approval.approved_quantity)} units</li>
            ) : null}
            {approval.total_cost ? <li>Approved cost: {formatMoney(approval.total_cost, approval.currency)}</li> : null}
            <li>Recorded: {formatDateTime(approval.created_at)}</li>
            {approval.note ? <li>Note: {approval.note}</li> : null}
          </ul>
        </div>
      ) : !executable ? null : (
        <>
          <label className="field">
            <span className="field__label">Review quantity (units)</span>
            <input
              className="input"
              type="number"
              min={0}
              value={targetQuantity}
              onChange={(event) => setTargetQuantity(event.target.value)}
              disabled={busy || !isCurrent}
              aria-label="Review quantity"
            />
          </label>

          {review ? (
            <div className={`callout ${review.matchesRecommendation ? 'callout--success' : 'callout--warning'}`}>
              {review.matchesRecommendation ? (
                <p>
                  Quantity matches the recommendation ({formatNumber(recommended)} units). Approval
                  is allowed.
                </p>
              ) : (
                <>
                  <p>
                    Quantity differs from the recommendation by {review.delta > 0 ? '+' : ''}
                    {formatNumber(review.delta)} units. An approval must match the exact
                    recommendation; a changed quantity requires a new recommendation version.
                  </p>
                  <p className="muted">
                    Preview only (not authoritative): cost {formatMoney(review.previewCost, recommendation.currency)}
                    {review.withinBudget === null
                      ? ''
                      : `, budget ${review.withinBudget ? 'ok' : 'exceeded'}`}
                    {review.withinStorage === null
                      ? ''
                      : `, storage ${review.withinStorage ? 'ok' : 'exceeded'}`}
                    .
                  </p>
                </>
              )}
            </div>
          ) : (
            <p className="muted">Enter a quantity to review.</p>
          )}

          <label className="field">
            <span className="field__label">Note (optional)</span>
            <input
              className="input"
              type="text"
              value={note}
              placeholder="Reason or instruction"
              onChange={(event) => setNote(event.target.value)}
              disabled={busy || !isCurrent}
            />
          </label>

          <div className="actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy || !isCurrent || !review?.matchesRecommendation}
              onClick={() => onApprove(note)}
            >
              Approve purchase
            </button>
            <button
              type="button"
              className="btn btn--danger"
              disabled={busy || !isCurrent}
              onClick={() => onReject(note)}
            >
              Reject
            </button>
          </div>

          {!isCurrent ? (
            <p className="muted">
              <StatusPill tone="neutral">{caseStatus.replace(/_/g, ' ')}</StatusPill> — a decision can
              only be recorded while the case is awaiting approval.
            </p>
          ) : null}
        </>
      )}
    </Panel>
  )
}
