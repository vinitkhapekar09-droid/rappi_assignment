import { ApiClientError } from './errors'
import type { PurchasingApi } from './client'
import {
  DEFAULT_CASE,
  getScenario,
  SCENARIOS,
  type PersistedOverride,
  type Scenario,
  type ScenarioId,
} from './fixtures'
import type {
  Approval,
  CaseDetail,
  ConstraintCheck,
  CreateCaseRequest,
  DecisionRequest,
  Evidence,
  PurchaseOrder,
  PurchaseOrderDetail,
  Recommendation,
  RecommendationEvidence,
  RecommendationList,
  ValidationResult,
} from './types'

export interface MockPurchasingApi extends PurchasingApi {
  getScenarios(): Scenario[]
  getScenarioId(): ScenarioId
  setScenario(id: ScenarioId): void
  getScenarioEvidence(): Evidence
  reset(): void
}

interface IdempotencyEntry {
  fingerprint: string
  state: 'in_progress' | 'completed' | 'failed' | 'uncertain'
  response?: PurchaseOrder
}

interface MockStore {
  caseDetail: CaseDetail | null
  recommendations: Recommendation[]
  approvals: Approval[]
  purchaseOrders: Map<string, PurchaseOrderDetail>
  idempotency: Map<string, IdempotencyEntry>
  validations: Map<string, ValidationResult[]>
  versionCounter: number
}

/**
 * In-memory mock of the purchasing API. It implements the same workflow as the
 * contract (investigation -> approval -> execution -> independent validation),
 * including approval/version guards, idempotency, and a deliberately mismatched
 * persisted purchase order. This lets the dashboard be built and demoed while the
 * backend is still in progress. No backend routes are invented here.
 */
