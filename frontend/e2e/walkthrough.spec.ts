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
  shot,
} from './helpers'

/**
 * JSON-alignment "buttonology" walkthrough — the 8 scenarios the json-batch
 * feature set promises, driven through the REAL Golden Schema + Batch Convert
 * tabs of a throwaway json_alignment project. Each scenario gets its OWN fresh
 * throwaway project (created + schema-seeded in beforeEach, deleted in
 * afterEach) so the shapes/approvals/learned-values one scenario mutates never
 * leak into another — the library and learned vocabulary persist per project,
 * so a shared project would couple scenarios' library state. The golden schema
 * is seeded from the real sample project-7 `game` target_schema.json, so every
 * field + enum used here is the shipped schema (no invented fields) and
 * project 7's own persisted state is never touched.
 *
 * Serial by config (workers:1). Each 0N-*.png screenshot is named by the
 * scenario NUMBER in the task.
 *
 * Every scenario calls shot() explicitly after its asserted state, because the
 * config's screenshot:'only-on-failure' would otherwise skip a passing run.
 */

let projectId = ''

/** The progress status span lives in the Upload & convert header row; it is the
 *  only status node that renders the "N of M files · P%" label. */
function progressStatus(page: Page) {
  return page.locator('[role="status"]', { hasText: /files ·/ })
}

/** The Batch result panel (appears when a batch completes). It is the innermost
 *  status panel whose first child is the "Batch result" heading, so scope to the
 *  panel that carries role="status" to avoid matching the outer convert panel. */
function batchResultPanel(page: Page) {
  return page.locator('.panel[role="status"]', { has: page.getByText('Batch result') })
}

/** Read the pathway -> count rows from the Batch result table. */
async function pathwayCounts(page: Page): Promise<Record<string, number>> {
  const rows = batchResultPanel(page).locator('table.docs tbody tr')
  const out: Record<string, number> = {}
  for (let i = 0; i < (await rows.count()); i++) {
    const cells = rows.nth(i).locator('td')
    const label = (await cells.nth(0).innerText()).trim()
    const n = Number((await cells.nth(1).innerText()).trim())
    out[label] = n
  }
  return out
}

async function openConvert(page: Page) {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Batch Convert')
}

async function openGolden(page: Page) {
  await page.goto('/')
  await selectProject(page, projectId)
  await openTab(page, 'Golden Schema')
}

/** Set the "Learn new shapes" checkbox, upload files, wait for Batch result. */
async function processFiles(page: Page, files: string[], research: boolean) {
  const learn = page.getByRole('checkbox', { name: /Learn new shapes/i })
  if (research !== (await learn.isChecked())) await learn.setChecked(research)
  const panel = page.locator('.panel', { has: page.getByText('Upload & convert') })
  await panel.locator('input[type="file"]').setInputFiles(files)
  await expect(page.getByText('Batch result')).toBeVisible({ timeout: 30_000 })
}

test.beforeEach(async ({ request }, testInfo) => {
  await assertBackendUp(request)
  // A fresh, isolated json_alignment project per scenario, seeded with the real
  // project-7 game schema.
  // Keep the project name free of scenario text so it never collides with
  // on-screen assertions (e.g. the "Accepted values" heading in scenario 8).
  const tag = testInfo.title.slice(0, 2)
  projectId = await createProject(request, `E2E walkthrough ${tag}`, 'json_alignment')
  await seedGoldenSchema(request, projectId, readProject7Schema())
})

test.afterEach(async ({ request }) => {
  if (projectId) await deleteProject(request, projectId)
  projectId = ''
})

