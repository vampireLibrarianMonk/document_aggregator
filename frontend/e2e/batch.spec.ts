import { expect, test, Page } from '@playwright/test'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  assertBackendUp,
  createProject,
  deleteProject,
  openTab,
  selectProject,
} from './helpers'

// JSON->golden batch, driven through the real BatchPanel UI, exercising all
// FIVE pathways the json-batch guide describes:
//   novel_research -> approve -> replay_clean -> drift_repair -> reject_irrelevant
//
// The lifecycle matters: submit a shape ONCE, approve it, THEN re-submit the
// same shape so it replays. Re-submitting an unapproved shape re-learns it.

const GOLDEN = JSON.stringify({
  title: 'game', type: 'object',
  properties: {
    name: { type: 'string', description: 'The title of the game' },
    releaseYear: { type: 'string', pattern: '^\\d{4}$', description: 'the year the game was published' },
    developer: { type: 'string', description: 'the studio that developed or made the game' },
    genres: { type: 'array', items: { type: 'string' }, description: 'list of genres' },
    criticScore: { type: 'integer', minimum: 0, maximum: 100, description: 'critic press rating out of 100' },
  },
  required: ['name', 'releaseYear'],
}, null, 2)

// Write JSON batch files to a temp dir to upload via the file input.
const DIR = mkdtempSync(join(tmpdir(), 'e2e-batch-'))
function writeDoc(name: string, records: unknown[]): string {
  const p = join(DIR, name)
  writeFileSync(p, JSON.stringify(records, null, 2))
  return p
}
const CLEAN = writeDoc('team_clean.json', [
  { name: 'Celeste', releaseYear: '2018', developer: 'Maddy Makes Games', genres: ['Platformer'], criticScore: 92 },
])
const CLEAN2 = writeDoc('team_clean_2.json', [
  { name: 'Hades', releaseYear: '2020', developer: 'Supergiant Games', genres: ['Roguelike'], criticScore: 93 },
])
const DRIFTED = writeDoc('team_clean_drifted.json', [
  { name: 'Hollow Knight', releaseYear: '2017', developer: 'Team Cherry', genres: ['Metroidvania'], criticScore: 'not available' },
])
const JUNK = writeDoc('weather.json', [
  { temperature_c: 21, humidity: 55, wind_kph: 12 },
])

let projectId = ''

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
  projectId = await createProject(request, 'E2E JSON batch')
})

test.afterAll(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
})

/** Open the project's Ingestion tab and turn on Batch mode. */
async function openBatch(page: Page) {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Ingestion')
  const toggle = page.getByRole('checkbox', { name: 'Enable batch conversion mode' })
  if (!(await toggle.isChecked())) await toggle.check()
}

/** Upload JSON files + process, returning when the Batch result renders. */
async function processFiles(page: Page, files: string[], research: boolean) {
  const learn = page.getByRole('checkbox', { name: /Learn new shapes/i })
  if (research !== (await learn.isChecked())) await learn.setChecked(research)
  // The hidden batch file input is the one that accepts .json inside the
  // Upload & process panel.
  const panel = page.locator('.panel', { has: page.getByText('Upload & process') })
  await panel.locator('input[type="file"]').setInputFiles(files)
  // Wait for the Batch result panel to appear/update.
  await expect(page.getByText('Batch result')).toBeVisible({ timeout: 30_000 })
}

/** Read the pathway -> count rows from the Batch result table. */
async function pathwayCounts(page: Page): Promise<Record<string, number>> {
  const rows = page.locator('.panel', { has: page.getByText('Batch result') })
    .locator('table.docs tbody tr')
  const out: Record<string, number> = {}
  for (let i = 0; i < await rows.count(); i++) {
    const cells = rows.nth(i).locator('td')
    const label = (await cells.nth(0).innerText()).trim()
    const n = Number((await cells.nth(1).innerText()).trim())
    out[label] = n
  }
  return out
}

test('batch: set golden schema', async ({ page }) => {
  await openBatch(page)
  await page.locator('#golden-schema').fill(GOLDEN)
  await page.getByRole('button', { name: 'Save golden schema' }).click()
  await expect(page.getByText(/Saved: game/)).toBeVisible()
})

test('batch: relevance dial persists to the project', async ({ page, request }) => {
  await openBatch(page)
  // Default 50%. Nudge it and confirm it persisted on the project.
  const dial = page.locator('#reject-dial')
  await dial.focus()
  await dial.fill('60')  // range inputs accept fill() of the value
  await dial.dispatchEvent('pointerup')
  await expect
    .poll(async () => {
      const r = await request.get(`/api/projects/${projectId}`)
      return (await r.json()).reject_below
    }, { timeout: 10_000 })
    .toBeCloseTo(0.6, 2)
  // put it back to 50 for the pathway tests
  await dial.fill('50'); await dial.dispatchEvent('pointerup')
})

test('batch: novel -> approve -> replay (3 pathways)', async ({ page }) => {
  await openBatch(page)
  // 1) NOVEL: a new shape, research on -> learned provisionally.
  await processFiles(page, [CLEAN], true)
  let counts = await pathwayCounts(page)
  expect(counts['New shape — needs approval']).toBeGreaterThanOrEqual(1)

  // An approval card appears; approve it.
  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).first().click()
  await expect(page.getByText('Shapes awaiting your approval')).toBeHidden({ timeout: 10_000 })

  // 2) REPLAY: the SAME shape now converts automatically (zero re-inference).
  await processFiles(page, [CLEAN2], true)
  counts = await pathwayCounts(page)
  expect(counts['Converted (known shape)']).toBeGreaterThanOrEqual(1)
})

test('batch: drift_repair pathway', async ({ page }) => {
  await openBatch(page)
  // Same shape as the approved one, but criticScore is non-numeric -> fill
  // rate collapses -> drift_repair.
  await processFiles(page, [DRIFTED], true)
  const counts = await pathwayCounts(page)
  expect(counts['Drift — needs re-approval']).toBeGreaterThanOrEqual(1)
})

test('batch: reject_irrelevant pathway (unrelated file, research off)', async ({ page }) => {
  await openBatch(page)
  await processFiles(page, [JUNK], false)
  const counts = await pathwayCounts(page)
  expect(counts['Quarantined (unrelated)']).toBeGreaterThanOrEqual(1)
})