export function createMockApi(initialScenario: ScenarioId = 'happy'): MockPurchasingApi {
  let scenarioId: ScenarioId = initialScenario
  let store = freshStore()

  function freshStore(): MockStore {
    return {
      caseDetail: null,
      recommendations: [],
      approvals: [],
      purchaseOrders: new Map(),
      idempotency: new Map(),
      validations: new Map(),
      versionCounter: 0,
    }
  }

  function requireCase(caseId: string): CaseDetail {
    if (!store.caseDetail || store.caseDetail.case_id !== caseId) {
      throw new ApiClientError('not_found', `Case ${caseId} was not found.`, 404)
    }
    return store.caseDetail
  }

  function scenario(): Scenario {
    return getScenario(scenarioId)
  }

  return {
    getScenarios: () => SCENARIOS,
    getScenarioId: () => scenarioId,
    setScenario(id: ScenarioId) {
      scenarioId = id
      store = freshStore()
    },
    getScenarioEvidence: () => clone(scenario().evidence),
    reset() {
      store = freshStore()
    },

    async createCase(input: CreateCaseRequest): Promise<CaseDetail> {
      const now = nowIso()
      const horizonStart = input.planning_horizon_start ?? DEFAULT_CASE.planning_horizon_start
      const caseDetail: CaseDetail = {
        case_id: uuid(),
        sku: input.sku,
        product_name: input.product_name,
        currency: input.currency,
        volume_unit: input.volume_unit,
        planning_horizon_start: horizonStart,
        planning_horizon_days: 7,
        planning_horizon_end: addDaysLocal(horizonStart, 6),
        initial_recommended_quantity: input.initial_recommended_quantity ?? 800,
        status: 'draft',
        current_recommendation_version: null,
        evidence: null,
        created_at: now,
        updated_at: now,
      }
      store.caseDetail = caseDetail
      return clone(caseDetail)
    },

    async getCase(caseId: string): Promise<CaseDetail> {
      return clone(requireCase(caseId))
    },

    async putEvidence(caseId: string, evidence: Evidence): Promise<Evidence> {
      const caseDetail = requireCase(caseId)
      const normalized = normalizeEvidence(evidence)
      caseDetail.evidence = normalized
      if (caseDetail.status === 'awaiting_approval' || caseDetail.status === 'approved') {
        supersedeCurrent(caseDetail)
      }
      caseDetail.updated_at = nowIso()
      return clone(normalized)
    },

    async generateRecommendation(caseId: string): Promise<Recommendation> {
      const caseDetail = requireCase(caseId)
      if (!caseDetail.evidence) {
        throw new ApiClientError(
          'missing_evidence',
          'No evidence has been submitted for this case.',
          409,
        )
      }
      if (isLockedState(caseDetail.status)) {
        throw new ApiClientError(
          'invalid_state',
          `Cannot regenerate a recommendation while the case is ${caseDetail.status}.`,
          409,
        )
      }
      store.versionCounter += 1
      const version = store.versionCounter
      supersedeCurrent(caseDetail)
      const recommendation = computeRecommendation(
        caseDetail,
        caseDetail.evidence,
        version,
        uuid(),
        nowIso(),
      )
      store.recommendations.push(recommendation)
      caseDetail.current_recommendation_version = version
      caseDetail.status = caseStatusForDecision(recommendation.decision)
      caseDetail.updated_at = nowIso()
      return clone(recommendation)
    },

    async listRecommendations(caseId: string): Promise<RecommendationList> {
      const caseDetail = requireCase(caseId)
      return {
        case_id: caseId,
        current_recommendation_version: caseDetail.current_recommendation_version,
        recommendations: store.recommendations.map((r) => ({
          recommendation_id: r.recommendation_id,
          version_number: r.version_number,
          status: r.status,
          decision: r.decision,
          recommended_quantity: r.recommended_quantity,
          total_cost: r.total_cost ?? null,
          created_at: r.created_at,
        })),
      }
    },

    async getRecommendation(caseId: string, versionNumber: number): Promise<Recommendation> {
      requireCase(caseId)
      const found = store.recommendations.find((r) => r.version_number === versionNumber)
      if (!found) {
        throw new ApiClientError(
          'not_found',
          `Recommendation version ${versionNumber} was not found.`,
          404,
        )
      }
      return clone(found)
    },

    async decide(
      caseId: string,
      versionNumber: number,
      request: DecisionRequest,
    ): Promise<Approval> {
      const caseDetail = requireCase(caseId)
      const recommendation = store.recommendations.find(
        (r) => r.version_number === versionNumber,
      )
      if (!recommendation) {
        throw new ApiClientError('not_found', 'Recommendation version not found.', 404)
      }
      if (caseDetail.current_recommendation_version !== versionNumber) {
        throw new ApiClientError(
          'recommendation_superseded',
          `Version ${versionNumber} is no longer the current recommendation.`,
          409,
          { current_version: caseDetail.current_recommendation_version },
        )
      }
      const existing = store.approvals.find(
        (a) => a.recommendation_version_number === versionNumber,
      )
      if (existing) return clone(existing)

      if (request.decision === 'approved') {
        if (recommendation.decision !== 'accept' && recommendation.decision !== 'modify') {
          throw new ApiClientError(
            'invalid_state',
            `A "${recommendation.decision}" recommendation cannot be approved.`,
            409,
          )
        }
        if (request.approved_quantity !== recommendation.recommended_quantity) {
          throw new ApiClientError(
            'approval_mismatch',
            'The approved quantity differs from the recommendation. Change the quantity by regenerating a new version.',
            409,
            {
              approved_quantity: request.approved_quantity,
              recommended_quantity: recommendation.recommended_quantity,
            },
          )
        }
      }

      const unitPrice = recommendation.unit_price ?? null
      const quantity = recommendation.recommended_quantity
      const approval: Approval = {
        approval_id: uuid(),
        case_id: caseId,
        recommendation_version_number: versionNumber,
        decision: request.decision,
        approved_quantity: request.decision === 'approved' ? quantity : null,
        supplier_offer_id: recommendation.chosen_supplier_offer_id ?? null,
        supplier_name: recommendation.supplier_name ?? null,
        unit_price: unitPrice,
        total_cost:
          request.decision === 'approved' && quantity !== null && unitPrice !== null
            ? (quantity * Number(unitPrice)).toFixed(2)
            : null,
        currency: recommendation.currency,
        approver: request.approver,
        note: request.note ?? null,
        created_at: nowIso(),
      }
      store.approvals.push(approval)
      caseDetail.status = request.decision === 'approved' ? 'approved' : 'rejected'
      caseDetail.updated_at = nowIso()
      return clone(approval)
    },

    async executePurchase(
      caseId: string,
      body: { recommendation_version_number: number; approval_id: string },
      idempotencyKey: string,
    ): Promise<PurchaseOrder> {
      const caseDetail = requireCase(caseId)
      const fingerprint = `${caseId}:execute_purchase:${JSON.stringify(body)}`
      const claim = store.idempotency.get(idempotencyKey)

      if (claim) {
        if (claim.fingerprint !== fingerprint) {
          throw new ApiClientError(
            'idempotency_conflict',
            'This idempotency key was already used with a different request.',
            409,
          )
        }
        if (claim.state === 'completed' || claim.state === 'failed') {
          if (claim.response) return clone(claim.response)
        }
        if (claim.state === 'in_progress') {
          throw new ApiClientError(
            'execution_in_progress',
            'A purchase for this idempotency key is already in progress.',
            409,
          )
        }
      }

      store.idempotency.set(idempotencyKey, { fingerprint, state: 'in_progress' })

      if (caseDetail.status !== 'approved') {
        store.idempotency.delete(idempotencyKey)
        throw new ApiClientError(
          'invalid_state',
          `The case must be approved before execution (current status: ${caseDetail.status}).`,
          409,
        )
      }
      if (caseDetail.current_recommendation_version !== body.recommendation_version_number) {
        store.idempotency.delete(idempotencyKey)
        throw new ApiClientError(
          'recommendation_superseded',
          'The requested recommendation version is not current.',
          409,
        )
      }
      const approval = store.approvals.find((a) => a.approval_id === body.approval_id)
      const recommendation = store.recommendations.find(
        (r) => r.version_number === body.recommendation_version_number,
      )
      if (
        !approval ||
        approval.decision !== 'approved' ||
        !recommendation ||
        recommendation.decision === 'investigate' ||
        recommendation.decision === 'reject' ||
        !recommendation.constraint_checks.every((c) => c.status === 'pass')
      ) {
        store.idempotency.delete(idempotencyKey)
        throw new ApiClientError(
          'stale_approval',
          'No valid approval matches the current recommendation version.',
          409,
        )
      }

      const activeScenario = scenario()
      if (activeScenario.failExecution) {
        const failedOrder: PurchaseOrder = {
          purchase_order_id: uuid(),
          case_id: caseId,
          approval_id: approval.approval_id,
          recommendation_version_number: body.recommendation_version_number,
          supplier_name: approval.supplier_name ?? '',
          sku: caseDetail.sku,
          quantity: approval.approved_quantity ?? 0,
          unit_price: approval.unit_price ?? '0.00',
          total_cost: approval.total_cost ?? '0.00',
          currency: caseDetail.currency,
          status: 'failed',
          external_po_id: null,
          execution_attempt: {
            execution_attempt_id: uuid(),
            attempt_number: 1,
            outcome: 'failed',
            http_status: 502,
            error_code: 'execution_failed',
            created_at: nowIso(),
          },
          validation_status: null,
          created_at: nowIso(),
        }
        store.idempotency.set(idempotencyKey, {
          fingerprint,
          state: 'failed',
          response: failedOrder,
        })
        caseDetail.status = 'execution_failed'
        throw new ApiClientError(
          'execution_failed',
          'The mock purchasing service failed before creating a purchase order.',
          502,
        )
      }

      const overrides = activeScenario.persistOverrides ?? []
      const order: PurchaseOrderDetail = {
        purchase_order_id: uuid(),
        case_id: caseId,
        approval_id: approval.approval_id,
        recommendation_version_number: body.recommendation_version_number,
        supplier_name: overrideString(overrides, 'supplier_name', approval.supplier_name ?? ''),
        sku: overrideString(overrides, 'sku', caseDetail.sku),
        quantity: overrideNumber(
          overrides,
          'quantity',
          approval.approved_quantity ?? recommendation.recommended_quantity ?? 0,
        ),
        unit_price: overrideString(overrides, 'unit_price', approval.unit_price ?? '0.00'),
        total_cost: overrideString(overrides, 'total_cost', approval.total_cost ?? '0.00'),
        currency: overrideString(overrides, 'currency', caseDetail.currency),
        status: 'created',
        external_po_id: `MOCK-PO-${Math.floor(1000 + Math.random() * 9000)}`,
        execution_attempt: {
          execution_attempt_id: uuid(),
          attempt_number: 1,
          outcome: 'succeeded',
          http_status: 201,
          error_code: null,
          created_at: nowIso(),
        },
        validation_status: 'pending',
        line_items: [],
        execution_attempts: [],
        validation_results: [],
        created_at: nowIso(),
      }
      order.line_items = [
        {
          sku: order.sku,
          quantity: order.quantity,
          unit_price: order.unit_price,
          line_total: order.total_cost,
        },
      ]
      store.purchaseOrders.set(order.purchase_order_id, order)
      store.idempotency.set(idempotencyKey, {
        fingerprint,
        state: 'completed',
        response: clone(order),
      })
      caseDetail.status = 'validating'
      caseDetail.updated_at = nowIso()
      return clone(order)
    },

    async validatePurchase(caseId: string, purchaseOrderId: string): Promise<ValidationResult> {
      const caseDetail = requireCase(caseId)
      const order = store.purchaseOrders.get(purchaseOrderId)
      if (!order) {
        throw new ApiClientError('not_found', 'Purchase order not found.', 404)
      }
      if (scenario().mockUnavailableOnValidate) {
        throw new ApiClientError(
          'mock_unavailable',
          'The persisted purchase order could not be re-read from the mock service.',
          502,
        )
      }
      if (order.status === 'failed') {
        throw new ApiClientError(
          'invalid_state',
          'A failed purchase order is terminal and cannot be validated.',
          409,
        )
      }
      if (order.status === 'validated' || order.status === 'validation_failed') {
        const replayed = store.validations.get(purchaseOrderId)?.at(-1)
        if (replayed) return clone(replayed)
      }

      const approval = store.approvals.find((a) => a.approval_id === order.approval_id)
      const checked: Record<string, unknown> = {
        sku: order.sku,
        quantity: order.quantity,
        supplier_name: order.supplier_name,
        unit_price: order.unit_price,
        total_cost: order.total_cost,
        currency: order.currency,
      }
      const mismatches: ValidationResult['mismatches'] = []
      if (approval) {
        if (order.quantity !== approval.approved_quantity) {
          mismatches.push({
            field: 'quantity',
            approved_value: approval.approved_quantity,
            persisted_value: order.quantity,
          })
        }
        if (order.supplier_name !== approval.supplier_name) {
          mismatches.push({
            field: 'supplier_name',
            approved_value: approval.supplier_name,
            persisted_value: order.supplier_name,
          })
        }
        if (order.unit_price !== approval.unit_price) {
          mismatches.push({
            field: 'unit_price',
            approved_value: approval.unit_price,
            persisted_value: order.unit_price,
          })
        }
        if (order.total_cost !== approval.total_cost) {
          mismatches.push({
            field: 'total_cost',
            approved_value: approval.total_cost,
            persisted_value: order.total_cost,
          })
        }
      }

      const result: ValidationResult = {
        validation_result_id: uuid(),
        purchase_order_id: order.purchase_order_id,
        case_id: caseId,
        recommendation_version_number: order.recommendation_version_number,
        status: mismatches.length === 0 ? 'passed' : 'failed',
        checked_fields: checked,
        mismatches,
        persisted_snapshot: {
          external_po_id: order.external_po_id,
          ...checked,
        },
        created_at: nowIso(),
      }
      const existing = store.validations.get(purchaseOrderId) ?? []
      existing.push(result)
      store.validations.set(purchaseOrderId, existing)
      order.status = result.status === 'passed' ? 'validated' : 'validation_failed'
      order.validation_status = result.status
      order.validation_results = existing.map((v) => ({
        validation_result_id: v.validation_result_id,
        status: v.status,
        mismatches: v.mismatches,
        created_at: v.created_at,
      }))
      order.execution_attempts = order.execution_attempt ? [order.execution_attempt] : []
      caseDetail.status = result.status === 'passed' ? 'validated' : 'validation_failed'
      caseDetail.updated_at = nowIso()
      return clone(result)
    },

    async getPurchaseOrder(caseId: string, purchaseOrderId: string): Promise<PurchaseOrderDetail> {
      requireCase(caseId)
      const order = store.purchaseOrders.get(purchaseOrderId)
      if (!order) {
        throw new ApiClientError('not_found', 'Purchase order not found.', 404)
      }
      return clone(order)
    },
  }

  function supersedeCurrent(caseDetail: CaseDetail) {
    for (const recommendation of store.recommendations) {
      recommendation.status = 'superseded'
    }
    store.approvals = []
    caseDetail.current_recommendation_version = null
  }
}

