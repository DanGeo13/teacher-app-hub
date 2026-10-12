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

export type HermesSessionStatus = 'connecting' | 'active' | 'failed'

export interface HermesSessionRecord {
  id: string
  hermesSessionId: string | null
  status: HermesSessionStatus
  transport: 'acp-stdio'
  protocolVersion: number | null
  agentName: string | null
  agentVersion: string | null
  model: string | null
  provider: string | null
  executable: string | null
  messageCount: number
  createdAt: string
  updatedAt: string
  lastStopReason: string | null
  lastError: string | null
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

export interface AtlasApproval {
  id: string
  action: string
  target: string
  status: string
  summary: string | null
  payload: Record<string, unknown> | null
  contentHash: string
  requestedBy: string
  expiresAt: string | null
  expiresLocal: string | null
}

export interface AtlasCheck {
  check: string
  state: 'ok' | 'no_change' | 'failed' | 'stale' | 'never_run'
  text: string
}

export interface AtlasHealth {
  overall: 'ok' | 'attention'
  problems: string[]
  checks: AtlasCheck[]
  lastBackupAt: string | null
  openReviewItems: number
}

export interface AtlasDigest {
  generatedFor: string
  text: string
  sections: { key: string; title: string; groups: { name: string; items: { text: string }[] }[] }[]
}

export type VerificationStatus = 'PASS' | 'FAIL' | 'BLOCKED'

export interface AtlasVerification {
  runId?: string
  status: 'never_run' | 'RUNNING' | 'finished' | 'error'
  steps: { name: string; status: VerificationStatus; detail: string }[]
  host?: { platform: string; machine: string; android: boolean }
  verdict?: string
  error?: string
  finishedAt?: string
}
