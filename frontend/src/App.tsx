import { useMemo } from 'react'
import { createApi } from './api'
import { usePurchasingWorkflow } from './workflow/usePurchasingWorkflow'
import { describeStatus, isExecutable } from './workflow/state'
import { ActivityLog } from './components/ActivityLog'
import { ApprovalPanel } from './components/ApprovalPanel'
import { CaseEvidence } from './components/CaseEvidence'
import { ConstraintChecks } from './components/ConstraintChecks'
import { ExecutionPanel } from './components/ExecutionPanel'
import { Panel } from './components/Panel'
import { RationalePanel } from './components/RationalePanel'
import { RecommendationSummary } from './components/RecommendationSummary'
import { ScenarioSelector } from './components/ScenarioSelector'
import { StatusBanner } from './components/StatusBanner'
import { StatusPill } from './components/StatusPill'
import { ValidationPanel } from './components/ValidationPanel'

const APPROVER = 'buyer@rappi.example'

export default function App() {
  const bundle = useMemo(() => createApi(), [])
  const wf = usePurchasingWorkflow(bundle, { approver: APPROVER })
  const { state } = wf
  const status = describeStatus(state)
  const recommendation = state.recommendation
  const caseDetail = state.caseDetail

  const busy = state.busy !== null

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1>Rappi AI Purchasing Agent</h1>
          <p className="app__subtitle">
            Review the initial 800-unit recommendation, approve or reject a specific version, execute
            through the mock purchasing API, and verify the persisted result independently.
          </p>
        </div>
        <div className="app__mode">
          <StatusPill tone={wf.apiMode === 'mock' ? 'info' : 'neutral'}>
            {wf.apiMode === 'mock' ? 'Mock adapter' : 'Live API'}
          </StatusPill>
          <span className="muted">Approver: {APPROVER}</span>
        </div>
      </header>

      <StatusBanner
        descriptor={status}
        errorCode={state.error?.code}
        onDismiss={state.error ? wf.dismissError : undefined}
      />

      <div className="toolbar">
        <div className="toolbar__group">
          <button
            type="button"
            className="btn"
            disabled={busy || !caseDetail?.evidence}
            onClick={() => void wf.runInvestigation()}
          >
            Run investigation
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => void wf.loadSampleEvidence()}
          >
            Load sample evidence
          </button>
        </div>
        {wf.apiMode === 'mock' && wf.scenarioId ? (
          <ScenarioSelector
            value={wf.scenarioId}
            disabled={busy}
            onChange={(id) => void wf.changeScenario(id)}
          />
        ) : null}
      </div>

      {!caseDetail ? (
        <Panel title="Loading">
          <p className="muted">Preparing the purchasing case…</p>
        </Panel>
      ) : (
        <main className="grid">
          <div className="grid__column">
            <CaseEvidence caseDetail={caseDetail} />

            {recommendation ? (
              <>
                <RecommendationSummary recommendation={recommendation} />
                <ConstraintChecks checks={recommendation.constraint_checks} />
              </>
            ) : (
              <Panel title="Recommendation">
                <p className="muted">No recommendation has been generated yet.</p>
              </Panel>
            )}
          </div>

          <div className="grid__column">
            {recommendation ? (
              <>
                <RationalePanel recommendation={recommendation} />
                <ApprovalPanel
                  recommendation={recommendation}
                  approval={state.approval}
                  caseStatus={caseDetail.status}
                  busy={busy}
                  onApprove={(note) => void wf.approve(note)}
                  onReject={(note) => void wf.reject(note)}
                />
              </>
            ) : null}

            {recommendation && isExecutable(recommendation) ? (
              <>
                <ExecutionPanel
                  recommendation={recommendation}
                  approval={state.approval}
                  purchaseOrder={state.purchaseOrder}
                  caseStatus={caseDetail.status}
                  busy={busy}
                  idempotencyKey={state.idempotencyKey}
                  onExecute={() => void wf.execute()}
                />
                <ValidationPanel
                  purchaseOrder={state.purchaseOrder}
                  validation={state.validation}
                  busy={busy}
                  onValidate={() => void wf.validate()}
                />
              </>
            ) : null}

            <ActivityLog entries={state.log} />
          </div>
        </main>
      )}
    </div>
  )
}
