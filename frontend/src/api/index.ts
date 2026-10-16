import { createHttpApi } from './httpApi'
import { createMockApi, type MockPurchasingApi } from './mockApi'
import type { PurchasingApi } from './client'

export type ApiMode = 'mock' | 'http'

export function resolveApiMode(): ApiMode {
  const configured = import.meta.env.VITE_API_MODE
  if (configured === 'http' || configured === 'mock') return configured
  return 'http'
}

export interface ApiBundle {
  mode: ApiMode
  api: PurchasingApi
  mock?: MockPurchasingApi
}

export function createApi(): ApiBundle {
  const mode = resolveApiMode()
  if (mode === 'http') {
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
    return { mode, api: createHttpApi(baseUrl) }
  }
  const mock = createMockApi('happy')
  return { mode, api: mock, mock }
}

export type { PurchasingApi } from './client'
export { ApiClientError, errorMessage, isApiClientError } from './errors'
export * from './types'
