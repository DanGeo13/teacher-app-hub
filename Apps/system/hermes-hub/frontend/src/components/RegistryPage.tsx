import { useCallback, useEffect, useMemo, useState } from 'react'
import { createApp, listApps, reorderApps, updateApp } from '../api'
import type { AppInput, AppRecord, HubName } from '../types'
import { AppCard } from './AppCard'
import { AppForm } from './AppForm'
import { EmptyState } from './EmptyState'

interface Props { hub: HubName }

export function RegistryPage({ hub }: Props) {
  const [apps, setApps] = useState<AppRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [editing, setEditing] = useState<AppRecord | null | undefined>(undefined)

  const title = hub === 'teaching' ? 'Teacher Hub' : 'Lifestyle Hub'
  const subtitle = hub === 'teaching' ? 'Classroom tools and teaching workflows, kept separate.' : 'Personal applications and everyday workflows.'

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setApps(await listApps(hub, true)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to load registry') }
    finally { setLoading(false) }
  }, [hub])

  useEffect(() => { void load() }, [load])

  const visible = useMemo(() => apps.filter((app) => {
    if (!showArchived && app.archived) return false
    const needle = search.toLowerCase()
    return !needle || [app.title, app.description, app.category, ...app.tags].some((value) => value.toLowerCase().includes(needle))
  }), [apps, search, showArchived])

  const active = apps.filter((app) => !app.archived).sort((a, b) => a.displayOrder - b.displayOrder)

  async function save(value: AppInput) {
    if (editing) {
      const { hub: _unchangedHub, ...changes } = value
      await updateApp(editing.id, editing.revision, changes)
    } else await createApp(value)
    setEditing(undefined); await load()
  }

  async function archive(app: AppRecord) {
    try { await updateApp(app.id, app.revision, { archived: !app.archived }); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to update application') }
  }

  async function move(app: AppRecord, direction: -1 | 1) {
    const current = active.findIndex((entry) => entry.id === app.id)
    const target = current + direction
    if (current < 0 || target < 0 || target >= active.length) return
    const ordered = [...active]; [ordered[current], ordered[target]] = [ordered[target], ordered[current]]
    try { await reorderApps(hub, ordered); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to reorder applications') }
  }

  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">Application registry</p><h1>{title}</h1><p>{subtitle}</p></div><button className="button button--primary" onClick={() => setEditing(null)}>+ Add application</button></header>
    <div className="toolbar"><label className="search"><span className="sr-only">Search applications</span><input type="search" placeholder="Search title, category or tags…" value={search} onChange={(e) => setSearch(e.target.value)} /></label><label className="check check--compact"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /><span>Show archived</span></label></div>
    {error && <div className="notice notice--error" role="alert">{error}<button onClick={() => void load()}>Retry</button></div>}
    {loading ? <div className="loading">Loading registry…</div> : visible.length === 0 ? <EmptyState title={search ? 'No matching applications' : `No ${hub} applications yet`}>{search ? 'Try another search or show archived entries.' : 'Add an existing web application without changing Hub code.'}</EmptyState> : <div className="app-grid">{visible.map((app) => <AppCard key={app.id} app={app} index={active.findIndex((item) => item.id === app.id)} count={active.length} onEdit={() => setEditing(app)} onMove={(direction) => void move(app, direction)} onArchive={() => void archive(app)} />)}</div>}
    {editing !== undefined && <AppForm hub={hub} app={editing ?? undefined} onCancel={() => setEditing(undefined)} onSave={save} />}
  </div>
}
