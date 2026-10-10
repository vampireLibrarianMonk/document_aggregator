import { expect, test } from '@playwright/test'
import { assertBackendUp } from './helpers'

// Diagnostics page, driven through the real browser. Confirms every service row
// renders with a status, the versions/offline-guard/data-dir footer is present,
// Refresh re-fetches, and (finding D1) a disabled Bedrock reads "Offline", not
// "OK", with the Enabled/In-use rows making the posture explicit.

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
})

test('diagnostics renders every service with a status and the footer', async ({ page }) => {
  await page.goto('/diagnostics')

  // Overall posture indicator is present.
  await expect(page.getByText('overall')).toBeVisible()

  // Each of the four services renders as a titled sub-panel with a status tag.
  // The title <strong> and its indicator share a .row; assert on that row to
  // avoid the nested-.panel ambiguity (the outer Diagnostics panel wraps them).
  for (const title of [
    'Embeddings (semantic search)',
    'OCR (scanned PDFs and images)',
    'Layout geometry tier (LibreOffice)',
    'Bedrock (optional model tier)',
  ]) {
    const heading = page.getByText(title, { exact: true })
    await expect(heading).toBeVisible()
    // The status tag lives in the same .row as the heading.
    const row = heading.locator('xpath=ancestor::div[contains(@class,"row")][1]')
    await expect(row.locator('.status-tag')).toBeVisible()
  }

  // The footer carries versions, offline guards, and the data dir.
  await expect(page.getByText(/Pipeline v/)).toBeVisible()
  await expect(page.getByText(/schema v/)).toBeVisible()
  await expect(page.getByText(/offline guard/)).toBeVisible()
  await expect(page.getByText(/data dir/)).toBeVisible()
})

test('embeddings report the real model + dimensions in the container', async ({ page }) => {
  await page.goto('/diagnostics')
  // The baked MiniLM model + 384 dims are reported (worker/api images bake the
  // weights). These strings are unique to the embeddings panel.
  await expect(page.getByText('all-MiniLM-L6-v2')).toBeVisible()
  await expect(page.getByText('384', { exact: true })).toBeVisible()
})

test('disabled Bedrock reads Offline, not OK (finding D1)', async ({ page, request }) => {
  // Only meaningful when the deployment has Bedrock disabled (the default).
  const cfgRes = await request.get('/api/diagnostics')
  const diag = await cfgRes.json()
  test.skip(diag.services.bedrock.enabled === true, 'Bedrock is enabled in this deployment')

  await page.goto('/diagnostics')
  const heading = page.getByText('Bedrock (optional model tier)', { exact: true })
  const row = heading.locator('xpath=ancestor::div[contains(@class,"row")][1]')
  // The indicator must be the muted "Offline" word, never "OK".
  await expect(row.locator('.status-tag')).toHaveText('Offline')
  // The posture detail is explicit on the page (Enabled: no (disabled by config)).
  await expect(page.getByText('disabled by config')).toBeVisible()
})

test('Refresh re-fetches diagnostics', async ({ page }) => {
  await page.goto('/diagnostics')
  // Count the /diagnostics calls triggered by clicking Refresh.
  let calls = 0
  page.on('request', (req) => { if (req.url().includes('/api/diagnostics')) calls += 1 })
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect.poll(() => calls, { timeout: 5000 }).toBeGreaterThanOrEqual(1)
})
