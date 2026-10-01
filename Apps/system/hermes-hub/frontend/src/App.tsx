import { useCallback, useEffect, useState } from 'react'
import { getRuntime, logout, restoreSession } from './api'
import { Dashboard } from './components/Dashboard'
import { Login } from './components/Login'
import { Maintenance } from './components/Maintenance'
import { RegistryPage } from './components/RegistryPage'
import { applyPwaUpdate, registerPwa } from './pwa'
import type { RuntimeState, SessionState } from './types'

type Page = 'dashboard' | 'teacher' | 'lifestyle' | 'maintenance'
const pages: Page[] = ['dashboard', 'teacher', 'lifestyle', 'maintenance']
const labels: Record<Page, string> = { dashboard: 'Dashboard', teacher: 'Teacher Hub', lifestyle: 'Lifestyle Hub', maintenance: 'Maintenance' }
const icons: Record<Page, string> = { dashboard: '◫', teacher: 'T', lifestyle: 'L', maintenance: '⌁' }

function pageFromHash(): Page {
  const candidate = window.location.hash.replace(/^#\/?/, '') as Page
  return pages.includes(candidate) ? candidate : 'dashboard'
}

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null)
  const [sessionError, setSessionError] = useState('')
  const [page, setPage] = useState<Page>(pageFromHash())
  const [runtime, setRuntime] = useState<RuntimeState | null>(null)
  const [runtimeError, setRuntimeError] = useState('')
  const [online, setOnline] = useState(navigator.onLine)
  const [update, setUpdate] = useState<ServiceWorkerRegistration | null>(null)

  useEffect(() => {
    restoreSession().then(setSession).catch((reason) => {
      setSessionError(reason instanceof Error ? reason.message : 'Backend unavailable')
      setSession({ authenticated: false })
    })
    const hash = () => setPage(pageFromHash())
    const connected = () => setOnline(true)
    const disconnected = () => setOnline(false)
    window.addEventListener('hashchange', hash)
    window.addEventListener('online', connected)
    window.addEventListener('offline', disconnected)
    void registerPwa(setUpdate).catch(() => { /* installability is optional */ })
    const reload = () => window.location.reload()
    navigator.serviceWorker?.addEventListener('controllerchange', reload)
    return () => {
      window.removeEventListener('hashchange', hash)
      window.removeEventListener('online', connected)
      window.removeEventListener('offline', disconnected)
      navigator.serviceWorker?.removeEventListener('controllerchange', reload)
    }
  }, [])

  const refreshRuntime = useCallback(async () => {
    if (!session?.authenticated) return
    setRuntimeError('')
    try { setRuntime(await getRuntime()) }
    catch (reason) { setRuntimeError(reason instanceof Error ? reason.message : 'Runtime probes failed') }
  }, [session?.authenticated])

  useEffect(() => { void refreshRuntime() }, [refreshRuntime])

  async function signOut() {
    await logout(); setSession({ authenticated: false }); setRuntime(null)
  }

  if (session === null) return <main className="loading-screen"><div className="brand-mark">H</div><p>Opening Hermes Hub…</p></main>
  if (!session.authenticated) return <><Login onLogin={(state) => { setSession(state); setSessionError('') }} />{sessionError && <div className="offline-corner">{sessionError}</div>}</>

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">H</div><div><strong>Hermes Hub</strong><span>Teacher app workspace</span></div></div>
      <nav aria-label="Primary navigation">{pages.map((item) => <a key={item} href={`#/${item}`} className={page === item ? 'nav-link nav-link--active' : 'nav-link'}><span aria-hidden="true">{icons[item]}</span>{labels[item]}</a>)}</nav>
      <div className="sidebar__footer"><div className={`connection-dot ${runtime?.hermes.status === 'available' ? 'connection-dot--online' : ''}`} /><div><strong>Runtime</strong><span>{runtime?.hermes.status ?? 'checking'}</span></div><button className="text-button" onClick={() => void signOut()}>Sign out</button></div>
    </aside>
    <main className="content">
      {(!online || sessionError) && <div className="offline-banner" role="status"><strong>Read-only offline shell.</strong> The registry, Hermes agents and AI are unavailable until the backend reconnects.</div>}
      {update && <div className="update-banner" role="status"><span>A Hub update is ready.</span><button onClick={() => applyPwaUpdate(update)}>Reload and update</button></div>}
      {page === 'dashboard' && <Dashboard runtime={runtime} runtimeError={runtimeError} onRefresh={() => void refreshRuntime()} />}
      {page === 'teacher' && <RegistryPage hub="teaching" />}
      {page === 'lifestyle' && <RegistryPage hub="personal" />}
      {page === 'maintenance' && <Maintenance runtime={runtime} onRefresh={() => void refreshRuntime()} />}
    </main>
    <nav className="mobile-nav" aria-label="Mobile navigation">{pages.map((item) => <a key={item} aria-label={labels[item]} href={`#/${item}`} className={page === item ? 'mobile-nav__active' : ''}><span>{icons[item]}</span><small>{item === 'maintenance' ? 'Maintain' : labels[item].split(' ')[0]}</small></a>)}</nav>
  </div>
}
