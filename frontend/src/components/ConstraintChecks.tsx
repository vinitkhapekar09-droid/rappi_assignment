import type { ConstraintCheck } from '../api/types'
import { constraintLabel, constraintStatusLabel, formatNumber } from '../utils/format'
import { EmptyState, Panel } from './Panel'
import { StatusPill, type Tone } from './StatusPill'

const STATUS_TONE: Record<ConstraintCheck['status'], Tone> = {
  pass: 'success',
  fail: 'danger',
  unknown: 'warning',
}

export function ConstraintChecks({ checks }: { checks: ConstraintCheck[] }) {
  return (
    <Panel title="Constraint checks" subtitle="Enforced by deterministic code, not the model">
      {checks.length === 0 ? (
        <EmptyState>No constraints were evaluated.</EmptyState>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Constraint</th>
              <th>Status</th>
              <th className="num">Evaluated</th>
              <th className="num">Limit</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((check) => (
              <tr key={check.constraint_type}>
                <td>{constraintLabel(check.constraint_type)}</td>
                <td>
                  <StatusPill tone={STATUS_TONE[check.status]}>
                    {constraintStatusLabel(check.status)}
                  </StatusPill>
                </td>
                <td className="num">{renderValue(check.evaluated_value, check.unit)}</td>
                <td className="num">{renderValue(check.limit_value, check.unit)}</td>
                <td>{check.message ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

function renderValue(value: number | string | null | undefined, unit?: string | null): string {
  if (value === null || value === undefined || value === '') return '—'
  const rendered = typeof value === 'number' ? formatNumber(value) : value
  return unit ? `${rendered} ${unit}` : String(rendered)
}
