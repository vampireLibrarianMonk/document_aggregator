# Developer Guide

Everything you need to run, understand, and extend the platform. It is
local-first and offline by design: no AWS, no OpenSearch, no external AI service
is required. Cloud pieces are optional adapters behind the same interfaces.

## Requirements

- Python 3.12
- Node.js 20 (for the frontend)
- Docker + Docker Compose (for the container stack)

## Repository layout

```
backend/
  app/
    convert/      DOCX/PPTX/PDF ingestion -> canonical/first-attempt shape (+ _evidence)
    reconcile/    the reconciliation engine (models, resolve, converge)
    discipline/   build-discipline: spec, learn-from-document, inspect, profiles
    jobs/         SQLite-backed job queue + templated worker loop + ops
    knowledge/    fact pool + page-growth stress harnesses
    layout/       vector-layout (PDF geometry) tier
    main.py       FastAPI app (routes)
    project.py   project loading + run_reconciliation / run_convergence
    store.py      JSON/SQLite-on-disk store (Postgres-ready interface)
    config.py     settings (DATA_DIR, embedding backend, flags)
  tests/          pytest suite (516 tests across 36 modules) + research harnesses
  build_project_graphics.py  generate real PNG figures + enrich graphics.json
  build_sample_docs.py        generate template/draft DOCX/PPTX/PDF from projects
  seed.py                     opt-in dev tool: throwaway local aggregation demo (never auto-run)
  verify_project.py          15 assertions over project 1's defect inventory
  worker_main.py              worker entrypoint
frontend/
  src/            React + Vite SPA (components, api client, styles)
  a11y/           accessibility audit toolkit (run.mjs, flows.mjs, config, docs)
deploy/
  docker/         Dockerfile.api / Dockerfile.rhel (worker) / Dockerfile.frontend / nginx.conf
  AIRGAP_RHEL.md  enclave deployment checklist
sample_docs/
  project/<id>/  corpus (+ figures/), corrections, first_attempt (+ generated/)
docs/             this guide + the user guide + testing/ (bake-off research)
docker-compose.yml
```

## Running locally

