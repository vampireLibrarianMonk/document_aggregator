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
// Shape approval CARD CONTENTS (not just "a card appeared + Approve works").
//
// The batch/walkthrough specs already cover that an approval card shows up and
// the Approve button advances the pathway. This spec covers what is ON the
// card, which nothing else asserts in a real browser:
//   1. the field-mapping table (Golden field <- Source path rows), and
//   2. the "unmapped source fields" listbox — the collapsible <details> that
//      distinguishes two shapes whose mapped tables look identical.
//
// cascade_base.json is the ideal input: it carries the 8 standard game fields
// (which map) PLUS 13 extra fields (extra_0..extra_12) that map to no golden
// field, so the unmapped listbox is populated and — being > 6 — starts
// collapsed by design.
// ---------------------------------------------------------------------------

let projectId = ''

test.beforeEach(async ({ request }) => {
  await assertBackendUp(request)
  projectId = await createProject(request, 'E2E shape card', 'json_alignment')
  await seedGoldenSchema(request, projectId, readProject7Schema())
})

test.afterEach(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
  projectId = ''
})

async function openConvertAndLearn(page: Page, files: string[]) {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Batch Convert')
  const learn = page.getByRole('checkbox', { name: /Learn new shapes/i })
  if (!(await learn.isChecked())) await learn.setChecked(true)
  const panel = page.locator('.panel', { has: page.getByText('Upload & convert') })
  await panel.locator('input[type="file"]').setInputFiles(files)
  await expect(page.getByText('Batch result')).toBeVisible({ timeout: 30_000 })
  // The provisional approval card for the learned shape.
  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()
}

/** The single provisional approval card (the panel holding the mapping table). */
function card(page: Page) {
  return page.locator('.panel', { has: page.getByText('Shapes awaiting your approval') })
}

test('card: shows the field-mapping table (Golden field <- Source path)', async ({ page }) => {
  await openConvertAndLearn(page, [fixture('cascade_base.json')])

  // The "Field mapping · N mapped" summary and the table header are present.
  await expect(card(page).getByText(/Field mapping · \d+ mapped/)).toBeVisible()
  // Scope to the MAPPING table specifically (the one whose header row is
  // "Golden field / Source path") — the card also contains other tables.
  const table = card(page)
    .locator('table.docs', { has: page.getByRole('columnheader', { name: 'Golden field' }) })
    .first()
  await expect(table.locator('thead')).toContainText('Golden field')
  await expect(table.locator('thead')).toContainText('Source path')

  // A representative mapped row: the golden `name` maps from source `name`.
  const nameRow = table.locator('tbody tr', { has: page.getByText('name', { exact: true }) }).first()
  await expect(nameRow).toBeVisible()
})

test('card: unmapped source fields render as a collapsible listbox', async ({ page }) => {
  await openConvertAndLearn(page, [fixture('cascade_base.json')])

  // The <details> summary names the count. cascade_base has 13 extra fields,
  // so the summary says "13 source fields not mapped to any golden field".
  const details = card(page).locator('details', {
    has: page.getByText(/source field.*not mapped to any golden field/i),
  })
  await expect(details).toBeVisible()

  const summary = details.locator('summary')
  await expect(summary).toContainText(/\d+ source fields not mapped to any golden field/i)

  // > 6 unmapped -> starts collapsed by design.
  await expect(details).not.toHaveAttribute('open', /.*/)

  // Expanding reveals the extra field names in the scrollable list.
  await summary.click()
  await expect(details).toHaveAttribute('open', /.*/)
  await expect(details.getByText('extra_0', { exact: true })).toBeVisible()
  await expect(details.getByText('extra_12', { exact: true })).toBeVisible()

  // The explanatory sentence is present (separate from the list).
  await expect(details).toContainText(/different shape with its own approval/i)
})

test('card: a clean shape (all fields map) shows no unmapped-fields note', async ({ page }) => {
  // clean_known.json maps all 8 of its fields -> no "unmapped" listbox at all.
  await openConvertAndLearn(page, [fixture('clean_known.json')])
  await expect(card(page).getByText(/Field mapping · \d+ mapped/)).toBeVisible()
  await expect(
    card(page).getByText(/source field.*not mapped to any golden field/i),
  ).toHaveCount(0)
})
