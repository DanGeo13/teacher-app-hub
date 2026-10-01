import { FormEvent, useState } from 'react'
import type { AppInput, AppRecord, HubName, IntegrationMode } from '../types'

interface Props {
  hub: HubName
  app?: AppRecord
  onSave: (value: AppInput) => Promise<void>
  onCancel: () => void
}

export function AppForm({ hub, app, onSave, onCancel }: Props) {
  const [title, setTitle] = useState(app?.title ?? '')
  const [description, setDescription] = useState(app?.description ?? '')
  const [category, setCategory] = useState(app?.category ?? '')
  const [tags, setTags] = useState(app?.tags.join(', ') ?? '')
  const [launchUrl, setLaunchUrl] = useState(app?.launchUrl ?? '')
  const [developmentUrl, setDevelopmentUrl] = useState(app?.developmentUrl ?? '')
  const [projectPath, setProjectPath] = useState(app?.projectPath ?? '')
  const [integrationMode, setIntegrationMode] = useState<IntegrationMode>(app?.integrationMode ?? 'external_link')
  const [authenticationRequirements, setAuthenticationRequirements] = useState(app?.authenticationRequirements ?? '')
  const [aiAdapterSupport, setAiAdapterSupport] = useState(app?.aiAdapterSupport ?? false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await onSave({
        hub, title, description, category,
        tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean),
        icon: app?.icon ?? 'app',
        projectPath: projectPath || null,
        launchUrl,
        developmentUrl: developmentUrl || null,
        integrationMode,
        authenticationRequirements,
        themeAdapterVersion: app?.themeAdapterVersion ?? null,
        aiAdapterSupport,
        healthStatus: app?.healthStatus ?? 'unknown',
        scriptId: app?.scriptId ?? null,
        deploymentId: app?.deploymentId ?? null,
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to save application')
    } finally { setBusy(false) }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
    <section className="modal" role="dialog" aria-modal="true" aria-labelledby="app-form-title">
      <div className="modal__header"><div><p className="eyebrow">{hub} registry</p><h2 id="app-form-title">{app ? 'Edit application' : 'Add application'}</h2></div><button className="icon-button" onClick={onCancel} aria-label="Close">×</button></div>
      <form onSubmit={submit} className="app-form">
        <div className="field-grid">
          <label>Title<input autoFocus required maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
          <label>Category<input required maxLength={80} value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Classroom tools" /></label>
        </div>
        <label>Description<textarea maxLength={1000} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label>Launch URL<input required type="url" value={launchUrl} onChange={(e) => setLaunchUrl(e.target.value)} placeholder="https://script.google.com/…" /></label>
        <div className="field-grid">
          <label>Development URL <span className="optional">optional</span><input type="url" value={developmentUrl} onChange={(e) => setDevelopmentUrl(e.target.value)} /></label>
          <label>Repository path <span className="optional">optional</span><input value={projectPath} onChange={(e) => setProjectPath(e.target.value)} placeholder="Apps/teaching/example" /></label>
        </div>
        <div className="field-grid">
          <label>Integration mode<select value={integrationMode} onChange={(e) => setIntegrationMode(e.target.value as IntegrationMode)}><option value="external_link">External link</option><option value="embedded_legacy">Embedded legacy app</option><option value="hub_aware">Hub-aware app</option></select></label>
          <label>Tags <span className="optional">comma-separated</span><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="design, year 9" /></label>
        </div>
        <label>Authentication notes <span className="optional">optional</span><input maxLength={500} value={authenticationRequirements} onChange={(e) => setAuthenticationRequirements(e.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={aiAdapterSupport} onChange={(e) => setAiAdapterSupport(e.target.checked)} /><span>Application declares Hub AI adapter support</span></label>
        <p className="form-note">This flag records declared support only. It does not prove that AI integration works.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="modal__actions"><button type="button" className="button button--ghost" onClick={onCancel}>Cancel</button><button className="button button--primary" disabled={busy}>{busy ? 'Saving…' : 'Save application'}</button></div>
      </form>
    </section>
  </div>
}
