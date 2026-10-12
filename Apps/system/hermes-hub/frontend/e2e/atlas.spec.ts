import { expect, test } from '@playwright/test'

const PASSWORD = 'E2E-only-password-1234'

test('Atlas page shows approvals, health and an honest verification result', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Administrator password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Good day.' })).toBeVisible()
  await page.goto('/#/atlas')
  await expect(page.getByRole('heading', { name: 'Waiting for your approval' })).toBeVisible()
  await expect(page.getByText('Nothing is waiting for approval.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'System health', level: 2 })).toBeVisible()
  await page.getByRole('button', { name: 'Verify Atlas on this host' }).click()
  // CI is not the Pixel host: the run must say so and never claim Atlas works.
  await expect(page.getByText(/Not on-device proof/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Approval round trip (synthetic)')).toBeVisible()
})