// ---------------------------------------------------------------------------
// Scenario 1 — LIVE PROGRESS INDICATOR
// ---------------------------------------------------------------------------
test('01 live progress indicator reaches M of M · 100%', async ({ page }) => {
  await openConvert(page)
  const learn = page.getByRole('checkbox', { name: /Learn new shapes/i })
  if (!(await learn.isChecked())) await learn.setChecked(true)

  // Sample the live progress label while the batch runs. Tiny fixtures may
  // finish inside one 600ms poll tick, so we record whatever frames we can see
  // and ALWAYS assert the terminal/complete state.
  const observed: string[] = []
  const panel = page.locator('.panel', { has: page.getByText('Upload & convert') })

  // Three distinct shapes keeps total >= 2 and gives the worker real work.
  const files = [
    fixture('clean_known.json'),
    fixture('needs_review_platform.json'),
    fixture('cascade_base.json'),
  ]
  await panel.locator('input[type="file"]').setInputFiles(files)

  // Poll the progress label a handful of times right after upload.
  for (let i = 0; i < 40; i++) {
    const txt = await progressStatus(page).innerText().catch(() => '')
    if (txt) observed.push(txt.trim())
    if (await page.getByText('Batch result').isVisible().catch(() => false)) break
    await page.waitForTimeout(50)
  }
  await expect(page.getByText('Batch result')).toBeVisible({ timeout: 30_000 })

  // The progress label — while visible — must take the "N of M files · P%" form,
  // its `done` must be non-decreasing, and the final observed frame (if any) must
  // be the complete "M of M · 100%".
  const labelRe = /(\d+) of (\d+) files · (\d+)%/
  const samples = observed
    .map((s) => labelRe.exec(s))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => ({ done: Number(m[1]), total: Number(m[2]), pct: Number(m[3]) }))

  // eslint-disable-next-line no-console
  console.log(`[01] progress frames observed: ${JSON.stringify(observed)}`)

  if (samples.length > 0) {
    const total = samples[0].total
    expect(total).toBeGreaterThanOrEqual(2)
    let prev = -1
    for (const s of samples) {
      expect(s.total).toBe(total)
      expect(s.done).toBeGreaterThanOrEqual(prev) // monotonic
      expect(s.pct).toBe(Math.round((s.done / s.total) * 100))
      prev = s.done
    }
    const last = samples[samples.length - 1]
    if (last.done === last.total) {
      expect(last.pct).toBe(100) // reached M of M · 100%
    }
  }

  // Terminal state: progress label gone (not busy) and the completed result
  // shows the three files converted/queued across pathways (clusters >= 2).
  await expect(progressStatus(page)).toHaveCount(0)
  await expect(batchResultPanel(page)).toBeVisible()
  const counts = await pathwayCounts(page)
  const totalDocs = Object.values(counts).reduce((a, b) => a + b, 0)
  expect(totalDocs).toBeGreaterThanOrEqual(3)

  await shot(page, '01-progress.png')
})

// ---------------------------------------------------------------------------
// Scenario 2 — BRAND-NEW SHAPE, N == 0, SILENT APPROVE
// Approving a provisional shape with no other affected files applies with NO
// cascade modal. Leaves the clean 8-field "game" shape S APPROVED (scenario 5
// depends on this).
// ---------------------------------------------------------------------------
test('02 brand-new shape with N==0 approves silently (no modal)', async ({ page }) => {
  await openConvert(page)
  await processFiles(page, [fixture('clean_known.json')], true)

  const counts = await pathwayCounts(page)
  expect(counts['New shape — needs approval']).toBeGreaterThanOrEqual(1)

  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()
  // Approve the single provisional shape.
  await page.getByRole('button', { name: /^Approve$/ }).first().click()

  // NO cascade modal must appear (N==0 -> silent apply). Hold the assertion
  // across a short window so a late-rendering dialog would still be caught.
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForTimeout(800)
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // The shape moved to approved: the approval card is gone.
  await expect(page.getByText('Shapes awaiting your approval')).toBeHidden({
    timeout: 10_000,
  })

  await shot(page, '02-silent-approve.png')
})

// ---------------------------------------------------------------------------
// Scenario 5 — 100% FILE THROUGH ALREADY-APPROVED SHAPE
// A new file of the already-approved shape S converts automatically and is NOT
// queued for approval. Depends on scenario 2.
// ---------------------------------------------------------------------------
test('05 new file of an approved shape auto-converts (known shape)', async ({ page }) => {
  await openConvert(page)
  // First learn + approve the clean 8-field game shape S (same shape both files
  // share) so there is an APPROVED profile to replay against.
  await processFiles(page, [fixture('clean_known.json')], true)
  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()
  await page.getByRole('button', { name: /^Approve$/ }).first().click()
  await expect(page.getByText('Shapes awaiting your approval')).toBeHidden({
    timeout: 10_000,
  })

  // Now upload a NEW file of that same shape: it must auto-convert (known shape)
  // and NOT re-enter the approval queue.
  await processFiles(page, [fixture('clean_known_2.json')], true)
  const counts = await pathwayCounts(page)
  expect(counts['Converted (known shape)']).toBeGreaterThanOrEqual(1)
  expect(counts['New shape — needs approval'] ?? 0).toBe(0)
  await expect(page.getByText('Shapes awaiting your approval')).toBeHidden()

  await shot(page, '05-replay-known.png')
})

