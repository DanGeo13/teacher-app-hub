export interface PwaRegistrationResult {
  supported: boolean
  registration?: ServiceWorkerRegistration
}

export async function registerPwa(onUpdate: (registration: ServiceWorkerRegistration) => void): Promise<PwaRegistrationResult> {
  if (!('serviceWorker' in navigator)) return { supported: false }
  const registration = await navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
  if (registration.waiting) onUpdate(registration)
  registration.addEventListener('updatefound', () => {
    const installing = registration.installing
    installing?.addEventListener('statechange', () => {
      if (installing.state === 'installed' && navigator.serviceWorker.controller) {
        onUpdate(registration)
      }
    })
  })
  return { supported: true, registration }
}

export function applyPwaUpdate(registration: ServiceWorkerRegistration): void {
  registration.waiting?.postMessage({ type: 'SKIP_WAITING' })
}
