import type { CaseDetail } from '../api/types'
import { formatDate, formatDateTime, formatMoney, formatNumber, titleCase } from '../utils/format'
import { EmptyState, Panel } from './Panel'
import { StatusPill } from './StatusPill'

export function CaseEvidence({ caseDetail }: { caseDetail: CaseDetail }) {
  const evidence = caseDetail.evidence
  const horizonStart = caseDetail.planning_horizon_start
  const horizonEnd = caseDetail.planning_horizon_end

  return (
    <Panel
      title="Purchasing case & gathered evidence"
      subtitle={`${caseDetail.sku} · ${caseDetail.product_name}`}
    >
      <dl className="meta-grid">
        <Meta label="Case ID" value={caseDetail.case_id} mono />
        <Meta label="Currency" value={caseDetail.currency} />
        <Meta label="Volume unit" value={caseDetail.volume_unit} />
        <Meta
          label="Planning horizon"
          value={`${formatDate(horizonStart)} → ${formatDate(horizonEnd)} (${caseDetail.planning_horizon_days} days)`}
        />
        <Meta label="Initial recommendation" value={`${formatNumber(caseDetail.initial_recommended_quantity)} units`} />
        <Meta label="Case status" value={titleCase(caseDetail.status)} />
        <Meta
          label="Current recommendation version"
          value={caseDetail.current_recommendation_version ?? '—'}
        />
        <Meta label="Updated" value={formatDateTime(caseDetail.updated_at)} />
      </dl>

      {!evidence ? (
        <EmptyState>No evidence has been submitted yet.</EmptyState>
      ) : (
        <>
          <h3 className="subhead">Decision inputs</h3>
          <dl className="meta-grid">
            <Meta label="Usable inventory" value={`${formatNumber(evidence.usable_inventory)} units`} />
            <Meta label="Forecast demand (7d)" value={`${formatNumber(evidence.forecast_demand)} units`} />
            <Meta label="Safety stock" value={`${formatNumber(evidence.safety_stock)} units`} />
            <Meta label="Available budget" value={formatMoney(evidence.budget_amount, caseDetail.currency)} />
            <Meta label="Storage capacity" value={decimalWithUnit(evidence.storage_capacity_units, caseDetail.volume_unit)} />
            <Meta
              label="Storage per unit"
              value={decimalWithUnit(evidence.storage_required_per_unit, caseDetail.volume_unit)}
            />
            <Meta label="Evidence as of" value={formatDateTime(evidence.evidence_as_of)} />
          </dl>

          <h3 className="subhead">Incoming orders</h3>
          {evidence.incoming_orders.length === 0 ? (
            <EmptyState>No incoming orders recorded.</EmptyState>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Reference</th>
                  <th className="num">Quantity</th>
                  <th>Expected arrival</th>
                  <th>Status</th>
                  <th>Eligible supply</th>
                </tr>
              </thead>
              <tbody>
                {evidence.incoming_orders.map((order, index) => {
                  const eligible =
                    order.status === 'confirmed' &&
                    order.expected_arrival_date >= horizonStart &&
                    order.expected_arrival_date <= horizonEnd
                  return (
                    <tr key={order.incoming_order_id ?? index}>
                      <td>{order.external_ref ?? '—'}</td>
                      <td className="num">{formatNumber(order.quantity)}</td>
                      <td>{formatDate(order.expected_arrival_date)}</td>
                      <td>{order.status}</td>
                      <td>
                        {eligible ? (
                          <StatusPill tone="success">Counts</StatusPill>
                        ) : (
                          <StatusPill tone="neutral">Not counted</StatusPill>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}

          <h3 className="subhead">Supplier offers</h3>
          {evidence.supplier_offers.length === 0 ? (
            <EmptyState>No supplier offers recorded.</EmptyState>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Supplier</th>
                  <th className="num">Unit price</th>
                  <th className="num">MOQ</th>
                  <th className="num">Lead time</th>
                  <th>Available</th>
                  <th>Eligible</th>
                </tr>
              </thead>
              <tbody>
                {evidence.supplier_offers.map((offer, index) => {
                  const eligible = offer.is_available && offer.lead_time_days <= caseDetail.planning_horizon_days - 1
                  return (
                    <tr key={offer.supplier_offer_id ?? index}>
                      <td>{offer.supplier_name}</td>
                      <td className="num">{formatMoney(offer.unit_price, offer.currency)}</td>
                      <td className="num">{formatNumber(offer.moq)}</td>
                      <td className="num">{offer.lead_time_days} days</td>
                      <td>{offer.is_available ? 'Yes' : 'No'}</td>
                      <td>
                        {eligible ? (
                          <StatusPill tone="success">Eligible</StatusPill>
                        ) : (
                          <StatusPill tone="warning">Not eligible</StatusPill>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </>
      )}
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

function decimalWithUnit(value: string | null, unit: string): string {
  if (value === null || value === '') return '—'
  return `${value} ${unit}`
}
