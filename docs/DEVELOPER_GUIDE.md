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
  tests/          pytest suite (13 modules)
  build_project_graphics.py  generate real PNG figures + enrich graphics.json
  build_sample_docs.py        generate template/draft DOCX/PPTX/PDF from projects
  seed.py                     seed the demo project
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
docs/             this guide + the user guide
docker-compose.yml
```

## Running locally

Backend (from the repo root):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\seed.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend
```

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
docker compose exec api python backend/seed.py   # seed the shared volume
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

## Testing and quality gates

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q      # ~244 tests
.\.venv\Scripts\python.exe backend\verify_project.py       # 15 project assertions
.\.venv\Scripts\ruff.exe check backend                      # lint

cd frontend
npm run build            # tsc + vite build
npm run lint             # oxlint
npm run audit:a11y       # axe-core + keyboard/focus audit (needs a running/seeded backend)
```

The a11y toolkit lives in `frontend/a11y/` and is transferable to other
projects; see `frontend/a11y/ACCESSIBILITY.md`.

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