// ---------------------------------------------------------------------------
// Scenario 6 — NEEDS-REVIEW FILL (remap)
// A file whose `developer` is carried under a foreign key `made_by` lands with
// `developer` flagged needs review; the human remaps it to the real source path
// and the record then conforms (propagation line reports conformant >= 1).
// ---------------------------------------------------------------------------
test('06 needs-review field is filled via the map-to remap', async ({ page }) => {
  await openConvert(page)
  await processFiles(page, [fixture('remap_case.json')], true)

  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()

  // Find the provisional card whose developer row needs review. The remap select
  // id is `remap-<profileId>-developer`.
  const devRow = page.locator('tr', { has: page.getByText('developer', { exact: false }) })
    .filter({ has: page.getByText('needs review') })
  await expect(devRow.first()).toBeVisible()

  const remapSelect = page.locator('select[id^="remap-"][id$="-developer"]').first()
  await expect(remapSelect).toBeVisible()

  // Choose the `made_by` source path (option text contains the path).
  const madeByOption = remapSelect.locator('option', { hasText: 'made_by' }).first()
  const optValue = await madeByOption.getAttribute('value')
  expect(optValue, 'made_by must be offered as a source path').toBeTruthy()
  await remapSelect.selectOption(optValue as string)

  // After the remap, the propagation status line reports records now conforming.
  const propagation = page.locator('[role="status"]', {
    hasText: /record\(s\) in this shape now fully\s+conform/i,
  })
  await expect(propagation).toBeVisible({ timeout: 15_000 })
  const propText = await propagation.innerText()
  // eslint-disable-next-line no-console
  console.log(`[06] propagation: ${propText.replace(/\s+/g, ' ').trim()}`)
  // The leading bold number is the conformant count; it must be >= 1.
  const m = /(\d+)\s+of\s+(\d+)\s+record/i.exec(propText.replace(/\s+/g, ' '))
  expect(m, 'propagation line carries an N of M record count').toBeTruthy()
  expect(Number((m as RegExpExecArray)[1])).toBeGreaterThanOrEqual(1)

  await shot(page, '06-remap.png')
})

// ---------------------------------------------------------------------------
// Scenario 7 — ACCEPT VALUE + UNDO (review pane)
// On a needs-review platform row ("Steam" not-in-enum), accept "Linux" inline;
// assert the server-confirmed success + undo button; undo; assert the removal
// message and that undo is gone. Leaves platform learned values clean.
// ---------------------------------------------------------------------------
test('07 accept a value for platform then undo (server-confirmed)', async ({ page }) => {
  await openConvert(page)
  await processFiles(page, [fixture('needs_review_platform.json')], true)

  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()

  // The inline accept-a-value control for the flagged platform field. Scope the
  // accept button to the SAME control row as the platform accept input.
  const acceptInput = page.locator('input[aria-label="Accept a value for platform"]').first()
  await expect(acceptInput).toBeVisible()
  const acceptRow = page.locator('div.row', { has: acceptInput })
  await acceptInput.fill('Linux')
  await acceptRow.getByRole('button', { name: /^accept$/ }).click()

  // Server-confirmed success: the acceptance message + an undo affordance.
  // The component renders curly quotes: accepted “Linux” for platform.
  await expect(
    page.getByText(/accepted .*Linux.* for platform/),
  ).toBeVisible({ timeout: 15_000 })
  const undoBtn = acceptRow.getByRole('button', { name: /^undo$/ })
  await expect(undoBtn).toBeVisible()

  await shot(page, '07-accept-undo.png')

  // Undo: server-confirmed removal, undo button gone.
  await undoBtn.click()
  await expect(
    page.getByText(/removed .*Linux.* from platform/),
  ).toBeVisible({ timeout: 15_000 })
  await expect(acceptRow.getByRole('button', { name: /^undo$/ })).toHaveCount(0)
})

