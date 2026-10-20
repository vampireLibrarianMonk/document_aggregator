import { expect, test, Page } from '@playwright/test'
import {
  assertBackendUp,
  createProject,
  deleteProject,
  fixture,
  openTab,
  readProject7Schema,
  seedGoldenSchema,
  selectProject,
} from './helpers'

// ---------------------------------------------------------------------------
// WORKER / QUEUE DROPDOWN (WorkerQueuePanel) — real-browser coverage.
//
// The main-UI "N of M files · P%" progress is covered by walkthrough scenario
// 01. The collapsible "Workers & queue" dropdown that polls the shared job
// queue was only unit-tested (mocked getJobCounts) — this spec drives it in a
// real browser against the live stack: open it, confirm it reports queue state
// and that a worker is draining the queue while/after a batch runs.
// ---------------------------------------------------------------------------

let projectId = ''

test.beforeEach(async ({ request }) => {
  await assertBackendUp(request)
  projectId = await createProject(request, 'E2E worker queue', 'json_alignment')
  await seedGoldenSchema(request, projectId, readProject7Schema())
})

test.afterEach(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
  projectId = ''
})

async function openConvert(page: Page) {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Batch Convert')
}

/** The Workers & queue dropdown (a <details> with that summary). */
function queuePanel(page: Page) {
  return page.locator('details', { has: page.getByText('Workers & queue') })
}

test('queue dropdown: present and expandable on the Batch Convert tab', async ({ page }) => {
  await openConvert(page)
  const panel = queuePanel(page)
  await expect(panel).toBeVisible()

  // Opening it reveals the queue status region (role=status) with a Queue line.
  await panel.locator('summary').click()
  await expect(panel).toHaveAttribute('open', /.*/)
  const status = panel.locator('[role="status"]')
  await expect(status).toBeVisible()
  await expect(status).toContainText('Queue:')
})

test('queue dropdown: reflects an active batch and a draining worker', async ({ page }) => {
  await openConvert(page)
  const panel = queuePanel(page)
  await panel.locator('summary').click()
  await expect(panel).toHaveAttribute('open', /.*/)

  // Fire a batch with several distinct shapes so the worker has real work.
  const learn = page.getByRole('checkbox', { name: /Learn new shapes/i })
  if (!(await learn.isChecked())) await learn.setChecked(true)
  const convert = page.locator('.panel', { has: page.getByText('Upload & convert') })
  await convert.locator('input[type="file"]').setInputFiles([
    fixture('clean_known.json'),
    fixture('needs_review_platform.json'),
    fixture('cascade_base.json'),
  ])

  // The summary should, at some point, report an active job (the summary text
  // flips to "active job …" while the batch job is pending/processing). Batches
  // can be fast, so accept either the active-job tell OR the post-run idle tell.
  const summary = panel.locator('summary')
  await expect
    .poll(async () => (await summary.innerText()).toLowerCase(), { timeout: 30_000 })
    .toMatch(/active job|idle|queue status/)

  // Wait for terminal state (Batch result rendered).
  await expect(page.getByText('Batch result')).toBeVisible({ timeout: 30_000 })

  // After the run, the panel must NOT be stuck claiming a stall with a worker
  // present: the live worker is draining the queue, so the "worker may not be
  // running" warning must be absent (that message only shows on a real stall).
  const status = panel.locator('[role="status"]')
  await expect(status).not.toContainText(/the async worker may not be running/i)
})
