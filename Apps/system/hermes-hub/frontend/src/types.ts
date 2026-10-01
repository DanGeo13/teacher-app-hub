export type HubName = 'teaching' | 'personal'
export type IntegrationMode = 'external_link' | 'embedded_legacy' | 'hub_aware'
export type HealthStatus = 'unknown' | 'healthy' | 'degraded' | 'unavailable'

export interface AppRecord {
  id: string
  hub: HubName
  title: string
  description: string
  category: string
  tags: string[]
  icon: string
  projectPath: string | null
  launchUrl: string
  developmentUrl: string | null
  integrationMode: IntegrationMode
  authenticationRequirements: string
  themeAdapterVersion: string | null
  aiAdapterSupport: boolean
  healthStatus: HealthStatus
  displayOrder: number
  scriptId: string | null
  deploymentId: string | null
  archived: boolean
  revision: number
  createdAt: string
  updatedAt: string
}

export interface AppInput {
  hub: HubName
  title: string
  description: string
  category: string
  tags: string[]
  icon: string
  projectPath: string | null
  launchUrl: string
  developmentUrl: string | null
  integrationMode: IntegrationMode
  authenticationRequirements: string
  themeAdapterVersion: string | null
  aiAdapterSupport: boolean
  healthStatus: HealthStatus
  scriptId: string | null
  deploymentId: string | null
}

export interface RuntimeCapability {
  state: 'verified' | 'unsupported' | 'unknown' | 'not_run'
  detail: string
}

export interface RuntimeProbe {
  component: string
  status: 'available' | 'unavailable' | 'authentication_required' | 'degraded'
  endpoint: string
  checkedAt: string
  version: string | null
  detail: string
  capabilities: Record<string, RuntimeCapability>
}

export interface RuntimeState {
  hermes: RuntimeProbe
  qwen: RuntimeProbe
}

export interface SessionState {
  authenticated: boolean
  actor?: string
  csrfToken?: string
  expiresAt?: string
}

export interface ApiErrorShape {
  error?: { code?: string; message?: string }
  detail?: string | { code?: string; message?: string }
}
