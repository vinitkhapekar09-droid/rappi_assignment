import type { ApiErrorBody } from './types'

/**
 * Error raised by any `PurchasingApi` implementation. Mirrors the contract's
 * single error shape: `{ "error": { "code", "message", "details" } }`.
 */
export class ApiClientError extends Error {
  readonly code: string
  readonly status?: number
  readonly details?: Record<string, unknown>

  constructor(
    code: string,
    message: string,
    status?: number,
    details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.code = code
    this.status = status
    this.details = details
  }

  static fromBody(body: unknown, status?: number): ApiClientError {
    const parsed = body as ApiErrorBody | undefined
    if (parsed && typeof parsed === 'object' && 'error' in parsed && parsed.error) {
      return new ApiClientError(
        parsed.error.code,
        parsed.error.message,
        status,
        parsed.error.details,
      )
    }
    return new ApiClientError('unexpected_error', 'Unexpected error response.', status)
  }
}

export function isApiClientError(value: unknown): value is ApiClientError {
  return value instanceof ApiClientError
}

export function errorMessage(value: unknown): string {
  if (isApiClientError(value)) return value.message
  if (value instanceof Error) return value.message
  return 'Unexpected error.'
}
