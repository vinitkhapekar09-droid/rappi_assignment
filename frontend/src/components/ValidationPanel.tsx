import type { PurchaseOrder, ValidationResult } from '../api/types'
import { formatDateTime, titleCase } from '../utils/format'
import { Panel } from './Panel'
import { StatusPill, type Tone } from './StatusPill'

const VALIDATION_TONE: Record<ValidationResult['status'], Tone> = {
  passed: 'success',
  failed: 'danger',
  inconclusive: 'warning',
}

interface ValidationPanelProps {
  purchaseOrder: PurchaseOrder | null
  validation: ValidationResult | null
  busy: boolean
  onValidate: () => void
}

export function ValidationPanel({
  purchaseOrder,
  validation,
  busy,
  onValidate,
}: ValidationPanelProps) {
  const canValidate = Boolean(purchaseOrder) && purchaseOrder?.status === 'created'

  return (
    <Panel
      title="Independent validation"
      subtitle="Re-reads the persisted purchase order and compares it with the approval"
    >
      <div className="actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || !canValidate}
          onClick={onValidate}
        >
          Run independent validation
        </button>
      </div>

      {!purchaseOrder ? (
        <p className="muted">Execute the approved purchase first.</p>
      ) : !validation ? (
        <p className="muted">
          A successful HTTP response is not proof of success. Run validation to verify the persisted
          order.
        </p>
      ) : (
        <div className="subpanel">
          <div className="subpanel__header">
            <h3 className="subhead">Validation result</h3>
            <StatusPill tone={VALIDATION_TONE[validation.status]}>
              {titleCase(validation.status)}
            </StatusPill>
          </div>

          {validation.status === 'failed' ? (
            <div className="callout callout--danger">
              The persisted purchase order does not match the approved request. Automatic purchasing
              is stopped for this action and buyer intervention is required. No automatic retry.
            </div>
          ) : null}

          <table className="table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Approved</th>
                <th>Persisted</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(validation.checked_fields).map(([field, value]) => {
                const mismatch = validation.mismatches.find((m) => m.field === field)
                return (
                  <tr key={field} className={mismatch ? 'row--mismatch' : undefined}>
                    <td>{field}</td>
                    <td>{mismatch ? String(mismatch.approved_value) : String(value)}</td>
                    <td>{mismatch ? String(mismatch.persisted_value) : String(value)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <h4 className="subhead">Persisted snapshot</h4>
          <pre className="code-block">{JSON.stringify(validation.persisted_snapshot, null, 2)}</pre>
          <p className="muted">Checked at {formatDateTime(validation.created_at)}.</p>
        </div>
      )}
    </Panel>
  )
}
