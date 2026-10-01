import { expect, test, type Page } from '@playwright/test'

const PASSWORD = 'E2E-only-password-1234'

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByLabel('Administrator password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Good day.' })).toBeVisible()
}

test('login, logout and session invalidation are observable', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByLabel('Administrator password')).toBeVisible()

  const response = await page.evaluate(async () => {
    const result = await fetch('/api/apps?hub=teaching')
    return result.status
  })
  expect(response).toBe(401)
})

test('a failed logout is visible and does not pretend the session ended', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/auth/logout', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Temporary logout failure' }),
    })
  })

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Good day.' })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Temporary logout failure')

  const session = await page.evaluate(async () => {
    const result = await fetch('/api/auth/session')
    return result.status
  })
  expect(session).toBe(200)
})

test('an authenticated API 401 returns the UI to sign-in', async ({ page }) => {
  await signIn(page)
  await page.route('**/api/apps**', async (route) => {
    if (route.request().url().includes('/api/apps?hub=teaching')) {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Session expired' }) })
    } else {
      await route.continue()
    }
  })

  await page.getByRole('link', { name: 'Teacher Hub' }).first().click()
  await expect(page.getByRole('heading', { name: 'Welcome to Hermes Hub' })).toBeVisible()
  await expect(page.getByLabel('Administrator password')).toBeVisible()
})

test('registry add, reorder, edit, archive, restore and reload persist', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Teacher Hub' }).first().click()

  async function add(title: string) {
    await page.getByRole('button', { name: 'Add application' }).click()
    await page.getByLabel('Title').fill(title)
    await page.getByLabel('Category').fill('Browser validation')
    await page.getByLabel('Description').fill('Synthetic browser validation fixture.')
    await page.getByLabel('Launch URL').fill('https://example.test/browser-validation')
    await page.getByRole('button', { name: 'Save application' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
  }

  await add('Browser Planner Alpha')
  await add('Browser Planner Beta')

  const beta = page.locator('.app-card').filter({ hasText: 'Browser Planner Beta' })
  await beta.getByRole('button', { name: 'Move Browser Planner Beta earlier' }).click()
  await expect(page.locator('.app-card h3').first()).toHaveText('Browser Planner Beta')

  await page.locator('.app-card').filter({ hasText: 'Browser Planner Beta' }).getByRole('button', { name: 'Edit' }).click()
  await page.getByLabel('Title').fill('Browser Planner Beta Edited')
  await page.getByRole('button', { name: 'Save application' }).click()
  await expect(page.getByRole('heading', { name: 'Browser Planner Beta Edited' })).toBeVisible()

  const edited = page.locator('.app-card').filter({ hasText: 'Browser Planner Beta Edited' })
  await edited.getByRole('button', { name: 'Archive' }).click()
  await expect(page.getByRole('heading', { name: 'Browser Planner Beta Edited' })).toHaveCount(0)

  await page.getByLabel('Show archived').check()
  await expect(page.getByRole('heading', { name: 'Browser Planner Beta Edited' })).toBeVisible()
  await page.locator('.app-card').filter({ hasText: 'Browser Planner Beta Edited' }).getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByRole('heading', { name: 'Browser Planner Beta Edited' })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Browser Planner Beta Edited' })).toBeVisible()
})