// ---------------------------------------------------------------------------
// Scenario 3 — APPROVE N>0 -> MODAL -> OK   ***BLOCKED (unreachable via UI)***
// Scenario 4 — APPROVE N>0 -> MODAL -> CANCEL ***BLOCKED (same reason)***
//
// WHAT WE OBSERVED (recorded here and in WALKTHROUGH_RESULTS.md): no sequence of
// real batch uploads can make the backend return cascade_count > 0, so the
// confirmation modal that scenarios 3 & 4 drive never opens through the UI.
//
// WHY (grounded in the backend source we read):
//   * cascade_count (backend/app/json_alignment/cascade.py) counts only OTHER
//     ledger files whose own cluster did NOT produce the approved profile P AND
//     that route to PATHWAY_REPLAY (replay_clean) under research_available=False
//     once P is approved.
//   * The pending ledger is written by op_batch_align (backend/app/jobs/ops.py):
//     each file's cluster_key = its signature hash and produced_cluster_profile =
//     the provisional profile its own cluster produced. Files of the SAME shape
//     as P therefore share P's cluster_key and are EXCLUDED (direct producers).
//   * A file of a DIFFERENT shape has >=1 added/removed path, which
//     assess_drift (backend/app/json_alignment/drift.py) flags as STRUCTURAL
//     drift (`match.added_paths or match.removed_paths`), so decide_pathway
//     routes it to drift_repair, NOT replay_clean — so it is never counted,
//     even at Jaccard 21/22 = 0.95 (HIGH band).
//   * The only state that yields N>0 is a file with P's EXACT path-set but a
//     DIFFERENT cluster_key — which the real batch pipeline never writes (same
//     path-set => same signature hash => same cluster_key). The backend unit
//     test test_cascade.py reaches N>0 only by hand-authoring such a ledger
//     (`_write_ledger` sets cluster_key = signature + "_held"); there is no UI
//     or API affordance to author that state.
//
// EVIDENCE: diag probes via the real /approve?preview endpoint returned
// cascade_count: 0 for (a) three identical-shape files blocked by platform
// 'Linux' then accepted, and (b) a learn-then-second-batch of the same shape.
// Both the engineered fixtures below and those probes are reproduced live here
// through the preview API so the 0 is captured in-run, a screenshot of the
// provisional (no-modal) state is saved, then the scenario is marked skipped
// with the blocker reason (NOT passed, NOT a fake green).
//
// To make these two scenarios runnable would require changing application
// source (e.g. relaxing the structural-drift gate in drift.py so a superset
// shape replays, or having op_batch_align record cross-shape ledger entries) —
// which this task forbids. Flagged to the caller via send_message.
// ---------------------------------------------------------------------------

/** Live-probe the real cascade preview for the engineered state, so the
 *  observed cascade_count is captured in-run as evidence. Returns the count. */
async function probeCascadeCount(
  request: import('@playwright/test').APIRequestContext,
  pid: string, baseDocId: string,
): Promise<{ count: number; baseProfile: string }> {
  const libRes = await request.get(`/api/projects/${pid}/alignment/library`)
  const lib = await libRes.json() as {
    entries: { profile_id: string; source_docs?: string[] }[]
  }
  const baseEntry = lib.entries.find((e) => (e.source_docs ?? []).includes(baseDocId))
  const baseProfile = baseEntry?.profile_id ?? ''
  const pvRes = await request.post(`/api/projects/${pid}/alignment/approve`, {
    data: { profile_id: baseProfile, preview: true },
  })
  const pv = await pvRes.json() as { cascade_count?: number }
  return { count: pv.cascade_count ?? -1, baseProfile }
}

test('03 approve with N>0 shows cascade modal, OK applies it', async ({ page, request }, testInfo) => {
  await openConvert(page)
  await processFiles(page, [
    fixture('cascade_base.json'),
    fixture('cascade_candidate_1.json'),
    fixture('cascade_candidate_2.json'),
  ], true)
  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()

  // Capture the provisional (no cascade modal) state as evidence.
  await shot(page, '03-cascade-ok.png')

  // Probe the real backend cascade preview for the engineered state.
  const { count, baseProfile } = await probeCascadeCount(request, projectId, 'cascade_base.json')
  // eslint-disable-next-line no-console
  console.log(`[03] engineered state cascade_count=${count} for base=${baseProfile}`)
  testInfo.annotations.push({
    type: 'blocked',
    description:
      `Cross-file cascade N>0 is unreachable through real batch uploads: the ` +
      `backend returned cascade_count=${count} for the engineered base profile ` +
      `${baseProfile}. Same-shape files share the approved profile's cluster_key ` +
      `(excluded as producers); different-shape files trigger structural drift ` +
      `(drift.py) so they route drift_repair, not replay_clean, and are not ` +
      `counted. The confirmation modal therefore never opens via the UI. See ` +
      `WALKTHROUGH_RESULTS.md.`,
  })
  // Document the observed value, then mark BLOCKED (not pass, not red-fail).
  expect(count, 'observed real cascade_count for the engineered state').toBe(0)
  test.skip(true,
    'BLOCKED: cascade N>0 unreachable via UI/API (structural-drift gate + ' +
    'cluster_key exclusion); modal never opens. Observed cascade_count=0.')
})

