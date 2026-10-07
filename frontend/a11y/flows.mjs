// Declarative audit flows — the ONLY app-specific file in the a11y toolkit.
//
// To transfer this audit to another project you edit THIS file (and the a few
// values in a11y.config.mjs). The runner (run.mjs) is generic.
//
// Each "state" is a named DOM condition to audit. `setup(page)` drives the app
// into that condition (clicks, typing, toggles); the runner then runs axe +
// the keyboard/focus assertions against whatever is on screen. This is how we
// "cover down": every interactive sub-view gets its own audited state, not just
// the default render of each tab.
//
// Playwright `page` API: https://playwright.dev/docs/api/class-page

/** @typedef {import('playwright').Page} Page */

/** Small helper: click an element by role+name if present. */
async function clickRole(page, role, name) {
  const el = page.getByRole(role, { name })
  if (await el.count()) {
    await el.first().click().catch(() => {})
    await page.waitForTimeout(500)
    return true
  }
  return false
}

/** Click a tab by its accessible name. */
async function openTab(page, name) {
  return clickRole(page, 'tab', name)
}

/** The app starts EMPTY. Flows that need a live project generate one with the
 *  offline deterministic generator on the New Project tab (no dependency on the
 *  opt-in Samples feature). Creates + selects a real project. */
async function ensureProject(page) {
  await openTab(page, 'New Project')
  await page.waitForTimeout(300)
  // Structured brief is the default mode; a domain is all the offline generator
  // needs. Fill it and click "Generate & save".
  const domain = page.getByPlaceholder(/domain/i)
  if (await domain.count()) {
    await domain.first().fill('telemetry gateway incident response').catch(() => {})
  }
  const gen = page.getByRole('button', { name: 'Generate & save' })
  if (await gen.count()) {
    await gen.first().click().catch(() => {})
    // Deterministic generation + persist; give it a moment to land + select.
    await page.waitForTimeout(2500)
  }
}

export const flows = [
  {
    id: 'new-project',
    title: 'New Project — project generator (first-run state)',
    async setup(page) {
      // The empty app auto-lands here; open it explicitly for determinism.
      await openTab(page, 'New Project')
      await page.waitForTimeout(400)
    },
  },
  {
    id: 'correction-default',
    title: 'Correction Pipeline — default (single pass, reconciled report)',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
    },
  },
  {
    id: 'correction-flowbox-raw',
    title: 'Correction Pipeline — a flow-box selected (raw component JSON view)',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
      // Select the first non-default component box to flip to the raw JSON view.
      const boxes = page.locator('.flow-box[role="button"]')
      if (await boxes.count()) {
        await boxes.first().click().catch(() => {})
        await page.waitForTimeout(400)
      }
    },
  },
  {
    id: 'correction-rounds',
    title: 'Correction Pipeline — Rounds (convergence view)',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
      await clickRole(page, 'button', 'Rounds')
      await page.waitForTimeout(600)
    },
  },
  {
    id: 'generate-project',
    title: 'Correction Pipeline — Generate project panel (model picker + brief)',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
      await clickRole(page, 'button', '+ New project')
      await page.waitForTimeout(400)
    },
  },
  {
    id: 'governed-live-log',
    title: 'Correction Pipeline — Governed run live progress log',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
      await clickRole(page, 'button', '+ New project')
      await page.waitForTimeout(300)
      // Provide a domain so the run can start, then trigger the governed stream.
      const domain = page.getByPlaceholder(/domain/i)
      if (await domain.count()) {
        await domain.first().fill('avionics interface validation').catch(() => {})
      }
      await clickRole(page, 'button', 'Governed (live log)')
      // Offline run completes fast; wait for the log + summary to render.
      await page.waitForTimeout(1200)
    },
  },
  {
    id: 'generate-from-document',
    title: 'Correction Pipeline — Generate from uploaded document',
    async setup(page) {
      await openTab(page, 'Correction Pipeline')
      await clickRole(page, 'button', '+ New project')
      await page.waitForTimeout(300)
      // Switch to the From-document mode so the file input + help render.
      await clickRole(page, 'button', 'From document')
      await page.waitForTimeout(300)
    },
  },
  {
    id: 'ingestion-default',
    title: 'Ingestion — document board',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Ingestion')
    },
  },
  {
    id: 'ingestion-canonical',
    title: 'Ingestion — canonical inspector open',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Ingestion')
      // Open the canonical inspector for the first document, if any.
      const canonicalBtn = page.getByRole('button', { name: 'Canonical' })
      if (await canonicalBtn.count()) {
        await canonicalBtn.first().click().catch(() => {})
        await page.waitForTimeout(500)
      }
    },
  },
  {
    id: 'ingestion-batch',
    title: 'Ingestion — Batch conversion section (switch on)',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Ingestion')
      // Flip the Batch conversion switch so the golden-schema editor, relevance
      // dial, upload controls, and approval area render for the audit.
      const toggle = page.getByRole('checkbox', { name: 'Enable batch conversion mode' })
      if (await toggle.count()) {
        await toggle.first().check().catch(() => {})
        await page.waitForTimeout(600)
      }
    },
  },
  {
    id: 'supplementals-default',
    title: 'Supplementals — form + list',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Supplementals')
    },
  },
  {
    id: 'search-results',
    title: 'Search — results rendered',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Search')
      const input = page.getByPlaceholder(/search the source documents/i)
      if (await input.count()) {
        await input.first().fill('firmware')
        await input.first().press('Enter')
        await page.waitForTimeout(900)
      }
    },
  },
  {
    id: 'search-empty',
    title: 'Search — no-results state',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Search')
      const input = page.getByPlaceholder(/search the source documents/i)
      if (await input.count()) {
        await input.first().fill('zzz-no-such-term-xyzzy')
        await input.first().press('Enter')
        await page.waitForTimeout(700)
      }
    },
  },
  {
    id: 'report-default',
    title: 'Report & Export — assembled report',
    needsProject: true,
    async setup(page) {
      await ensureProject(page)
      await openTab(page, 'Report & Export')
    },
  },
  {
    id: 'diagnostics',
    title: 'Diagnostics — live service status',
    async setup(page) {
      // Project-independent: audit the service-status page directly.
      await openTab(page, 'Diagnostics')
      await page.waitForTimeout(600)
    },
  },
  {
    id: 'samples',
    title: 'Samples — bundled case picker (when SAMPLES_ENABLED)',
    async setup(page) {
      // Only present when the backend flag is on; the tab is simply absent
      // otherwise and this flow audits whatever renders (no-op if hidden).
      await openTab(page, 'Samples')
      await page.waitForTimeout(500)
    },
  },
]
