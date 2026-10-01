import { FormEvent, useState } from 'react'
import { login } from '../api'
import type { SessionState } from '../types'

interface Props { onLogin: (state: SessionState) => void }

export function Login({ onLogin }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try { onLogin(await login(password)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign-in failed') }
    finally { setBusy(false) }
  }

  return <main className="login-shell">
    <section className="login-card" aria-labelledby="login-title">
      <div className="brand-mark" aria-hidden="true">H</div>
      <p className="eyebrow">Private control panel</p>
      <h1 id="login-title">Welcome to Hermes Hub</h1>
      <p className="lede">Sign in to open your server-side app registry and runtime controls.</p>
      <form onSubmit={submit}>
        <label htmlFor="password">Administrator password</label>
        <input id="password" name="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button button--primary button--wide" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
      <p className="fine-print">The session token is stored in an HttpOnly cookie, never browser storage.</p>
    </section>
  </main>
}
