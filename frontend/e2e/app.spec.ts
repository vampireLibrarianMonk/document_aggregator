import { expect, test } from '@playwright/test'
import { assertBackendUp, openTab, samplesEnabled, wipeProjects } from './helpers'

// Smoke / navigation: the app shell, empty-start behavior, tab gating, and the
// project selector. No sample data required beyond a clean slate.

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
  await wipeProjects(request)
})

test('app loads and starts empty on New Project', async ({ page }) => {
  await page.goto('/')
  // The header is always present.
  await expect(page.getByRole('heading', { name: 'Document Aggregation Pipeline' }))
    .toBeVisible()
  // Empty app: the project selector shows the no-projects option.
  await expect(page.locator('#active-project')).toContainText(/No projects yet/i)
  // New Project tab is the active one on a fresh app.
  await expect(page.getByRole('tab', { name: 'New Project' })).toHaveAttribute(
    'aria-selected', 'true')
})

test('downstream tabs are gated until prerequisites are met', async ({ page }) => {
  await page.goto('/')
  // With no project, Correction Pipeline / Report & Export are disabled.
  for (const name of ['Correction Pipeline', 'Report & Export']) {
    await expect(page.getByRole('tab', { name })).toHaveAttribute(
      'aria-disabled', 'true')
  }
})

test('Samples tab is present when the feature is enabled', async ({ page, request }) => {
  test.skip(!(await samplesEnabled(request)), 'SAMPLES_ENABLED is off')
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Samples' })).toBeVisible()
  // And it opens.
  await openTab(page, 'Samples')
  await expect(page.getByRole('tab', { name: 'Samples' })).toHaveAttribute(
    'aria-selected', 'true')
})
