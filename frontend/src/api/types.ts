/**
 * TypeScript types mirroring `docs/API_CONTRACT.md` (frozen contract).
 *
 * Conventions from the contract:
 * - IDs are UUID strings.
 * - Monetary amounts and storage volumes are decimal STRINGS (e.g. "12.50").
 * - Quantities are integers (whole units).
 * - Timestamps are ISO-8601 UTC strings; dates are ISO-8601 calendar dates.
 * - Field naming is snake_case to match the wire format exactly.
 */

/** Wire-level error body returned by all non-2xx API responses. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

export type CaseStatus =
  | 'draft'
  | 'investigating'
  | 'awaiting_approval'
  | 'approved'
  | 'executing'
  | 'validating'
  | 'validated'
  | 'rejected'
  | 'needs_investigation'
  | 'execution_failed'
  | 'validation_failed'

export type RecommendationDecision = 'accept' | 'modify' | 'reject' | 'investigate'

export type ConstraintType =
  | 'supplier_availability'
  | 'lead_time'
  | 'moq'
  | 'budget'
  | 'storage'
  | 'data_quality'

export type ConstraintStatus = 'pass' | 'fail' | 'unknown'

export type IncomingOrderStatus = 'confirmed' | 'unconfirmed' | (string & {})

export interface IncomingOrder {
  incoming_order_id?: string
  external_ref?: string | null
  quantity: number
  expected_arrival_date: string
  status: IncomingOrderStatus
}

export interface SupplierOffer {
  supplier_offer_id?: string
  supplier_name: string
  unit_price: string
  currency: string
  moq: number
  lead_time_days: number
  is_available: boolean
}

export interface Evidence {
  usable_inventory: number | null
  forecast_demand: number | null
  safety_stock: number | null
  budget_amount: string | null
  storage_capacity_units: string | null
  storage_required_per_unit: string | null
  evidence_as_of: string | null
  incoming_orders: IncomingOrder[]
  supplier_offers: SupplierOffer[]
}

export interface CaseDetail {
  case_id: string
  sku: string
  product_name: string
  currency: string
  volume_unit: string
  planning_horizon_start: string
  planning_horizon_days: number
  planning_horizon_end: string
  initial_recommended_quantity: number
  status: CaseStatus
  current_recommendation_version: number | null
  evidence: Evidence | null
  created_at: string
  updated_at: string
}

export interface CreateCaseRequest {
  sku: string
  product_name: string
  currency: string
  volume_unit: string
  initial_recommended_quantity?: number
  planning_horizon_start?: string
}

export interface ConstraintCheck {
  constraint_type: ConstraintType
  status: ConstraintStatus
  evaluated_value?: number | string | null
  limit_value?: number | string | null
  unit?: string | null
  message?: string | null
  details?: {
    [key: string]: unknown
    agent_workflow?: {
      inference?: InferenceInfo
      warnings?: string[]
      interpretation?: {
        summary: string
        key_evidence: string[]
        trade_offs: string[]
        caveats: string[]
      }
    }
  }
}

export interface InferenceInfo {
  mode: 'online' | 'offline_fallback' | 'provider_fallback'
  online_succeeded: boolean
  provider: string
  model: string | null
  requested_provider: string
  requested_model: string | null
  fallback_reason: string | null
}

export interface Recommendation {
  recommendation_id: string
  case_id: string
  version_number: number
  status: 'ready' | 'superseded' | (string & {})
  decision: RecommendationDecision
  initial_recommended_quantity: number
  baseline_required_quantity: number | null
  recommended_quantity: number | null
  chosen_supplier_offer_id?: string | null
  supplier_name?: string | null
  unit_price?: string | null
  currency: string
  total_cost?: string | null
  lead_time_days?: number | null
  moq?: number | null
  rationale: string
  evidence?: RecommendationEvidence | null
  assumptions: string[]
  missing_information: string[]
  inputs_fingerprint?: string | null
  constraint_checks: ConstraintCheck[]
  generated_by: string
  created_at: string
}

export interface RecommendationEvidence {
  usable_inventory: number | null
  forecast_demand: number | null
  safety_stock: number | null
  eligible_incoming_quantity: number | null
  budget_amount: string | null
  storage_capacity_units: string | null
  storage_required_per_unit: string | null
}

export interface RecommendationSummary {
  recommendation_id: string
  version_number: number
  status: string
  decision: RecommendationDecision
  recommended_quantity: number | null
  total_cost: string | null
  created_at: string
}

export interface RecommendationList {
  case_id: string
  current_recommendation_version: number | null
  recommendations: RecommendationSummary[]
}

export type BuyerDecision = 'approved' | 'rejected'

export interface DecisionRequest {
  decision: BuyerDecision
  approved_quantity?: number
  approver: string
  note?: string
}

export interface Approval {
  approval_id: string
  case_id: string
  recommendation_version_number: number
  decision: BuyerDecision
  approved_quantity?: number | null
  supplier_offer_id?: string | null
  supplier_name?: string | null
  unit_price?: string | null
  total_cost?: string | null
  currency: string
  approver: string
  note?: string | null
  created_at: string
}

export type PurchaseOrderStatus = 'created' | 'failed' | 'validated' | 'validation_failed'

export interface ExecutionAttempt {
  execution_attempt_id: string
  attempt_number: number
  outcome: 'succeeded' | 'failed' | 'uncertain'
  http_status: number | null
  error_code?: string | null
  created_at: string
}

export interface PurchaseOrder {
  purchase_order_id: string
  case_id: string
  approval_id: string | null
  recommendation_version_number: number
  supplier_name: string
  sku: string
  quantity: number
  unit_price: string
  total_cost: string
  currency: string
  status: PurchaseOrderStatus
  external_po_id?: string | null
  execution_attempt?: ExecutionAttempt | null
  validation_status?: 'pending' | 'passed' | 'failed' | 'inconclusive' | null
  created_at: string
}

export interface LineItem {
  sku: string
  quantity: number
  unit_price: string
  line_total: string
}

export interface ValidationResultSummary {
  validation_result_id: string
  status: ValidationStatus
  mismatches: Mismatch[]
  created_at: string
}

export interface PurchaseOrderDetail extends PurchaseOrder {
  line_items: LineItem[]
  execution_attempts: ExecutionAttempt[]
  validation_results: ValidationResultSummary[]
}

export type ValidationStatus = 'passed' | 'failed' | 'inconclusive'

export interface Mismatch {
  field: string
  approved_value: unknown
  persisted_value: unknown
}

export interface ValidationResult {
  validation_result_id: string
  purchase_order_id: string
  case_id: string
  recommendation_version_number: number
  status: ValidationStatus
  checked_fields: Record<string, unknown>
  mismatches: Mismatch[]
  persisted_snapshot: Record<string, unknown>
  created_at: string
}

export interface ExecutePurchaseRequest {
  recommendation_version_number: number
  approval_id: string
}
