import { expect, test, Page } from '@playwright/test'
import {
  assertBackendUp,
  deleteProject,
  instantiateSample,
  openTab,
  samplesEnabled,
  selectProject,
} from './helpers'
import { SAMPLE_CASES, CorrectionCounts } from './expected'

// Correction pipeline, driven through the real UI for all six sample projects.
// For each: instantiate the sample, open the Correction Pipeline, read the
// on-screen summary (the status-tag counts + the intermediate-JSON units box),
// assert they match the guide, confirm the conflict row reads "unresolved",
// then flip to Template mode and assert its summary. The exact six-guide tables.

test.beforeAll(async ({ request }) => {
  await assertBackendUp(request)
  test.skip(!(await samplesEnabled(request)), 'SAMPLES_ENABLED is off')
})

/** Read the visible correction summary: total_units from the intermediate_json
 *  flow-box, and each status count from the status-tag row. */
async function readSummary(page: Page): Promise<CorrectionCounts> {
  // total_units is the count inside the "Corrected intermediate JSON" flow box.
  const unitsBox = page.locator('.flow-box', { hasText: /intermediate json/i })
  const totalText = (await unitsBox.locator('.count').innerText()).trim()

  const counts: CorrectionCounts = {
    total: Number(totalText),
    unchanged: 0, filled: 0, corrected: 0, needs_review: 0, conflict: 0,
  }
  // Each status renders as `<span class="status-tag {key}">{key}</span> {n}`.
  for (const key of ['unchanged', 'filled', 'corrected', 'needs_review', 'conflict'] as const) {
    const tag = page.locator(`.status-tag.${key}`)
    if (await tag.count()) {
      // The count is the text node right after the tag span, in the same parent.
      const parent = tag.first().locator('xpath=..')
      const txt = (await parent.innerText()).trim()
      const m = txt.match(/(\d+)\s*$/)
      counts[key] = m ? Number(m[1]) : 0
    }
  }
  return counts
}

function expectCounts(got: CorrectionCounts, want: CorrectionCounts, label: string) {
  expect(got.total, `${label} total_units`).toBe(want.total)
  expect(got.filled, `${label} filled`).toBe(want.filled)
  expect(got.corrected, `${label} corrected`).toBe(want.corrected)
  expect(got.needs_review, `${label} needs_review`).toBe(want.needs_review)
  expect(got.conflict, `${label} conflict`).toBe(want.conflict)
  // unchanged is only shown in draft; template reports omit it (0).
  if (want.unchanged > 0) {
    expect(got.unchanged, `${label} unchanged`).toBe(want.unchanged)
  }
}

for (const c of SAMPLE_CASES) {
  test(`project #${c.id} ${c.title} — correction summary matches the guide`, async ({ page, request }) => {
    const projectId = await instantiateSample(request, c.id)
    try {
      await page.goto('/')
      await selectProject(page, projectId)
      await openTab(page, 'Correction Pipeline')
      // The report loads on open (Draft is the default mode). Wait for the
      // summary row to render.
      await expect(page.locator('.flow-box', { hasText: /intermediate json/i }))
        .toBeVisible()
      await expect(page.locator('.status-tag.conflict').first()).toBeVisible()

      // --- Draft mode ---
      const draft = await readSummary(page)
      expectCounts(draft, c.draft, `#${c.id} draft`)

      // The conflict unit shows "unresolved" in the document view (no value).
      await expect(page.locator('.doc-field', { hasText: /unresolved/i }).first())
        .toBeVisible()

      // Spot-check a key filled/corrected value is actually on the page.
      const probe = c.keyFields.find((f) => f.value)
      if (probe?.value) {
        await expect(page.getByText(probe.value, { exact: false }).first())
          .toBeVisible()
      }

      // --- Template mode ---
      await page.getByRole('button', { name: 'Template mode' }).click()
      await expect(page.locator('.flow-box', { hasText: /intermediate json/i }))
        .toBeVisible()
      // Let the template report load (total flips 17 -> 16).
      await expect
        .poll(async () => (await readSummary(page)).total, { timeout: 15_000 })
        .toBe(c.template.total)
      const tmpl = await readSummary(page)
      expectCounts(tmpl, c.template, `#${c.id} template`)
    } finally {
      await deleteProject(request, projectId)
    }
  })
}
