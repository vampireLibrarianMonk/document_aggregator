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

export const flows = [
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
    id: 'ingestion-default',
    title: 'Ingestion — document board',
    needsProject: true,
    async setup(page) {
      await openTab(page, 'Ingestion')
    },
  },
  {
    id: 'ingestion-canonical',
    title: 'Ingestion — canonical inspector open',
    needsProject: true,
    async setup(page) {
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
    id: 'supplementals-default',
    title: 'Supplementals — form + list',
    needsProject: true,
    async setup(page) {
      await openTab(page, 'Supplementals')
    },
  },
  {
    id: 'search-results',
    title: 'Search — results rendered',
    needsProject: true,
    async setup(page) {
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
      await openTab(page, 'Report & Export')
    },
  },
]
