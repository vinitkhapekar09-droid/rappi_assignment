import { ApiClientError } from './errors'
import type { PurchasingApi } from './client'
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

const API_PREFIX = '/api/v1'

interface RequestOptions {
  body?: unknown
  headers?: Record<string, string>
}

/**
 * Real HTTP adapter. Uses only the routes listed in `docs/API_CONTRACT.md` §4.
 */
export function createHttpApi(baseUrl: string): PurchasingApi {
  const root = `${baseUrl.replace(/\/+$/, '')}${API_PREFIX}`

  async function request<T>(
    method: 'GET' | 'POST' | 'PUT',
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    let response: Response
    try {
      response = await fetch(`${root}${path}`, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      })
    } catch {
      throw new ApiClientError(
        'network_error',
        `Could not reach the API at ${root}. Is the backend running?`,
      )
    }

    const text = await response.text()
    const parsed: unknown = text ? safeJsonParse(text) : undefined
    if (!response.ok || (parsed && typeof parsed === 'object' && 'error' in parsed)) {
      throw ApiClientError.fromBody(parsed, response.status)
    }
    return parsed as T
  }

  return {
    createCase(input: CreateCaseRequest) {
      return request<CaseDetail>('POST', '/cases', { body: input })
    },
    getCase(caseId: string) {
      return request<CaseDetail>('GET', `/cases/${caseId}`)
    },
    putEvidence(caseId: string, evidence: Evidence) {
      return request<Evidence>('PUT', `/cases/${caseId}/evidence`, {
        body: {
          ...evidence,
          incoming_orders: evidence.incoming_orders.map(({ incoming_order_id: _id, ...order }) => order),
          supplier_offers: evidence.supplier_offers.map(({ supplier_offer_id: _id, ...offer }) => offer),
        },
      })
    },
    generateRecommendation(caseId: string) {
      return request<Recommendation>('POST', `/cases/${caseId}/recommendations`, {
        body: {},
      })
    },
    listRecommendations(caseId: string) {
      return request<RecommendationList>('GET', `/cases/${caseId}/recommendations`)
    },
    getRecommendation(caseId: string, versionNumber: number) {
      return request<Recommendation>(
        'GET',
        `/cases/${caseId}/recommendations/${versionNumber}`,
      )
    },
    decide(caseId: string, versionNumber: number, decision: DecisionRequest) {
      return request<Approval>(
        'POST',
        `/cases/${caseId}/recommendations/${versionNumber}/decision`,
        { body: decision },
      )
    },
    executePurchase(
      caseId: string,
      body: { recommendation_version_number: number; approval_id: string },
      idempotencyKey: string,
    ) {
      return request<PurchaseOrder>('POST', `/cases/${caseId}/purchases`, {
        body,
        headers: { 'Idempotency-Key': idempotencyKey },
      })
    },
    validatePurchase(caseId: string, purchaseOrderId: string) {
      return request<ValidationResult>(
        'POST',
        `/cases/${caseId}/purchase-orders/${purchaseOrderId}/validate`,
        { body: {} },
      )
    },
    getPurchaseOrder(caseId: string, purchaseOrderId: string) {
      return request<PurchaseOrderDetail>(
        'GET',
        `/cases/${caseId}/purchase-orders/${purchaseOrderId}`,
      )
    },
  }
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}
