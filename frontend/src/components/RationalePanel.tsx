import type { Recommendation } from '../api/types'
import { Panel } from './Panel'

export function RationalePanel({ recommendation }: { recommendation: Recommendation }) {
  const trace = recommendation.constraint_checks.find(c => c.constraint_type === 'data_quality')?.details?.agent_workflow
  const inference = trace?.inference
  const online = inference?.mode === 'online' && inference.online_succeeded
  const interpretation = trace?.interpretation
  return (
    <Panel title="Decision rationale & assumptions">
      <div className={`callout ${online ? 'callout--success' : 'callout--warning'}`}>
        <strong>{online ? 'Online LLM inference succeeded' : inference ? 'Offline fallback — no successful online inference' : 'Inference status unavailable for this version'}</strong>
        {inference ? <>
          <p>Explanation provider: {inference.provider} · Model: {inference.model ?? 'None (deterministic)'}</p>
          {!online ? <p>Requested provider: {inference.requested_provider} · Model: {inference.requested_model ?? 'Not configured'}</p> : null}
          {inference.fallback_reason ? <p>{inference.fallback_reason}</p> : null}
        </> : null}
        {trace?.warnings?.length ? <ul>{trace.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul> : null}
      </div>
      {interpretation ? <>
        <h4 className="subhead">Structured interpretation</h4>
        <p>{interpretation.summary}</p>
        {(['key_evidence', 'trade_offs', 'caveats'] as const).map(field => interpretation[field].length ? <div key={field}>
          <h4 className="subhead">{field.replace(/_/g, ' ')}</h4>
          <ul>{interpretation[field].map((text, i) => <li key={i}>{text}</li>)}</ul>
        </div> : null)}
      </> : null}
      <p className="rationale">{recommendation.rationale}</p>

      {recommendation.missing_information.length > 0 ? (
        <div className="callout callout--warning">
          <h4>Missing / contradictory information</h4>
          <ul>
            {recommendation.missing_information.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {recommendation.assumptions.length > 0 ? (
        <>
          <h4 className="subhead">Assumptions</h4>
          <ul className="bullets">
            {recommendation.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  )
}
