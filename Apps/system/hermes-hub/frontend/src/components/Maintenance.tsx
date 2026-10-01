import { useState } from 'react'
import { createLocalBackup } from '../api'
import type { RuntimeState } from '../types'
import { StatusBadge } from './StatusBadge'

interface Props { runtime: RuntimeState | null; onRefresh: () => void }

export function Maintenance({ runtime, onRefresh }: Props) {
  const [backupMessage, setBackupMessage] = useState('')
  const [busy, setBusy] = useState(false)
  async function backup() {
    setBusy(true); setBackupMessage('')
    try { const result = await createLocalBackup(); setBackupMessage(`Verified local snapshot: ${result.filename}`) }
    catch (reason) { setBackupMessage(reason instanceof Error ? reason.message : 'Snapshot failed') }
    finally { setBusy(false) }
  }
  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">Settings and maintenance</p><h1>Maintenance</h1><p>Observable state, conservative recovery and no hidden side effects.</p></div><button className="button button--ghost" onClick={onRefresh}>Run probes</button></header>
    <div className="maintenance-grid">
      {runtime && [runtime.hermes, runtime.qwen].map((probe) => <article className="maintenance-card" key={probe.component}><div className="maintenance-card__head"><h2>{probe.component === 'hermes' ? 'Hermes Agent' : 'Qwen via Ollama'}</h2><StatusBadge status={probe.status} /></div><dl><div><dt>Endpoint</dt><dd><code>{probe.endpoint}</code></dd></div><div><dt>Version</dt><dd>{probe.version ?? 'Not available'}</dd></div><div><dt>Last check</dt><dd>{new Date(probe.checkedAt).toLocaleString('en-AU')}</dd></div></dl><h3>Capabilities</h3><ul className="capability-list">{Object.entries(probe.capabilities).map(([name, value]) => <li key={name}><span>{name}</span><span className={`capability capability--${value.state}`}>{value.state.replace('_', ' ')}</span><small>{value.detail}</small></li>)}</ul></article>)}
      <article className="maintenance-card"><div className="maintenance-card__head"><h2>Local backup</h2><span className="status">workspace only</span></div><p>Create a consistent SQLite snapshot using the online backup API. This does not protect against Codespace deletion.</p><button className="button button--primary" disabled={busy} onClick={() => void backup()}>{busy ? 'Creating snapshot…' : 'Create local snapshot'}</button>{backupMessage && <p className="result-message" role="status">{backupMessage}</p>}</article>
      <article className="maintenance-card maintenance-card--muted"><div className="maintenance-card__head"><h2>Restricted operations</h2><span className="status">disabled</span></div><p>Git commits, GitHub pushes, clasp writes, deployments and live restore are intentionally unavailable.</p><button className="button" disabled>External-write broker — not implemented</button></article>
    </div>
  </div>
}
