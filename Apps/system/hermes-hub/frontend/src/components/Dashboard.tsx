import type { RuntimeState } from '../types'
import { StatusBadge } from './StatusBadge'

interface Props { runtime: RuntimeState | null; runtimeError: string; onRefresh: () => void }

export function Dashboard({ runtime, runtimeError, onRefresh }: Props) {
  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">System overview</p><h1>Good day.</h1><p>Your apps stay useful even when the agent runtime is disconnected.</p></div><button className="button button--ghost" onClick={onRefresh}>Refresh status</button></header>
    <section className="hero-panel"><div><span className="hero-panel__kicker">Durable foundation</span><h2>One quiet place for your teaching and personal tools.</h2><p>Registry changes are stored in the Hub database. Nothing is committed or deployed automatically.</p></div><div className="hero-panel__stamp"><strong>0.1</strong><span>foundation</span></div></section>
    {runtimeError && <div className="notice notice--error">{runtimeError}</div>}
    <div className="metric-grid">
      {(['hermes', 'qwen'] as const).map((name) => {
        const probe = runtime?.[name]
        return <article className="metric" key={name}><div className="metric__label"><span>{name === 'hermes' ? 'Hermes Agent' : 'Local Qwen'}</span>{probe ? <StatusBadge status={probe.status} /> : <span className="status">checking</span>}</div><strong>{probe?.version ?? 'Not verified'}</strong><p>{probe?.detail ?? 'Checking the server-side runtime probe…'}</p></article>
      })}
      <article className="metric"><div className="metric__label"><span>Approvals</span><span className="status status--available">enforced</span></div><strong>Fail closed</strong><p>External-write actions remain disabled until a constrained broker is implemented.</p></article>
    </div>
    <section className="section"><div className="section__heading"><div><p className="eyebrow">Workflow</p><h2>What is available now</h2></div></div><div className="steps"><div><span>01</span><h3>Register</h3><p>Add external, legacy or Hub-aware applications.</p></div><div><span>02</span><h3>Organise</h3><p>Edit categories, reorder cards and archive safely.</p></div><div className="steps__disabled"><span>03</span><h3>Develop with agents</h3><p>Planned — unavailable until a real Hermes session is integrated.</p></div></div></section>
  </div>
}
