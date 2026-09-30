# Accessibility audit toolkit

A small, **transferable** accessibility gate for any Vite/React (or other SPA)
frontend. It runs [axe-core](https://github.com/dequelabs/axe-core) for the
machine-checkable WCAG rules **and** adds keyboard/focus checks that axe cannot
perform, driving the app through real user flows in headless Chromium.

It is built to match this project's operating requirements:

- **Air-gap friendly** — serves the built app on loopback and drives a local
  headless browser. No external network calls at audit time. The only online
  step is the one-time `npx playwright install chromium` (mirror the browser
  build into the enclave; see [Air-gapped install](#air-gapped-install)).
- **No silent defaults** — the run prints exactly what was and was not covered.
  If the backend isn't reachable, it says so and tells you the data-dependent
  coverage was skipped, rather than quietly passing.
- **Reproducible** — deterministic checks, a committed `flows.mjs` describing
  every audited state, and a machine-readable `a11y-report.json` artifact.

> **Scope honesty (read this).** Automated tooling covers roughly a third to a
> half of the WCAG 2.1 success criteria. This toolkit extends axe with keyboard
> reachability, focus-visibility, tab-order-trap, and roving-focus checks — but
> it is **not** a conformance sign-off. A human still has to test with a real
> screen reader (NVDA / JAWS / VoiceOver), judge reading/focus order, and verify
> that labels and messages are *meaningful*, not just present. See
> [What this does NOT check](#what-this-does-not-check).

---

## Quick start

```bash
# from frontend/
npm install                     # dev deps incl. playwright + @axe-core/playwright
npx playwright install chromium # one-time browser download (see air-gap note)
npm run build                   # the audit runs against dist/

# optional but recommended: start + seed the backend so data views render
#   (in repo root)  python backend/seed.py
#   (in backend/)    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

npm run audit:a11y              # exits non-zero if any gate fails
```

Output: a per-flow console summary plus `a11y/a11y-report.json`.

---

## What it checks

| Check | Tool | WCAG (examples) | Why axe alone isn't enough |
|-------|------|-----------------|----------------------------|
| Contrast, names, roles, ARIA validity, landmarks, scrollable regions | axe-core | 1.4.3, 4.1.2, 2.4.1, 2.1.1 | — (axe's strength) |
| Every interactive control is programmatically focusable (not `inert`/`aria-hidden`/removed) | custom | 2.1.1 | axe checks markup, not live focusability across flows |
| No positive `tabindex` (breaks natural tab order) | custom | 2.4.3 | — |
| Stylesheet guarantees a `:focus-visible` ring | custom | 2.4.7 | axe doesn't assert a visible focus indicator |
| Tablist supports Arrow-key roving focus | custom (real keys) | 4.1.2, APG | axe can't drive the keyboard |
| All of the above across **9 interactive states**, not just default tabs | flows.mjs | — | conditional UI (sub-views, forms, results) is invisible to a single-snapshot scan |

### The audited states (this project)

Declared in [`flows.mjs`](./flows.mjs). Each drives the app into a DOM condition,
then the runner audits whatever is on screen:

1. Correction Pipeline — default reconciled report
2. Correction Pipeline — a flow-box selected (raw component JSON view)
3. Correction Pipeline — Rounds (convergence view)
4. Ingestion — document board
5. Ingestion — canonical inspector open
6. Supplementals — form + list
7. Search — results rendered
8. Search — no-results state
9. Report & Export — assembled report

This "cover down" on interactive states is the point: the first version of this
audit only scanned each tab's default render and reported clean — while missing
non-focusable scrollable JSON panes and a tab-order bug that only appeared in
specific states.

---

## How it's structured (and how to transfer it)

Three files. Only the first two are app-specific.

```
a11y/
  a11y.config.mjs   # project values: dist dir, port, backend, WCAG tags, gates
  flows.mjs         # the states to audit + how to reach them  ← EDIT PER APP
  run.mjs           # generic runner (axe + keyboard/focus)     ← keep as-is
  ACCESSIBILITY.md  # this file
  a11y-report.json  # generated artifact (git-ignore if you prefer)
```

**To move this to another project:**

1. Copy the `a11y/` folder into that project's frontend.
2. `npm i -D playwright @axe-core/playwright` and add
   `"audit:a11y": "node a11y/run.mjs"` to `package.json` scripts.
3. Edit `a11y.config.mjs` — `distDir`, `port`, `apiTarget`, `axeTags`.
4. Rewrite `flows.mjs` — one entry per meaningful interactive state in that app.
   Use `getByRole` / `getByPlaceholder` selectors so flows survive restyling.
5. `npm run build && npm run audit:a11y`.

`run.mjs` does not need edits; it reads config + flows.

---

## The requirement-driven checklist (self-check)

The gate enforces these automatically (config `failOn`); the rest are the
human's responsibility. Use this list per feature/PR.

**Automated (enforced by `npm run audit:a11y`):**

- [ ] 0 axe violations across every declared flow state
- [ ] every visible, enabled control is focusable (no traps)
- [ ] no positive `tabindex`
- [ ] a `:focus-visible` ring is defined in CSS
- [ ] the tablist (if any) roves with Arrow keys

**Manual (still required for sign-off):**

- [ ] Tab through each screen on a real OS browser — focus order is logical and
      never lost
- [ ] operate every control with keyboard only (Enter/Space/Escape/Arrows)
- [ ] screen-reader pass (NVDA or VoiceOver): names, roles, and live-region
      announcements make sense
- [ ] text at 200% zoom and 320px width remains usable (reflow, WCAG 1.4.10)
- [ ] `prefers-reduced-motion` respected for any animation
- [ ] error messages identify the field and how to fix it (WCAG 3.3.1/3.3.3)

---

## Air-gapped install

The browser binary is the only piece that needs the internet once. Two options:

- **Mirror the Playwright browser build** into the enclave and set
  `PLAYWRIGHT_BROWSERS_PATH` / `PLAYWRIGHT_DOWNLOAD_HOST` to the internal mirror,
  then `npx playwright install chromium`.
- **Use an enclave-approved system Chromium** and point Playwright at it via
  `channel`/`executablePath` in `run.mjs`'s `chromium.launch(...)`.

Everything else (axe-core, the runner, the static server) is pure JS with no
runtime network access.

---

## What this does NOT check

Automated a11y tooling cannot judge:

- whether the **reading and focus order** matches the visual/logical order;
- whether **alt text / labels are meaningful** (only that they exist);
- **screen-reader output quality** and live-region timing in a real AT;
- **cognitive** load, plain-language, and whether an interaction is *discoverable*;
- context-dependent contrast (images, gradients, overlapping text).

Treat a green run as "no known programmatic defects" — the necessary floor
before manual review, not the ceiling.

---

## Interpreting `a11y-report.json`

```jsonc
{
  "generatedAt": "…", "backendUp": true,
  "flows": [
    {
      "id": "ingestion-canonical",
      "title": "Ingestion — canonical inspector open",
      "axe": { "passes": 23, "violations": [ /* id, impact, helpUrl, nodes */ ] },
      "keyboard": {
        "interactiveControls": 28, "reached": 28,
        "unreachable": [], "missingFocusRing": [],
        "tablistRoving": { "applicable": true, "ok": true }
      }
    }
  ]
}
```

Feed this to CI to trend violations over time, or diff it between branches.