test('04 approve with N>0 cascade modal, Cancel changes nothing', async ({ page, request }, testInfo) => {
  await openConvert(page)
  await processFiles(page, [
    fixture('cascade4_base.json'),
    fixture('cascade4_candidate_1.json'),
    fixture('cascade4_candidate_2.json'),
  ], true)
  await expect(page.getByText('Shapes awaiting your approval')).toBeVisible()

  await shot(page, '04-cascade-cancel.png')

  const { count, baseProfile } = await probeCascadeCount(request, projectId, 'cascade4_base.json')
  // eslint-disable-next-line no-console
  console.log(`[04] engineered state cascade_count=${count} for base=${baseProfile}`)
  testInfo.annotations.push({
    type: 'blocked',
    description:
      `Same blocker as scenario 3: backend cascade_count=${count} for the ` +
      `engineered base profile ${baseProfile}; the cascade confirmation modal ` +
      `never opens via real batch uploads, so the Cancel path cannot be driven. ` +
      `See WALKTHROUGH_RESULTS.md.`,
  })
  expect(count, 'observed real cascade_count for the engineered state').toBe(0)
  test.skip(true,
    'BLOCKED: cascade N>0 unreachable via UI/API; modal never opens. ' +
    'Observed cascade_count=0.')
})

// ---------------------------------------------------------------------------
// Scenario 8 — GOLDEN SCHEMA CRUD (AcceptedValuesEditor)
// On the Golden Schema tab, add 'Steam' and 'Linux' as accepted values for
// platform; assert they appear as LEARNED chips (distinct from read-only
// BUILT-IN taxonomy chips); remove 'Steam'; assert it disappears from learned
// while the built-in taxonomy is untouched and 'Linux' remains learned.
// Runs LAST because it mutates the project's learned platform vocabulary.
// ---------------------------------------------------------------------------
test('08 golden schema CRUD: add/remove learned accepted values', async ({ page }) => {
  await openGolden(page)

  // The Accepted values editor renders once a schema is saved (it is, seeded).
  // Match the panel heading <strong> exactly (the project name option also
  // contains the words "accepted values", so a substring match is ambiguous).
  await expect(
    page.locator('strong').filter({ hasText: /^Accepted values$/ }),
  ).toBeVisible({ timeout: 15_000 })

  // The platform row: built-in chips are the schema taxonomy; add learned values.
  const platformRow = page.locator('tr', {
    has: page.locator('td.mono', { hasText: /^platform$/ }),
  }).first()
  await expect(platformRow).toBeVisible()

  const addInput = page.locator('input[aria-label="Accept a value for platform"]')
  const addBtn = platformRow.getByRole('button', { name: '+' })

  // Add Steam.
  await addInput.fill('Steam')
  await addBtn.click()
  await expect(platformRow.getByRole('button', { name: 'Remove Steam' })).toBeVisible({
    timeout: 15_000,
  })
  // Add Linux.
  await addInput.fill('Linux')
  await addBtn.click()
  await expect(platformRow.getByRole('button', { name: 'Remove Linux' })).toBeVisible({
    timeout: 15_000,
  })

  // Built-in taxonomy chips remain present (read-only, .chip.muted) and intact.
  for (const builtin of ['Windows PC', 'Xbox', 'PlayStation', 'Nintendo Switch']) {
    await expect(
      platformRow.locator('.chip.muted', { hasText: new RegExp(`^${builtin}$`) }),
    ).toBeVisible()
  }

  await shot(page, '08-crud.png')

  // Remove Steam -> it leaves the learned list; Linux stays; built-ins untouched.
  await platformRow.getByRole('button', { name: 'Remove Steam' }).click()
  await expect(platformRow.getByRole('button', { name: 'Remove Steam' })).toHaveCount(0, {
    timeout: 15_000,
  })
  await expect(platformRow.getByRole('button', { name: 'Remove Linux' })).toBeVisible()
  for (const builtin of ['Windows PC', 'Xbox', 'PlayStation', 'Nintendo Switch']) {
    await expect(
      platformRow.locator('.chip.muted', { hasText: new RegExp(`^${builtin}$`) }),
    ).toBeVisible()
  }
})