// --- deterministic recommendation computation (mirrors PROJECT_PLAN §5) ---

function computeRecommendation(
  caseDetail: CaseDetail,
  evidence: Evidence,
  version: number,
  recommendationId: string,
  createdAt: string,
): Recommendation {
  const inventory = toNumber(evidence.usable_inventory)
  const forecast = toNumber(evidence.forecast_demand)
  const safety = toNumber(evidence.safety_stock)
  const budget = toDecimalNumber(evidence.budget_amount)
  const capacity = toDecimalNumber(evidence.storage_capacity_units)
  const perUnit = toDecimalNumber(evidence.storage_required_per_unit)

  const missing: string[] = []
  if (inventory === null) missing.push('usable_inventory')
  if (forecast === null) missing.push('forecast_demand')
  if (safety === null) missing.push('safety_stock')
  if (budget === null) missing.push('budget_amount')
  if (capacity === null || perUnit === null) {
    missing.push('storage_capacity_units/storage_required_per_unit')
  }

  const eligibleOrders = evidence.incoming_orders.filter(
    (order) =>
      order.status === 'confirmed' &&
      order.expected_arrival_date >= caseDetail.planning_horizon_start &&
      order.expected_arrival_date <= caseDetail.planning_horizon_end,
  )
  const eligibleIncoming = eligibleOrders.reduce((sum, order) => sum + order.quantity, 0)

  const suppliers = evidence.supplier_offers
    .filter(
      (offer) =>
        offer.is_available && offer.lead_time_days <= caseDetail.planning_horizon_days - 1,
    )
    .sort((a, b) => a.lead_time_days - b.lead_time_days || Number(a.unit_price) - Number(b.unit_price))
  const supplier = suppliers[0]
  if (!supplier) missing.push('eligible supplier offer')

  const baseline =
    forecast !== null && safety !== null && inventory !== null
      ? Math.max(0, forecast + safety - inventory - eligibleIncoming)
      : null

  let decision: Recommendation['decision']
  let recommendedQuantity: number | null = null
  let proposedQuantity: number | null = null
  let moqCheck: ConstraintCheck

  if (missing.length > 0 || baseline === null || !supplier || budget === null) {
    decision = 'investigate'
    moqCheck = { constraint_type: 'moq', status: 'unknown', message: 'Cannot evaluate MOQ.' }
  } else {
    const price = Number(supplier.unit_price)
    const moq = supplier.moq
    const candidateCost = (qty: number) => (qty * price).toFixed(2)
    const candidateStorage = (qty: number) => (qty * (perUnit ?? 1)).toFixed(1)

    if (baseline === 0 && moq === 0) {
      decision = 'reject'
      moqCheck = {
        constraint_type: 'moq',
        status: 'pass',
        evaluated_value: 0,
        limit_value: 0,
        unit: 'units',
        message: 'No purchase is required.',
      }
    } else if (baseline < moq) {
      const fits = Number(candidateCost(moq)) <= budget && Number(candidateStorage(moq)) <= (capacity ?? 0)
      if (fits) {
        decision = 'modify'
        proposedQuantity = moq
        recommendedQuantity = moq
        moqCheck = {
          constraint_type: 'moq',
          status: 'pass',
          evaluated_value: moq,
          limit_value: baseline,
          unit: 'units',
          message: `MOQ ${moq} exceeds the baseline ${baseline}; the extra units fit budget and storage.`,
        }
      } else {
        decision = 'investigate'
        proposedQuantity = moq
        moqCheck = {
          constraint_type: 'moq',
          status: 'fail',
          evaluated_value: moq,
          limit_value: baseline,
          unit: 'units',
          message: `MOQ ${moq} exceeds the baseline ${baseline} and the extra units do not fit budget or storage. Buyer investigation required.`,
        }
      }
    } else {
      decision = 'accept'
      proposedQuantity = baseline
      recommendedQuantity = baseline
      moqCheck = {
        constraint_type: 'moq',
        status: 'pass',
        evaluated_value: moq,
        limit_value: baseline,
        unit: 'units',
        message: 'MOQ satisfied by the calculated requirement.',
      }
    }

    if (decision === 'accept' || decision === 'modify') {
      const qty = proposedQuantity as number
      if (Number(candidateCost(qty)) > budget) {
        decision = 'investigate'
        recommendedQuantity = null
      } else if (capacity !== null && Number(candidateStorage(qty)) > capacity) {
        decision = 'investigate'
        recommendedQuantity = null
      }
    }
  }

  const qtyForCosts = proposedQuantity
  const price = supplier ? Number(supplier.unit_price) : null
  const cost = qtyForCosts !== null && price !== null ? (qtyForCosts * price).toFixed(2) : null
  const storageRequired =
    qtyForCosts !== null && perUnit !== null ? (qtyForCosts * perUnit).toFixed(1) : null

  const budgetCheck = buildBudgetCheck(budget, cost)
  const storageCheck = buildStorageCheck(capacity, perUnit, storageRequired)
  const supplierCheck: ConstraintCheck = supplier
    ? { constraint_type: 'supplier_availability', status: 'pass', message: `${supplier.supplier_name} is available.` }
    : { constraint_type: 'supplier_availability', status: 'unknown', message: 'No eligible supplier offer was found.' }
  const leadTimeCheck: ConstraintCheck = supplier
    ? {
        constraint_type: 'lead_time',
        status: 'pass',
        evaluated_value: supplier.lead_time_days,
        limit_value: caseDetail.planning_horizon_days - 1,
        unit: 'days',
        message: 'Lead time is within the seven-day horizon.',
      }
    : { constraint_type: 'lead_time', status: 'unknown', message: 'No eligible supplier offer was found.' }

  const dataQuality: ConstraintCheck = {
    constraint_type: 'data_quality',
    status: missing.length === 0 ? 'pass' : 'fail',
    message:
      missing.length === 0
        ? 'All decision-critical evidence is present.'
        : `Missing or contradictory evidence: ${missing.join(', ')}.`,
  }

  const recommendationEvidence: RecommendationEvidence = {
    usable_inventory: inventory,
    forecast_demand: forecast,
    safety_stock: safety,
    eligible_incoming_quantity: eligibleIncoming,
    budget_amount: evidence.budget_amount,
    storage_capacity_units: evidence.storage_capacity_units,
    storage_required_per_unit: evidence.storage_required_per_unit,
  }

  const horizonLabel = `${caseDetail.planning_horizon_start} through ${caseDetail.planning_horizon_end} inclusive (seven days)`
  const assumptions = [
    `Planning horizon is ${horizonLabel}.`,
    eligibleOrders.length > 0
      ? `Only confirmed incoming orders arriving within the horizon count (${eligibleIncoming} units).`
      : 'No confirmed incoming orders arrive within the horizon.',
  ]

  const constraintChecks: ConstraintCheck[] = [
    supplierCheck,
    leadTimeCheck,
    moqCheck,
    budgetCheck,
    storageCheck,
    dataQuality,
  ]

  return {
    recommendation_id: recommendationId,
    case_id: caseDetail.case_id,
    version_number: version,
    status: 'ready',
    decision,
    initial_recommended_quantity: caseDetail.initial_recommended_quantity,
    baseline_required_quantity: baseline,
    recommended_quantity: recommendedQuantity,
    chosen_supplier_offer_id: supplier?.supplier_offer_id ?? null,
    supplier_name: supplier?.supplier_name ?? null,
    unit_price: supplier?.unit_price ?? null,
    currency: caseDetail.currency,
    total_cost: cost,
    lead_time_days: supplier?.lead_time_days ?? null,
    moq: supplier?.moq ?? null,
    rationale: buildRationale({
      decision,
      baseline,
      recommendedQuantity,
      initial: caseDetail.initial_recommended_quantity,
      supplierName: supplier?.supplier_name ?? null,
      cost,
      budget: evidence.budget_amount,
      storageRequired,
      capacity: evidence.storage_capacity_units,
      missing,
    }),
    evidence: recommendationEvidence,
    assumptions,
    missing_information:
      decision === 'investigate' && (missing.length > 0 || !supplier || moqCheck.status === 'fail')
        ? missing.length > 0
          ? missing.map((field) => `Missing decision-critical evidence: ${field}.`)
          : [moqCheck.message ?? 'MOQ overage is not justified.']
        : [],
    inputs_fingerprint: fakeFingerprint(JSON.stringify({ ...evidence, version })),
    constraint_checks: constraintChecks,
    generated_by: 'mock',
    created_at: createdAt,
  }
}

