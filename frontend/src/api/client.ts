import type {
  Approval,
  CaseDetail,
  CreateCaseRequest,
  DecisionRequest,
  Evidence,
  PurchaseOrder,
  PurchaseOrderDetail,
  Recommendation,
  RecommendationList,
  ValidationResult,
} from './types'

/**
 * The single interface the dashboard depends on. All HTTP paths and request
 * shapes live in the adapter implementations, never in components.
 *
 * Two adapters implement it:
 * - `httpApi`  — talks to the FastAPI backend using only the frozen routes.
 * - `mockApi`  — in-memory fixtures so the UI is demoable before the backend
 *                exists. No backend routes are invented.
 */
export interface PurchasingApi {
  createCase(input: CreateCaseRequest): Promise<CaseDetail>
  getCase(caseId: string): Promise<CaseDetail>
  putEvidence(caseId: string, evidence: Evidence): Promise<Evidence>
  generateRecommendation(caseId: string): Promise<Recommendation>
  listRecommendations(caseId: string): Promise<RecommendationList>
  getRecommendation(caseId: string, versionNumber: number): Promise<Recommendation>
  decide(
    caseId: string,
    versionNumber: number,
    request: DecisionRequest,
  ): Promise<Approval>
  executePurchase(
    caseId: string,
    request: { recommendation_version_number: number; approval_id: string },
    idempotencyKey: string,
  ): Promise<PurchaseOrder>
  validatePurchase(caseId: string, purchaseOrderId: string): Promise<ValidationResult>
  getPurchaseOrder(caseId: string, purchaseOrderId: string): Promise<PurchaseOrderDetail>
}
