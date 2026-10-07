import { expect, test, Page } from '@playwright/test'
import { join } from 'node:path'
import {
  assertBackendUp,
  createProject,
  deleteProject,
  openTab,
  selectProject,
} from './helpers'
import { INGESTION_READY } from './expected'

// Ingestion / upload pathways, driven through the real browser: create an EMPTY
// project, upload the four document pathways (corpus x2, template, first_draft,
// corrections) via the real file input, watch each row reach "completed", and
// confirm the readiness banner flips incomplete -> ready and the Correction
// Pipeline / Report tabs unlock. This is TGX-9's Step 2 end to end.

// sample_docs lives at repo-root/sample_docs; specs run with cwd = frontend/.
const SAMPLES = join(process.cwd(), '..', 'sample_docs', 'project', '1')
// Area titles are the exact numbered <strong> labels in IngestionBoard.
const FILES: { area: string; files: string[] }[] = [
  { area: '1. Original corpus', files: [
    join(SAMPLES, 'corpus', 'field_report_2026-03-02.txt'),
    join(SAMPLES, 'corpus', 'root_cause_notes_2026-03-15.md'),
  ] },
  { area: '2. Template', files: [join(SAMPLES, 'template', 'incident_report_template.json')] },
  { area: '4. First draft', files: [join(SAMPLES, 'first_attempt', 'incident_report_draft.json')] },
  { area: '3. Corrections', files: [join(SAMPLES, 'corrections', 'comments.json')] },
]

let projectId = ''

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
  projectId = await createProject(request, 'E2E ingestion upload')
})

test.afterAll(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
})

/** Upload files into one named Ingestion area's hidden file input. The area is
 *  the .panel whose heading <strong> is exactly `area` (e.g. "2. Template"). */
async function uploadToArea(page: Page, area: string, files: string[]) {
  const panel = page.locator('.panel', {
    has: page.getByText(area, { exact: true }),
  })
  await panel.locator('input[type="file"]').setInputFiles(files)
}

test('upload the four pathways, readiness flips to ready, tabs unlock', async ({ page }) => {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Ingestion')

  // Readiness starts incomplete.
  await expect(page.getByText('Inputs incomplete')).toBeVisible()

  // Upload each pathway and wait for its row(s) to reach "completed".
  let expectedRows = 0
  for (const { area, files } of FILES) {
    await uploadToArea(page, area, files)
    expectedRows += files.length
    // Each uploaded doc renders a row with a "completed" status badge.
    await expect
      .poll(async () => page.locator('.docs .badge.completed').count(),
        { timeout: 20_000 })
      .toBeGreaterThanOrEqual(expectedRows)
  }

  // Readiness flips to ready.
  await expect(page.getByText('Inputs ready')).toBeVisible({ timeout: 15_000 })

  // The Correction Pipeline + Report tabs are now enabled.
  await expect(page.getByRole('tab', { name: 'Correction Pipeline' }))
    .not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByRole('tab', { name: 'Report & Export' }))
    .not.toHaveAttribute('aria-disabled', 'true')
})

test('readiness counts match the four pathways', async ({ request }) => {
  // Verify the backend readiness reflects the uploads (corpus:2 etc.). This
  // asserts the same counts the guide quotes, via the API the UI reads.
  const res = await request.get(`/api/projects/${projectId}/readiness`)
  expect(res.ok()).toBeTruthy()
  const r = await res.json()
  expect(r.ready).toBe(true)
  expect(r.counts).toMatchObject(INGESTION_READY)
})
