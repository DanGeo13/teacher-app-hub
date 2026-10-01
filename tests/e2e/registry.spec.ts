import { expect, test } from '@playwright/test'

test('adds an application and retains it after reload', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Administrator password').fill('E2E-only-password-1234')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Good day.' })).toBeVisible()

  await page.getByRole('link', { name: 'Teacher Hub' }).first().click()
  await page.getByRole('button', { name: 'Add application' }).click()
  await page.getByLabel('Title').fill('Synthetic Workshop Planner')
  await page.getByLabel('Category').fill('Classroom tools')
  await page.getByLabel('Description').fill('Synthetic test fixture with no student data.')
  await page.getByLabel('Launch URL').fill('https://example.test/workshop')
  await page.getByRole('button', { name: 'Save application' }).click()

  await expect(page.getByRole('heading', { name: 'Synthetic Workshop Planner' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Synthetic Workshop Planner' })).toBeVisible()
})
