# End-to-end UI suite (Playwright)

This suite drives the **built** app through a real browser against a **live
backend**, asserting the on-screen values the project guides promise. It is the
UI counterpart to the backend's `pytest` suites and the a11y audit: where
`verify_project.py` proves the engine's numbers at the API level, this proves a
user can actually reach and read those numbers in the UI.

It is an **on-demand gate**, deliberately *not* wired into pre-commit or CI — it
needs Docker and a browser binary, which the hooks avoid. Run it when you touch
the frontend, the project-scoped routes, or anything the guides quote.

## What it covers

| Spec | Flows | Asserts |
|---|---|---|
| `app.spec.ts` | 3 | empty start lands on New Project; tab-gating enables tabs only when prerequisites are met; the project selector scopes every tab; the Samples tab appears when `SAMPLES_ENABLED` |
| `correction.spec.ts` | 6 | every bundled sample project via **Samples → Use this sample**: draft-mode summary counts + key field values/statuses (incl. a `conflict` field showing *unresolved* with no value), then flipped to template mode with the template counts — matching the six guide tables |
| `ingestion.spec.ts` | 2 | create an empty project → upload the four pathways through the real file input (corpus ×2, template, first draft, corrections) → every row reaches `completed` → readiness flips incomplete→ready (corpus 2 / template 1 / corrections 1 / first_draft 1) → the Correction/Report tabs unlock |
| `batch.spec.ts` | 5 | the JSON→golden **Batch conversion** section: set a golden schema, tune the relevance dial, upload JSON, process — exercising all five pathways (novel research → approve → replay-clean, drift → drift-repair, unrelated → reject) |
| `diagnostics.spec.ts` | 4 | `/diagnostics` renders every service row with a status, the footer is present, **Refresh** re-fetches, and the Bedrock row reports enabled/reachable/in-use honestly |

The numbers each spec asserts live in one place: `expected.ts` (the centralized
guide values). `helpers.ts` holds the shared actions (sample instantiation,
project create/delete, tab navigation, backend-reachability precheck). Each spec
creates and then deletes its own projects, so a clean run leaves **zero**
leftover projects.

## Prerequisites

1. **The Docker stack is up** with samples (and diagnostics) enabled:

   ```powershell
   # from the repo root
   $env:SAMPLES_ENABLED = "true"
   $env:DIAGNOSTICS_ENABLED = "true"
   docker compose up
   ```

   The suite talks to the `api` service on `http://localhost:8000` through the
   proxy. The global setup does a reachability precheck and fails fast with a
   clear message if the backend is down.

2. **The app is built** (the suite serves `dist/`, not the Vite dev server):

   ```powershell
   cd frontend
   npm run build
   ```

3. **Chromium is installed** for Playwright (one-time, needs network — fine on a
   dev box, skip on the air-gapped target):

   ```powershell
   npx playwright install chromium
   ```

## Running it

```powershell
cd frontend
npm run e2e        # headless, list + JSON reporter (e2e/e2e-report.json, gitignored)
npm run e2e:ui     # the Playwright UI runner, for debugging a single flow
```

The suite runs **serially** (`workers: 1`, `fullyParallel: false`) because every
spec mutates shared backend state (projects). A `node a11y/serve.mjs <port>`
instance is started automatically on port 4600 to serve the built app and proxy
`/api` → the backend; it reuses an already-running server if one is up.

Override the backend target or port with `E2E_API_TARGET` /
`E2E_PORT` if your stack is elsewhere.

## How it's wired

The static-server-with-`/api`-proxy is shared with the a11y audit:
`frontend/a11y/serve.mjs` exports `createAppServer()` and also runs as a CLI
(`node a11y/serve.mjs <port>`), which is exactly what `playwright.config.ts`
launches as its `webServer`. One server implementation, two consumers (E2E +
a11y), so the app is exercised identically by both.

## Findings this suite caught

Driving the real UI (not just the API) surfaced three genuine app bugs, each
fixed and committed as part of standing the suite up:

1. **Sample projects could never open the correction pipeline in the UI.** The
   Correction/Report tabs were gated solely on `readiness.ready`, which only
   flips once Ingestion documents are uploaded. Sample/correction projects are
   fixtures with zero uploads, so their tabs stayed disabled forever. Fixed:
   `useProject` now also fetches the project components and exposes
   `hasCorrectionData`; `App.tsx` gates on `hasCorrectionData || inputsReady`.

2. **The Ingestion upload picker greyed out the sample files.** The upload
   `accept` list omitted `.json`, but the sample template / first-draft /
   corrections are all JSON — so the picker refused to select them even though
   the API accepted them. Fixed: added `.json` to the accepted extensions.

3. **Diagnostics showed a disabled Bedrock integration as green.** The Bedrock
   row reported `ok` whenever the endpoint was *reachable*, even with
   `BEDROCK_ENABLED=false` (credentials are mounted on the dev box). Fixed: the
   state is `ok` only when the integration is both **enabled and available**,
   otherwise `offline`; the panel now shows explicit enabled / reachable / in-use
   rows.

## Relationship to the other gates

- **a11y audit** (`npm run audit:a11y`) — same served app, checks WCAG + keyboard
  reachability/focus across the UI states; see `frontend/a11y/ACCESSIBILITY.md`.
- **Backend tests** (`backend/tests/`, `verify_project.py`) — prove the engine's
  numbers at the API level; this suite proves they survive the trip to the
  screen.

All three are on-demand. None run in pre-commit/CI.
