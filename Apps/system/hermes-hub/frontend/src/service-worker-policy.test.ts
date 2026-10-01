import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('service worker privacy policy', () => {
  const source = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf8')

  it('makes API requests network-only before cache handling', () => {
    const apiGuard = source.indexOf("url.pathname.startsWith('/api/')")
    const cacheLookup = source.indexOf('caches.match(request)')
    expect(apiGuard).toBeGreaterThan(0)
    expect(cacheLookup).toBeGreaterThan(apiGuard)
  })

  it('does not pre-cache private API routes', () => {
    const assetBlock = source.slice(source.indexOf('OFFLINE_ASSETS'), source.indexOf('self.addEventListener'))
    expect(assetBlock).not.toContain('/api/')
    expect(assetBlock).not.toMatch(/conversation|approval|session|student/i)
  })

  it('limits runtime caching to declared public asset paths', () => {
    expect(source).toContain("url.pathname.startsWith('/assets/')")
    expect(source).toContain("url.pathname.startsWith('/icons/')")
    expect(source).not.toContain("new Set(['script', 'style', 'image'")
  })

  it('caches only the public root navigation shell', () => {
    expect(source).toContain("url.pathname !== '/' && url.pathname !== '/index.html'")
    expect(source).toContain("response.ok && response.type === 'basic'")
  })
})
