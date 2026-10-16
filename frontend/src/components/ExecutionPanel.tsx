import type { Approval, CaseStatus, PurchaseOrder, Recommendation } from '../api/types'
import { formatDateTime, formatMoney, formatNumber, titleCase } from '../utils/format'
import { Panel } from './Panel'
import { StatusPill, type Tone } from './StatusPill'

const PO_TONE: Record<PurchaseOrder['status'], Tone> = {
  created: 'info',
  failed: 'danger',
  validated: 'success',
  validation_failed: 'danger',
}

interface ExecutionPanelProps {
  recommendation: Recommendation
  approval: Approval | null
  purchaseOrder: PurchaseOrder | null
  caseStatus: CaseStatus
  busy: boolean
  idempotencyKey: string | null
  onExecute: () => void
}

export function ExecutionPanel({
  recommendation,
  approval,
  purchaseOrder,
  caseStatus,
  busy,
  idempotencyKey,
  onExecute,
}: ExecutionPanelProps) {
  const approved = approval?.decision === 'approved'
  const canExecute = caseStatus === 'approved' && approved
  const canReconcile = caseStatus === 'executing' && approved && Boolean(idempotencyKey)

  return (
    <Panel
      title="Execution (mock purchasing API)"
      subtitle="Creating a purchase order does not by itself mean success"
    >
      <dl className="meta-grid">
        <Meta label="Case status" value={titleCase(caseStatus)} />
        <Meta label="Recommendation version" value={recommendation.version_number} />
        <Meta
          label="Idempotency key"
          value={idempotencyKey ?? 'Generated on first execution'}
          mono
        />
      </dl>

      <div className="actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || !(canExecute || canReconcile)}
          onClick={onExecute}
        >
          {canReconcile ? 'Reconcile existing purchase' : purchaseOrder ? 'Re-submit same key' : 'Execute approved purchase'}
        </button>
      </div>

      {canReconcile ? (
        <p className="muted">Check the existing operation using the same key before any further attempt.</p>
      ) : !canExecute && !purchaseOrder ? (
        <p className="muted">Approve the current recommendation before executing.</p>
      ) : null}

      {purchaseOrder ? (
        <div className="subpanel">
          <div className="subpanel__header">
            <h3 className="subhead">Purchase order</h3>
            <StatusPill tone={PO_TONE[purchaseOrder.status]}>
              {titleCase(purchaseOrder.status)}
            </StatusPill>
          </div>
          <dl className="meta-grid">
            <Meta label="External PO ID" value={purchaseOrder.external_po_id ?? '—'} mono />
            <Meta label="Supplier" value={purchaseOrder.supplier_name} />
            <Meta label="Quantity" value={`${formatNumber(purchaseOrder.quantity)} units`} />
            <Meta label="Total cost" value={formatMoney(purchaseOrder.total_cost, purchaseOrder.currency)} />
            <Meta label="Created" value={formatDateTime(purchaseOrder.created_at)} />
          </dl>
          {purchaseOrder.execution_attempt ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Attempt</th>
                  <th>Outcome</th>
                  <th className="num">HTTP</th>
                  <th>Error</th>
                  <th>At</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{purchaseOrder.execution_attempt.attempt_number}</td>
                  <td>{purchaseOrder.execution_attempt.outcome}</td>
                  <td className="num">{purchaseOrder.execution_attempt.http_status ?? '—'}</td>
                  <td>{purchaseOrder.execution_attempt.error_code ?? '—'}</td>
                  <td>{formatDateTime(purchaseOrder.execution_attempt.created_at)}</td>
                </tr>
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}

function Meta({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="meta">
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{value}</dd>
    </div>
  )
}