function buildBudgetCheck(budget: number | null, cost: string | null): ConstraintCheck {
  if (budget === null || cost === null) {
    return { constraint_type: 'budget', status: 'unknown', message: 'Budget could not be evaluated.' }
  }
  const within = Number(cost) <= budget
  return {
    constraint_type: 'budget',
    status: within ? 'pass' : 'fail',
    evaluated_value: cost,
    limit_value: budget.toFixed(2),
    unit: 'MXN',
    message: within ? 'Within budget.' : 'Total cost exceeds the available budget.',
  }
}

function buildStorageCheck(
  capacity: number | null,
  perUnit: number | null,
  storageRequired: string | null,
): ConstraintCheck {
  if (capacity === null || perUnit === null || storageRequired === null) {
    return {
      constraint_type: 'storage',
      status: 'unknown',
      message: 'Storage could not be evaluated (missing capacity or per-unit usage).',
    }
  }
  const within = Number(storageRequired) <= capacity
  return {
    constraint_type: 'storage',
    status: within ? 'pass' : 'fail',
    evaluated_value: storageRequired,
    limit_value: capacity.toFixed(1),
    unit: 'liter',
    message: within ? 'Within storage capacity.' : 'Required storage exceeds available capacity.',
  }
}

function buildRationale(input: {
  decision: Recommendation['decision']
  baseline: number | null
  recommendedQuantity: number | null
  initial: number
  supplierName: string | null
  cost: string | null
  budget: string | null
  storageRequired: string | null
  capacity: string | null
  missing: string[]
}): string {
  if (input.decision === 'investigate') {
    if (input.missing.length > 0) {
      return `The baseline requirement could not be computed deterministically because decision-critical evidence is missing (${input.missing.join(', ')}). Escalating for buyer investigation rather than guessing.`
    }
    return `The proposal conflicts with a hard constraint (MOQ, budget, or storage). The initial ${input.initial}-unit recommendation is not executable as-is; buyer investigation is required.`
  }
  if (input.decision === 'reject') {
    return `No purchase is required: the baseline requirement is zero and there is no order constraint forcing a purchase.`
  }
  const verb = input.decision === 'modify' ? 'modified to' : 'accepted at'
  return `The deterministic requirement is ${input.baseline} units. The initial ${input.initial}-unit recommendation is ${verb} ${input.recommendedQuantity} units. Supplier ${input.supplierName ?? 'n/a'} can deliver within the horizon; total cost ${input.cost ?? 'n/a'} is within budget ${input.budget ?? 'n/a'}; storage ${input.storageRequired ?? 'n/a'} is within capacity ${input.capacity ?? 'n/a'}.`
}

