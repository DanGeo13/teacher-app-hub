/**
 * Defence-in-depth redaction applied immediately before any Hermes
 * diagnostic/error text is rendered in the UI.
 *
 * The backend is the primary control: it redacts known credential/token
 * shapes before persisting `lastError` or returning diagnostics over
 * HTTP/SSE (see `hermes_hub_backend/redaction.py`). This client-side pass is
 * a second, independent layer in case a historical database row predates
 * that control or a future code path forgets to call it — it does not
 * replace the backend redaction and does not guarantee every sensitive
 * value is caught; it only covers known credential/token/password shapes.
 */

const PATTERNS: RegExp[] = [
  /(api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|bearer)\b\s*[:=]\s*("[^"]*"|'[^']*'|[^\s,;"'}\]]+)/gi,
  /\bauthorization\s*:\s*\S+(?:\s+\S+)?/gi,
  /\bbearer\s+[A-Za-z0-9._-]{8,}/gi,
  /\bsk-[A-Za-z0-9_-]{10,}\b/g,
  /\bAIza[0-9A-Za-z_-]{10,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/g,
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g,
  /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
]

const URL_CREDENTIALS = /([a-zA-Z][a-zA-Z0-9+.-]*:\/\/)[^\s/@]+:[^\s/@]+@/g

const REDACTED = '[redacted]'

/** Redact known credential/token shapes from text before it is rendered. */
export function redactDisplayText(text: string | null | undefined): string {
  if (!text) return ''
  let redacted = text
  for (const pattern of PATTERNS) {
    redacted = redacted.replace(pattern, REDACTED)
  }
  redacted = redacted.replace(URL_CREDENTIALS, `$1${REDACTED}@`)
  return redacted
}
