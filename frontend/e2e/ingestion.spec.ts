import { expect, test, Page } from '@playwright/test'
import { join } from 'node:path'
import {
  assertBackendUp,
  createProject,
  deleteProject,
  openTab,
  SAMPLE_DOCS,
  selectProject,
} from './helpers'
import { INGESTION_READY } from './expected'

// Ingestion / upload, driven through the real browser: create an EMPTY project,
// upload the standard source document set (corpus .txt/.md + figures) via the
// real file input, watch each row reach "completed", and confirm readiness
// flips incomplete -> ready (the app GENERATES the project from the uploads) and
// the Correction Pipeline / Report tabs unlock. This is TGX-9's upload step end
// to end — no hand-authored JSON, which the current flow no longer uses.

const CORPUS = join(SAMPLE_DOCS, '1', 'corpus')
// Everything is uploaded into the "Original corpus" area; the app derives the
// manifest/template/draft/corrections from these real documents.
const CORPUS_FILES = [
  join(CORPUS, 'field_report_2026-03-02.txt'),
  join(CORPUS, 'root_cause_notes_2026-03-15.md'),
  join(CORPUS, 'figures', 'packet_loss_vs_temp.png'),
  join(CORPUS, 'figures', 'site_network_topology.png'),
  join(CORPUS, 'figures', 'cabinet_thermal_layout.png'),
]

let projectId = ''

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
  projectId = await createProject(request, 'E2E ingestion upload')
})

test.afterAll(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
})

/** Upload files into the "Original corpus" area's hidden file input. */
async function uploadToCorpus(page: Page, files: string[]) {
  const panel = page.locator('.panel', {
    has: page.getByText('1. Original corpus', { exact: true }),
  })
  await panel.locator('input[type="file"]').setInputFiles(files)
}

test('upload the corpus set, readiness flips to ready, tabs unlock', async ({ page }) => {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Ingestion')

  // Readiness starts with the "add your documents" prompt.
  await expect(page.getByText('Add your documents')).toBeVisible()

  // Upload the whole document set and wait for every row to reach "completed".
  await uploadToCorpus(page, CORPUS_FILES)
  await expect
    .poll(async () => page.locator('.docs .badge.completed').count(),
      { timeout: 30_000 })
    .toBeGreaterThanOrEqual(CORPUS_FILES.length)

  // Readiness flips to "Project ready" once the app generates the project from
  // the uploads.
  await expect(page.getByText('Project ready')).toBeVisible({ timeout: 15_000 })

  // The Correction Pipeline + Report tabs are now enabled.
  await expect(page.getByRole('tab', { name: 'Correction Pipeline' }))
    .not.toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByRole('tab', { name: 'Report & Export' }))
    .not.toHaveAttribute('aria-disabled', 'true')
})

test('readiness reflects the uploaded corpus set', async ({ request }) => {
  // Verify the backend readiness reflects the uploads via the API the UI reads.
  const res = await request.get(`/api/projects/${projectId}/readiness`)
  expect(res.ok()).toBeTruthy()
  const r = await res.json()
  expect(r.ready).toBe(true)
  expect(r.counts).toMatchObject(INGESTION_READY)
})