function caseStatusForDecision(decision: Recommendation['decision']): CaseDetail['status'] {
  switch (decision) {
    case 'accept':
    case 'modify':
      return 'awaiting_approval'
    case 'reject':
      return 'rejected'
    case 'investigate':
      return 'needs_investigation'
  }
}

function isLockedState(status: CaseDetail['status']): boolean {
  return ['executing', 'validating', 'validated', 'execution_failed', 'validation_failed'].includes(
    status,
  )
}

function normalizeEvidence(evidence: Evidence): Evidence {
  return {
    ...evidence,
    incoming_orders: evidence.incoming_orders.map((order) => ({
      ...order,
      incoming_order_id: order.incoming_order_id ?? uuid(),
    })),
    supplier_offers: evidence.supplier_offers.map((offer) => ({
      ...offer,
      supplier_offer_id: offer.supplier_offer_id ?? uuid(),
    })),
  }
}

function overrideString(
  overrides: PersistedOverride[],
  field: PersistedOverride['field'],
  fallback: string,
): string {
  const found = overrides.find((o) => o.field === field)
  return found ? String(found.value) : fallback
}

function overrideNumber(
  overrides: PersistedOverride[],
  field: PersistedOverride['field'],
  fallback: number,
): number {
  const found = overrides.find((o) => o.field === field)
  return found ? Number(found.value) : fallback
}

function toNumber(value: number | null): number | null {
  return value === null || value === undefined ? null : Number(value)
}

function toDecimalNumber(value: string | null): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isNaN(parsed) ? null : parsed
}

function fakeFingerprint(input: string): string {
  let hash = 0
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash * 31 + input.charCodeAt(i)) | 0
  }
  return `sha256:mock-${(hash >>> 0).toString(16).padStart(8, '0')}`
}

function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
}

function addDaysLocal(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function uuid(): string {
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

function clone<T>(value: T): T {
  return structuredClone(value)
}
