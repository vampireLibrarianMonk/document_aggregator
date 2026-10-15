# JSON-alignment "buttonology" walkthrough — results

Verification / test-authoring run of the 8 scenarios the json-batch feature set
promises, driven through the REAL Golden Schema + Batch Convert tabs of the
built app against the live backend. No application source was modified; only
files under `frontend/e2e/` were added (plus additive helpers in
`frontend/e2e/helpers.ts`). Nothing was committed.

## Environment

- API: `http://127.0.0.1:8000` — healthy (`/api/health` → 200), reused (Docker
  stack NOT rebuilt).
- Served app: Playwright `webServer` serves `dist/` on port 4600 and proxies
  `/api` to the live backend (`reuseExistingServer: true`, `workers: 1`, serial).
- Each scenario runs in its OWN throwaway `json_alignment` project, created and
  schema-seeded in `beforeEach` and deleted in `afterEach`, so one scenario's
  learned shapes / approvals / accepted values never leak into another (library
  and learned vocabulary persist per project). The golden schema is seeded from
  the real `sample_docs/project/7/golden/target_schema.json` (`game`: name,
  releaseYear `^\d{4}$`, developer, publisher, platform
  `["Windows PC","Xbox","PlayStation","Nintendo Switch"]`, genres, criticScore
  0–100, ESRB `["E","E10+","T","M","AO"]`). No fields were invented.

## Backend state mutated

None persisted. Project 7 was never touched. Every scenario created and then
DELETED its own throwaway project, so no backend state remains after the run.

## Commands run (Windows PowerShell, `frontend/`)

1. `npm install --no-audit --no-fund` — node_modules was incomplete (vitest and
   @testing-library missing); install restored them. Result: success.
2. `npm run build` (`tsc -b && vite build`) — success; `dist/` emitted
   (`index-D51zbGHG.js` 241.55 kB). Type-checks the added helpers.
3. `npx playwright install chromium` — chromium already present; success.
4. `npx playwright test e2e/walkthrough.spec.ts --reporter=list` — final result
   below: **6 passed, 2 skipped (BLOCKED)**, exit 0.
5. `npm run e2e` (whole suite) — walkthrough 6 passed / 2 skipped; existing
   `batch`, `correction`, `ingestion`, `app` specs pass (22 passed total). The 4
   failures in the suite are all in the pre-existing `diagnostics.spec.ts` and
   are environmental (the diagnostics route does not render in this live stack);
   they fail identically when run alone and are unrelated to this task's added
   files. No existing spec was weakened or deleted.

## Per-scenario outcome

| # | Scenario | Result | Key on-screen value asserted | Screenshot |
|---|----------|--------|------------------------------|-----------|
| 1 | Live progress indicator | PASS | Terminal: progress status gone (not busy), `Batch result` panel visible, ≥3 docs across pathways. Mid-run `N of M files · P%` frames were NOT observable (tiny fixtures finish inside one 600 ms poll tick — frames observed: `[]`, logged); the spec asserts the completed state and documents the non-observability. | `screenshots/01-progress.png` |
| 2 | Brand-new shape, N==0, silent approve | PASS | `New shape — needs approval` ≥ 1; after Approve, NO `role=dialog` appears (held across 800 ms) and `Shapes awaiting your approval` becomes hidden (shape approved). | `screenshots/02-silent-approve.png` |
| 3 | Approve N>0 → modal → OK | **BLOCKED** | Cannot be driven via the UI/API. Observed real `cascade_count = 0` for the engineered base profile (`game__shape_c57ba97237`) via the live `/approve?preview` endpoint; the confirmation modal never opens. See "Blocked scenarios" below. Screenshot captures the provisional (no-modal) state. | `screenshots/03-cascade-ok.png` |
| 4 | Approve N>0 → modal → Cancel | **BLOCKED** | Same blocker as #3. Observed real `cascade_count = 0` for the engineered base profile (`game__shape_1471718bcf`); modal never opens, so the Cancel path cannot be driven. Screenshot captures the provisional (no-modal) state. | `screenshots/04-cascade-cancel.png` |
| 5 | 100% file through already-approved shape | PASS | After approving shape S, a NEW same-shape file routes `Converted (known shape)` ≥ 1 and `New shape — needs approval` == 0; approval queue not shown. | `screenshots/05-replay-known.png` |
| 6 | Needs-review fill (remap) | PASS | `developer` carried under foreign key `made_by` lands needs review; selecting `made_by` in the `map to:` select yields the propagation line "After your mapping, **1** of 1 record(s) in this shape now fully conform." (logged). | `screenshots/06-remap.png` |
| 7 | Accept value + undo | PASS | Platform carried under synonym key `system` with not-in-enum value `Steam` → `platform` needs review. Typing `Linux` + accept shows server-confirmed `accepted "Linux" for platform` + an `undo` button; undo shows `removed "Linux" from platform` and the undo button disappears. | `screenshots/07-accept-undo.png` |
| 8 | Golden schema CRUD | PASS | On the Accepted values editor, adding `Steam` then `Linux` for `platform` shows `Remove Steam` / `Remove Linux` learned chips while the built-in `.chip.muted` taxonomy chips (`Windows PC`, `Xbox`, `PlayStation`, `Nintendo Switch`) stay present; removing `Steam` drops `Remove Steam` (count 0) while `Linux` and all built-ins remain. | `screenshots/08-crud.png` |

