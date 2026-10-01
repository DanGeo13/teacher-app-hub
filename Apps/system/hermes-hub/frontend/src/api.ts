import type { ApiErrorShape, AppInput, AppRecord, HubName, RuntimeState, SessionState } from './types'

let csrfToken: string | null = null

export class ApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  let response: Response
  try {
    response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  } catch {
    throw new ApiError('The Hermes Hub backend is unavailable.', 0, 'BACKEND_UNAVAILABLE')
  }
  if (!response.ok) {
    let payload: ApiErrorShape = {}
    try { payload = await response.json() as ApiErrorShape } catch { /* non-JSON error */ }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    throw new ApiError(
      payload.error?.message ?? detail ?? `Request failed with HTTP ${response.status}`,
      response.status,
      payload.error?.code ?? (typeof payload.detail === 'object' ? payload.detail.code : undefined),
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function restoreSession(): Promise<SessionState> {
  const state = await request<SessionState>('/api/auth/session')
  csrfToken = state.csrfToken ?? null
  return state
}

export async function login(password: string): Promise<SessionState> {
  const state = await request<SessionState>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
  csrfToken = state.csrfToken ?? null
  return state
}

export async function logout(): Promise<void> {
  await request<void>('/api/auth/logout', { method: 'POST' })
  csrfToken = null
}

export function listApps(hub: HubName, includeArchived = true): Promise<AppRecord[]> {
  return request(`/api/apps?hub=${hub}&include_archived=${includeArchived}`)
}

export function createApp(value: AppInput): Promise<AppRecord> {
  return request('/api/apps', { method: 'POST', body: JSON.stringify(value) })
}

export function updateApp(
  id: string,
  expectedRevision: number,
  changes: Partial<AppInput> & { archived?: boolean },
): Promise<AppRecord> {
  return request(`/api/apps/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ expectedRevision, ...changes }),
  })
}

export function reorderApps(hub: HubName, apps: AppRecord[]): Promise<AppRecord[]> {
  return request('/api/apps/reorder', {
    method: 'POST',
    body: JSON.stringify({
      hub,
      orderedIds: apps.map((app) => app.id),
      expectedRevisions: Object.fromEntries(apps.map((app) => [app.id, app.revision])),
    }),
  })
}

export function getRuntime(): Promise<RuntimeState> {
  return request('/api/runtime')
}

export function createLocalBackup(): Promise<{ filename: string; sha256: string; createdAt: string }> {
  return request('/api/backups/local', { method: 'POST' })
}
