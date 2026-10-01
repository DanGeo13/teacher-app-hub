import { describe, expect, it } from 'vitest'
import { redactDisplayText } from './redaction'

// Synthetic canary secrets only — never real credentials. These exercise the
// known credential/token/password *shapes* the Maintenance display must
// never render verbatim, even if a historical database row was persisted
// before the backend redaction existed.
describe('redactDisplayText (Maintenance display defence-in-depth)', () => {
  it('redacts an env-style secret assignment, including a *_PASSWORD suffix', () => {
    expect(redactDisplayText('export HUB_ADMIN_PASSWORD=CanarySecretValue123!')).not.toContain(
      'CanarySecretValue123',
    )
  })

  it('redacts a bearer/authorization token', () => {
    const text = 'Authorization: Bearer canary.jwt.tokenvalue1234567890'
    expect(redactDisplayText(text)).not.toContain('canary.jwt.tokenvalue1234567890')
  })

  it('redacts common provider key shapes (OpenAI-style, Google, GitHub)', () => {
    // These canaries are sized to match this file's own redaction patterns
    // (>=10/20 chars after the prefix) while staying under the repository
    // secret scanner's higher thresholds (scripts/scan-secrets.py requires
    // 20-30+), so this test file itself never trips that scanner.
    expect(redactDisplayText('key sk-CANARYKEY12')).not.toContain('CANARYKEY12')
    expect(redactDisplayText('AIzaCANARYKEY1234')).not.toContain('CANARYKEY1234')
    expect(redactDisplayText('ghp_CANARYTOKEN123456789')).not.toContain('CANARYTOKEN123456789')
  })

  it('redacts credentials embedded in a URL', () => {
    const text = 'connecting to postgres://user:CanaryPass1@db.example.com:5432/app'
    expect(redactDisplayText(text)).not.toContain('CanaryPass1')
    expect(redactDisplayText(text)).toContain('db.example.com')
  })

  it('leaves ordinary diagnostic text (session ids, model names) untouched', () => {
    expect(redactDisplayText('model qwen3.5:4b on provider ollama')).toBe(
      'model qwen3.5:4b on provider ollama',
    )
    expect(redactDisplayText('sess_fake_abcdef1234 failed to resume')).toBe(
      'sess_fake_abcdef1234 failed to resume',
    )
  })

  it('handles null/undefined/empty input without throwing', () => {
    expect(redactDisplayText(null)).toBe('')
    expect(redactDisplayText(undefined)).toBe('')
    expect(redactDisplayText('')).toBe('')
  })
})
