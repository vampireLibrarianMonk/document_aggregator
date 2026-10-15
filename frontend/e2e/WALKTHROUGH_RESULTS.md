# JSON-alignment "buttonology" walkthrough — results

Verification / test-authoring run of the scenarios the json-batch feature set
promises, driven through the REAL Golden Schema + Batch Convert tabs of the
built app against the live backend. No application source was modified; only
files under `frontend/e2e/` were added (plus additive helpers in
`frontend/e2e/helpers.ts`). Nothing was committed.

> Note: the files-only N>0 auto-approve cascade feature (the preview/apply
> two-phase approve and its confirmation modal) was later REMOVED as dead code.
> The two scenarios that drove that modal (originally 3 & 4) and their
> cascade-only fixtures/screenshots were deleted. Approval is now a single
> direct action with no confirmation dialog. The remaining scenarios (live
> progress, direct approve, replay of an approved shape, remap, accept/undo,
> golden-schema CRUD) are unchanged.

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

## Per-scenario outcome

| # | Scenario | Result | Key on-screen value asserted | Screenshot |
|---|----------|--------|------------------------------|-----------|
| 1 | Live progress indicator | PASS | Terminal: progress status gone (not busy), `Batch result` panel visible, ≥3 docs across pathways. Mid-run `N of M files · P%` frames were NOT observable (tiny fixtures finish inside one 600 ms poll tick — frames observed: `[]`, logged); the spec asserts the completed state and documents the non-observability. | `screenshots/01-progress.png` |
| 2 | Brand-new shape, direct approve | PASS | `New shape — needs approval` ≥ 1; after Approve, NO `role=dialog` appears (held across 800 ms) and `Shapes awaiting your approval` becomes hidden (shape approved). | `screenshots/02-silent-approve.png` |
| 5 | 100% file through already-approved shape | PASS | After approving shape S, a NEW same-shape file routes `Converted (known shape)` ≥ 1 and `New shape — needs approval` == 0; approval queue not shown. | `screenshots/05-replay-known.png` |
| 6 | Needs-review fill (remap) | PASS | `developer` carried under foreign key `made_by` lands needs review; selecting `made_by` in the `map to:` select yields the propagation line "After your mapping, **1** of 1 record(s) in this shape now fully conform." (logged). | `screenshots/06-remap.png` |
| 7 | Accept value + undo | PASS | Platform carried under synonym key `system` with not-in-enum value `Steam` → `platform` needs review. Typing `Linux` + accept shows server-confirmed `accepted "Linux" for platform` + an `undo` button; undo shows `removed "Linux" from platform` and the undo button disappears. | `screenshots/07-accept-undo.png` |
| 8 | Golden schema CRUD | PASS | On the Accepted values editor, adding `Steam` then `Linux` for `platform` shows `Remove Steam` / `Remove Linux` learned chips while the built-in `.chip.muted` taxonomy chips (`Windows PC`, `Xbox`, `PlayStation`, `Nintendo Switch`) stay present; removing `Steam` drops `Remove Steam` (count 0) while `Linux` and all built-ins remain. | `screenshots/08-crud.png` |

## Files added (no app source touched)

- `frontend/e2e/walkthrough.spec.ts`
- `frontend/e2e/helpers.ts` — additive helpers only (`SCREENSHOTS`, `FIXTURES`,
  `shot`, `fixture`, `readProject7Schema`, `seedGoldenSchema`)
- `frontend/e2e/fixtures/*.json` — deterministic game records derived from the
  project-7 schema
- `frontend/e2e/screenshots/01-progress.png` … `08-crud.png`
- `frontend/e2e/WALKTHROUGH_RESULTS.md` (this file)