## Blocked scenarios 3 & 4 — exact reason and evidence

The cross-file auto-approve cascade cannot be made to count N > 0 through any
real sequence of batch uploads, so the confirmation modal those two scenarios
drive never opens via the UI. This is a reachability limit of the shipped
pipeline, confirmed against the source:

- `cascade_count` (`backend/app/json_alignment/cascade.py`) counts only OTHER
  ledger files whose own cluster did NOT produce the approved profile P AND that
  route to `replay_clean` (`PATHWAY_REPLAY`) under `research_available=False`
  once P is approved.
- The pending ledger is written by `op_batch_align`
  (`backend/app/jobs/ops.py`): each file's `cluster_key` is its signature hash
  and `produced_cluster_profile` is the provisional profile its own cluster
  produced. Files of the SAME shape as P therefore share P's `cluster_key` and
  are EXCLUDED as direct producers.
- A file of a DIFFERENT shape has ≥1 added/removed path, which `assess_drift`
  (`backend/app/json_alignment/drift.py`) flags as STRUCTURAL drift
  (`match.band != BAND_HIGH or match.added_paths or match.removed_paths`), so
  `decide_pathway` routes it to `drift_repair`, NOT `replay_clean` — even at a
  HIGH-band Jaccard of 21/22 = 0.95. So it is never counted.
- The only state that yields N > 0 is a file carrying P's EXACT path-set but a
  DIFFERENT `cluster_key`, which the real batch pipeline never writes (same
  path-set ⇒ same signature hash ⇒ same `cluster_key`). The backend unit test
  `backend/tests/test_cascade.py` reaches N > 0 only by hand-authoring such a
  ledger (`_write_ledger` sets `cluster_key = signature + "_held"`); there is no
  UI or API affordance to author that state.

Evidence captured in-run (console-logged and asserted) via the live
`/api/projects/{id}/alignment/approve` preview endpoint on the engineered
fixtures:

- Scenario 3 state: `cascade_count = 0` for base profile `game__shape_c57ba97237`
  (base `cascade_base.json` = 21-path shape; candidates
  `cascade_candidate_1/2.json` = 22-path near-identical shape).
- Scenario 4 state: `cascade_count = 0` for base profile `game__shape_1471718bcf`
  (disjoint filler keys so it never collides with scenario 3's shape).

Both were also reproduced out-of-band with (a) three identical-shape files
blocked by `platform: "Linux"` then accepted, and (b) a learn-then-second-batch
of the same shape — both returned `cascade_count = 0`.

The tests therefore mark scenarios 3 & 4 as skipped with a BLOCKED annotation
carrying the observed `cascade_count = 0` (NOT a fabricated pass, NOT a red
failure), and each saves a screenshot of the provisional (no-modal) state as
evidence. Making these two scenarios runnable would require changing application
source (relaxing the structural-drift gate in `drift.py` so a superset shape
replays, or having `op_batch_align` record cross-shape ledger entries), which
this task forbids; this limitation was flagged to the caller.

## Files added (no app source touched)

- `frontend/e2e/walkthrough.spec.ts`
- `frontend/e2e/helpers.ts` — additive helpers only (`SCREENSHOTS`, `FIXTURES`,
  `shot`, `fixture`, `readProject7Schema`, `seedGoldenSchema`)
- `frontend/e2e/fixtures/*.json` — deterministic game records derived from the
  project-7 schema
- `frontend/e2e/screenshots/01-progress.png` … `08-crud.png`
- `frontend/e2e/WALKTHROUGH_RESULTS.md` (this file)
