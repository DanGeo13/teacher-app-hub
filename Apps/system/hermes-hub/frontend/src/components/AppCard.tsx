import type { AppRecord } from '../types'

interface Props {
  app: AppRecord
  index: number
  count: number
  onEdit: () => void
  onMove: (direction: -1 | 1) => void
  onArchive: () => void
}

export function AppCard({ app, index, count, onEdit, onMove, onArchive }: Props) {
  return <article className={`app-card ${app.archived ? 'app-card--archived' : ''}`}>
    <div className="app-card__top"><span className="app-icon" aria-hidden="true">{app.title.slice(0, 1).toUpperCase()}</span><div className="app-card__meta"><span>{app.category}</span><span>rev {app.revision}</span></div></div>
    <h3>{app.title}</h3>
    <p>{app.description || 'No description supplied.'}</p>
    <div className="tag-row">{app.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div>
    <div className="app-card__facts"><span>{app.integrationMode.replaceAll('_', ' ')}</span><span>{app.aiAdapterSupport ? 'AI adapter declared' : 'No AI adapter'}</span></div>
    <div className="app-card__actions">
      <a className="button button--small button--primary" href={app.launchUrl} target="_blank" rel="noreferrer">Open</a>
      <button className="button button--small button--ghost" onClick={onEdit}>Edit</button>
      {!app.archived && <div className="order-buttons" aria-label={`Reorder ${app.title}`}><button className="icon-button" aria-label={`Move ${app.title} earlier`} disabled={index === 0} onClick={() => onMove(-1)}>↑</button><button className="icon-button" aria-label={`Move ${app.title} later`} disabled={index === count - 1} onClick={() => onMove(1)}>↓</button></div>}
      <button className="button button--small button--quiet" onClick={onArchive}>{app.archived ? 'Restore' : 'Archive'}</button>
    </div>
  </article>
}