Backend (from the repo root):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend
```

The app starts empty. To get data, instantiate a sample case from the UI
("Start from a sample") or via `POST /projects/from-template/{id}`, or upload
your own documents on the Ingestion tab. Optionally, `python backend/seed.py`
populates one throwaway local aggregation project for manual testing (opt-in,
never auto-run).

Frontend:

```powershell
cd frontend
npm install
npm run dev     # http://localhost:5173, proxies /api to the backend
```

Full stack via Docker (BuildKit):

```powershell
$env:DOCKER_BUILDKIT=1
docker compose build
docker compose up -d
# frontend on http://localhost:8080, api on http://localhost:8000
# The app starts empty; create a project from the UI ("Start from a sample"),
# or optionally seed a throwaway aggregation demo into the volume:
#   docker compose exec api python backend/seed.py
```

## How correction works (the pipeline)

1. **Ingest / convert** (`app/convert/`): a real DOCX/PPTX/PDF is parsed into a
   `first_attempt` dict plus an `_evidence` block (observed fonts, tables, images
   with size/title/alignment, header/footer, page numbers). OOXML style
   inheritance is resolved so inherited formatting is observed.
2. **Reconcile** (`app/reconcile/engine.py`): for every unit declared by the
   template + manifest, the engine picks the corpus-backed correct value,
   applies comment corrections, or flags `needs_review` / `conflict`. It never
   computes or invents a value.
3. **Build-discipline inspection** (`app/discipline/`): the formatting/placement
   rubric is **learned from the template document's own evidence**
   (`learn_discipline`), falling back to the pinned `gov_standard` profile only
   where the template is silent. The draft's evidence is then inspected against
   that rubric (`inspect`). Policy: every rule is declared, learned, or an
   explicit "undefined" call-out - never a silent default.
4. **Report**: a `CorrectedReport` with per-unit status, provenance, candidates,
   and discipline findings. Summarized into counts for the UI.

Key invariant: **no fabrication, no auto-resolution of genuine conflicts, no
silent defaults.** These are enforced by `tests/test_invariants.py` and
`verify_project.py`.

## Project data model

Each project under `sample_docs/project/<id>/`:

- `project.json` - manifest: `fields` (key/label/section/extract/hint),
  `section_bodies`, `table`.
- `first_attempt/incident_report_template.json` - the rubric (required sections,
  table specs, furniture rules, `build_discipline` profile reference).
- `first_attempt/incident_report_draft.json` - the flawed draft (with a
  `_seeded_defects` inventory).
- `first_attempt/generated/` - real `template`/`draft` `.docx/.pptx/.pdf`
  generated from the JSON (the DOCX is the authoritative rubric source at
  reconciliation time).
- `corpus/` - source `.txt`/`.md` docs, `figures/*.png` (real images with baked
  centered titles), `graphics.json` (managed name/title/file/size/align), and
  `fact_pool.json` (for the page-growth harness).
- `corrections/comments.json` (single round) and `rounds.json` (multi-round).

### Regenerating project assets

After changing figures or project JSON, regenerate the derived assets:

```powershell
.\.venv\Scripts\python.exe backend\build_project_graphics.py   # real PNGs + graphics.json
.\.venv\Scripts\python.exe backend\build_sample_docs.py         # template/draft docx/pptx/pdf
```

`build_project_graphics.py` is data-driven: it reads each project's existing
`graphics.json`, derives a title from the caption, generates a real PNG with a
centered baked-in title at a controlled size, and rewrites `graphics.json`
enriched with `title`/`file`/`width`/`height`/`align`.

### Adding a project

1. Create `sample_docs/project/<id>/` with `project.json`, a template + draft
   JSON, corpus docs, a `graphics.json`, and `corrections/comments.json`.
2. Run the two generators above.
3. The project appears automatically (loaders discover any dir with a
   `project.json`).

## Async jobs

Heavy work runs through a job queue (`app/jobs/`):

- `POST /jobs {op, payload}` enqueues; `GET /jobs/{id}` polls; `GET /jobs` counts.
- Ops: `reconcile`, `converge`, `render_geometry`, `pipeline`.
- The worker (`worker_main.py`) drains the shared-volume SQLite queue;
  `WORKER_CONCURRENCY` sets the thread count. The queue interface is
  Postgres-ready.

## Testing regime

The project is verified at four levels, from pure-unit up to a real browser
driving the built app against a live stack. Everything below runs **offline and
deterministic by default** — the `backend/tests/conftest.py` autouse fixture
pins `BEDROCK_ENABLED=false`, so no test makes a live model call unless you
explicitly opt in with `BEDROCK_ENABLED=true`.

Think of the levels as a pyramid: the backend `pytest` suite is the broad base
(fast, always run), `verify_project.py` and the workflow e2e tests are the
mid-tier integration gates, the Playwright suite + a11y audit are the on-demand
UI gates, and the research harnesses under `docs/testing/` are the archived
experiments that decided the architecture.

### Level 1 — Backend unit + API suite (`pytest`)

The core gate. **516 tests across 36 modules**, all offline:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q           # whole suite
.\.venv\Scripts\python.exe -m pytest backend/tests/test_invariants.py -q   # one module
.\.venv\Scripts\python.exe -m pytest backend/tests -k json_alignment -q    # by keyword
```

What the suite covers, by area:

| Area | Modules (examples) | What it proves |
|---|---|---|
| **Correction engine invariants** | `test_invariants` (78), `test_randomized_draft` (60), `test_convergence` | No fabrication, no auto-resolution of genuine conflicts, no silent defaults — fuzzed over randomized drafts. |
| **Ingestion / convert** | `test_conversion` (48), `test_ocr`, `test_vector_layout` | DOCX/PPTX/PDF → canonical shape + evidence; OOXML style inheritance; OCR; PDF geometry. |
| **Correction Orchestrator** | `test_command_center` (35), `test_coordinator_proveout` | The DAG + queue + convergence path is byte-identical to the inline path across all six sample projects and both modes. |
| **JSON → golden-JSON batch** | `test_json_alignment*` (9 modules), `test_batch_api`, **`test_batch_workflow_e2e`** | Mapping cascade, relevance cutoff, drift, profile library, and the **five batch pathways end-to-end through the HTTP API** (see below). |
| **Discipline / fact-pool / page-growth** | `test_discipline*`, `test_factpool` (25), `test_pagegrow` (18) | Formatting rubric learned from the template; figure/table placement; page-growth stress. |
| **Jobs + diagnostics + settings** | `test_jobs`, `test_job_ops`, `test_diagnostics` (15), `test_project_settings` | Async queue lifecycle; the Diagnostics probes; per-project settings (incl. the relevance dial). |
| **Model tier (opt-in)** | `test_model_sweep`, `test_tooluse_spiral`, `test_json_alignment_semantic` | Run offline as no-ops; `BEDROCK_ENABLED=true` turns on the live-model paths. |

#### Testing a scenario *in its totality* (the pattern to copy)

`backend/tests/test_batch_workflow_e2e.py` is the reference for exercising a
whole user workflow end-to-end. It drives the **same HTTP endpoints the UI
buttons call, in button-click order**, and asserts the human-visible result for
each scenario — this is how we reliably test "buttonology" without a flaky
browser layer. Each of the five JSON-batch pathways is a complete scenario
(`novel_research` → `replay_clean` → `review` → `reject_irrelevant` →
`drift_repair`), plus a combined mixed-batch pass and an output-is-JSON-only
lock.

Two deliberate design choices make it a true contract test:

- it mirrors the frontend's `filesToDocs` (file → `{doc_id, records}`) and
  `PATHWAY_LABELS` map as small local helpers, so if the backend pathway
  constants ever drift from what `BatchPanel.tsx` renders, the test fails;
- it runs the real `Worker` against the shared job queue the API enqueued to,
  so the async job path is exercised, not stubbed.

When you add a new user-facing workflow, add a sibling `*_workflow_e2e.py` that
walks its endpoints the same way.

### Level 2 — Project assertion gate

```powershell
.\.venv\Scripts\python.exe backend\verify_project.py       # 15 assertions over project 1's defect inventory
```

Proves the engine's numbers at the API level for the TGX-9 reference project
(the figures the project guides quote). The Playwright suite then proves those
same numbers survive the trip to the screen.

### Level 3 — Lint, types, and secret scanning (pre-commit)

```powershell
.\.venv\Scripts\ruff.exe check backend          # lint (E,F,I,N,W,UP)
.\.venv\Scripts\ruff.exe format backend          # formatter
.\.venv\Scripts\mypy.exe backend/app             # types
pre-commit run --all-files                        # everything below in one shot
```

`.pre-commit-config.yaml` wires the automated gates that run on every commit:
`ruff` (+ `ruff-format`), `mypy` over `backend/app/`, **gitleaks** secret
scanning, `oxlint` over `frontend/src/`, plus the standard hygiene hooks
(trailing whitespace, EOF, YAML/JSON/TOML/AST checks, large-file + merge-conflict
+ private-key guards, LF line endings). These are the only gates that run
automatically; everything else is run on demand.

### Level 4 — Frontend build, lint, a11y, and browser e2e

```powershell
cd frontend
npm run build            # tsc -b + vite build (the type + build gate)
npm run lint             # oxlint
npm run audit:a11y       # axe-core WCAG 2.1 A/AA + keyboard/focus across 17 UI states
npm run e2e              # Playwright: 20 flows through a real browser (needs the stack up)
npm run e2e:ui           # the Playwright UI runner, for debugging one flow
```

- **a11y audit** (`frontend/a11y/`): axe-core plus keyboard reachability/focus
  checks across 17 interactive UI states; 0 violations is the bar. Transferable
  to other projects — see `frontend/a11y/ACCESSIBILITY.md`.
- **Playwright e2e** (`frontend/e2e/`): drives the **built** app in a real
  browser against the **live** Docker stack and asserts the on-screen values the
  guides promise — tab-gating, the six correction cases (draft + template), the
  four-pathway ingestion upload, the JSON→golden batch (all five pathways), and
  Diagnostics. The asserted numbers live in `frontend/e2e/expected.ts`. It is
  **on-demand only** (needs Docker + a browser binary), so it is deliberately
  *not* in pre-commit/CI. Prerequisites and the full matrix are in
  `frontend/e2e/README.md`.

The a11y audit and the e2e suite share one static-server-with-`/api`-proxy
(`frontend/a11y/serve.mjs`), so the app is exercised identically by both.

### What runs when

| Gate | Command | Automatic? |
|---|---|---|
| Lint / format / types / secrets / oxlint | `pre-commit run --all-files` | **Yes** (every commit) |
| Backend suite | `pytest backend/tests` | On demand (run before every push) |
| Project assertions | `python backend/verify_project.py` | On demand |
| Frontend build + lint | `npm run build` / `npm run lint` | On demand (run before every push) |
| a11y audit | `npm run audit:a11y` | On demand (needs a served app) |
| Browser e2e | `npm run e2e` | On demand (needs Docker + Chromium) |

Before a push, the minimum bar is: backend suite green, `verify_project.py`
green, `npm run build` clean, and `pre-commit run --all-files` clean. Run the
a11y + e2e gates whenever you touch the frontend or any route the guides quote.

### Research & bake-offs (archived experiments)

The experiments that decided the Correction Pipeline's engine architecture are
documented under [`docs/testing/`](testing/README.md), each with a self-contained
harness under `backend/tests/` and persisted machine-readable results:

- `backend/tests/bakeoff/` — structure-extraction bake-off (can the engine
  reproduce the gold from raw uploads? — no; the manifest is the decisive input).
- `backend/tests/command_center/` — the bake-off for the correction engine now
  known as the **Correction Orchestrator** (orchestrator + sub-agents + manifest
  strategies + pathways; 72-cell matrix). The `command_center` package/test path
  keeps its generic name because the same engine also drives alignment + batch.
- `backend/tests/command_center/alpha/` — the precision-correction scaling alpha
  loop (precision-editing techniques vs document size; micro-model verdict).
- `backend/tests/command_center/datagen/` — the synthetic growth-dataset
  generator the alpha loop runs on.

The winner (figure-relabel corpus grounding + Option-B model refinement over the
deterministic reconcile baseline) is integrated into the app in
`app/corrections/refine.py`; the bake-off harnesses themselves inform the design
and are not wired into the running app. See
[`docs/testing/README.md`](testing/README.md) for method, metrics, and the
recommended architecture.

## Air-gapped / RHEL deployment

See `deploy/AIRGAP_RHEL.md`. Summary: mirror the UBI base images + approved OSS
(LibreOffice, fonts, CPU torch wheels) into the enclave registry, vendor a pip
wheelhouse, bake or mount the embedding model (or run the hashing embedder),
keep `BEDROCK_ENABLED=false`, and run non-root under OpenShift group 0 with
`DATA_DIR` on a PersistentVolume.

## Configuration

Environment variables (see `app/config.py`):

- `DATA_DIR` - data root (immutable originals, canonical docs, indexes, jobs.db).
- `EMBEDDING_BACKEND` - `auto` | `sentence-transformers` | `hashing`
  (compose defaults to `hashing` with `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE=1`).
- `WORKER_CONCURRENCY` - worker thread count.
- `BEDROCK_ENABLED` - optional cloud feedback interpreter; off by default.
