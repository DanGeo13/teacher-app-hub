import { useCallback, useEffect, useState } from 'react'
import { decideAtlasApproval, getAtlasDigest, getAtlasHealth, getAtlasVerification, listAtlasApprovals, startAtlasVerification } from '../api'
import { redactDisplayText } from '../redaction'
import type { AtlasApproval, AtlasDigest, AtlasHealth, AtlasVerification, VerificationStatus } from '../types'

const stepClass: Record<VerificationStatus, string> = { PASS: 'verified', FAIL: 'unsupported', BLOCKED: 'unknown' }
const checkClass: Record<string, string> = { ok: 'verified', no_change: 'verified', failed: 'unsupported', stale: 'unsupported', never_run: 'unknown' }

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback
}

export function Atlas() {
  const [approvals, setApprovals] = useState<AtlasApproval[] | null>(null)
  const [health, setHealth] = useState<AtlasHealth | null>(null)
  const [digest, setDigest] = useState<AtlasDigest | null>(null)
  const [verification, setVerification] = useState<AtlasVerification | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [a, h, d, v] = await Promise.all([listAtlasApprovals(), getAtlasHealth(), getAtlasDigest(), getAtlasVerification()])
      setApprovals(a.approvals); setHealth(h); setDigest(d); setVerification(v)
    } catch (reason) { setError(message(reason, 'Atlas data is unavailable')) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    if (verification?.status !== 'RUNNING') return
    const timer = window.setInterval(() => {
      getAtlasVerification().then(setVerification).catch((reason) => setError(message(reason, 'Verification status unavailable')))
    }, 2000)
    return () => window.clearInterval(timer)
  }, [verification?.status])

  async function decide(approval: AtlasApproval, decision: 'approve' | 'reject') {
    setBusy(approval.id); setNotice('')
    try {
      await decideAtlasApproval(approval.id, decision, approval.contentHash)
      setNotice(decision === 'approve' ? `Approved: ${approval.summary ?? approval.action}` : `Denied: ${approval.summary ?? approval.action}`)
      await refresh()
    } catch (reason) { setNotice(message(reason, 'The decision was not recorded')) }
    finally { setBusy('') }
  }

  async function verify() {
    setNotice('')
    try { setVerification(await startAtlasVerification()) }
    catch (reason) { setError(message(reason, 'Verification could not start')) }
  }

  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">Personal operations agent</p><h1>Atlas</h1><p>Approvals, health and the weekly overview. Atlas proposes; only you approve.</p></div><button className="button button--ghost" onClick={() => void refresh()}>Refresh</button></header>
    {error && <p className="result-message" role="alert">{error}</p>}
    {notice && <p className="result-message" role="status">{notice}</p>}
    <div className="maintenance-grid">
      <article className="maintenance-card"><div className="maintenance-card__head"><h2>Waiting for your approval</h2><span className="status">{approvals?.length ?? 0} pending</span></div>
        {approvals?.length === 0 && <p className="result-message">Nothing is waiting for approval.</p>}
        <ul className="capability-list">{approvals?.map((approval) => <li key={approval.id} className="atlas-approval">
          <span><strong>{approval.summary ?? approval.action}</strong></span>
          <small>Action <code>{approval.action}</code> · target <code>{approval.target}</code> · expires {approval.expiresLocal ?? 'never'} (Sydney)</small>
          <pre className="atlas-payload" aria-label="Exact content to approve">{redactDisplayText(JSON.stringify(approval.payload, null, 2))}</pre>
          <small>Content hash {approval.contentHash.slice(0, 12)}…</small>
          <div className="atlas-actions">
            <button className="button button--primary button--small" disabled={busy === approval.id || approval.status !== 'pending'} onClick={() => void decide(approval, 'approve')}>Approve exactly this</button>
            <button className="button button--small" disabled={busy === approval.id || approval.status !== 'pending'} onClick={() => void decide(approval, 'reject')}>Deny</button>
          </div>
        </li>)}</ul>
      </article>
      <article className="maintenance-card"><div className="maintenance-card__head"><h2>System health</h2><span className={`status ${health?.overall === 'ok' ? 'status--available' : 'status--degraded'}`}>{health?.overall ?? 'loading'}</span></div>
        <ul className="capability-list">{health?.checks.map((check) => <li key={check.check}><span>{check.check}</span><span className={`capability capability--${checkClass[check.state] ?? 'unknown'}`}>{check.state.replace('_', ' ')}</span><small>{check.text}</small></li>)}</ul>
        {health && health.problems.length > 0 && <ul>{health.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>}
      </article>
      <article className="maintenance-card"><div className="maintenance-card__head"><h2>Live verification</h2><span className="status">{verification?.status === 'RUNNING' ? 'running' : verification?.status ?? 'never run'}</span></div>
        <p>Checks this host: Hermes answers, persona, each skill, database, Telegram round trip, approval round trip and a dry-run digest. Synthetic data only; no real account is touched.</p>
        <button className="button button--primary" disabled={verification?.status === 'RUNNING'} onClick={() => void verify()}>{verification?.status === 'RUNNING' ? 'Verifying… (tap Confirm in Telegram)' : 'Verify Atlas on this host'}</button>
        {verification?.verdict && <p className="result-message" role="status"><strong>{verification.verdict}</strong>{verification.host ? ` Host: ${verification.host.platform} (${verification.host.android ? 'Android' : 'not Android'}).` : ''}</p>}
        {verification?.error && <p className="result-message" role="alert">{verification.error}</p>}
        <ul className="capability-list">{verification?.steps.map((step) => <li key={step.name}><span>{step.name}</span><span className={`capability capability--${stepClass[step.status]}`}>{step.status}</span>{step.detail && <small>{step.detail}</small>}</li>)}</ul>
      </article>
      <article className="maintenance-card"><div className="maintenance-card__head"><h2>This week</h2><span className="status">{digest?.generatedFor ?? ''}</span></div>
        {digest?.sections.map((section) => <section key={section.key}><h3>{section.title}</h3>
          {section.groups.every((group) => group.items.length === 0) ? <p className="result-message">Nothing.</p> :
            section.groups.map((group) => <div key={group.name}><strong>{group.name}</strong><ul>{group.items.map((item) => <li key={item.text}>{item.text}</li>)}</ul></div>)}
        </section>)}
      </article>
    </div>
  </div>
}
