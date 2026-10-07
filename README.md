# Document Aggregation Platform

A local-first document ingestion, normalization, search, and aggregation
pipeline with a barebones React frontend that tracks each document through the
pipeline and assembles an intermediate report you can export to DOCX, PPTX, or
PDF.

Everything runs offline on CPU. No AWS, no OpenSearch, no external AI service is
required. Cloud pieces would slot in as adapters behind the same interfaces.

**Version:** see [`VERSION`](VERSION) and [`CHANGELOG.md`](CHANGELOG.md).

### Documentation

- [User Guide](docs/user-guide/USER_GUIDE.md) - using the app: the tabs, the
  correction pipeline, what statuses/colors mean, what to submit and where.
- [Project walkthroughs](docs/user-guide/projects/) - step-by-step guides for
  specific projects: the [TGX-9 correction case](docs/user-guide/projects/tgx-9/README.md)
  and the [JSON→golden batch case](docs/user-guide/projects/json-batch/README.md).
- [Developer Guide](docs/DEVELOPER_GUIDE.md) - architecture, running it, the
  reconciliation + discipline engine, the project data model, testing.
- [Coordinator prove-out](docs/testing/coordinator-proveout.md) - both pipelines
  measured through the command center.
- [JSON-alignment benchmark](docs/testing/json-alignment-benchmark.md) - how the
  schema-alignment slice is scored (and the MaDI-Bench assessment).
- [Air-gapped RHEL deployment](deploy/AIRGAP_RHEL.md) - the enclave checklist.
- [Accessibility toolkit](frontend/a11y/ACCESSIBILITY.md) - the transferable
  axe-core + keyboard/focus audit.

## What it does

```
Source docs (PDF/DOCX/PPTX/TXT/MD/PNG/JPEG)
      │
      ▼
  ingest → parse → chunk → embed → index      (tracked per document)
      │
      ▼
  Canonical JSON  +  Supplementals (comments / angry emails / corrections)
      │
      ▼
  Aggregated report (ordered by resolved effective DTG)
      │
      ▼
  Export → JSON / Markdown / DOCX / PPTX / PDF
```

- **Immutable originals.** Source bytes are hashed (SHA-256) and stored
  untouched. Everything else is derived state.
- **Provenance + timestamp evidence.** Every block records the parser used;
  every document keeps candidate timestamps and a deterministically resolved
  effective date-time group with its source.
- **Hybrid search.** Local vector embeddings + lexical overlap fused with
  Reciprocal Rank Fusion, returning OpenSearch-shaped hits.
- **Assembly, not generation.** The report is built from source words ordered
  and grouped — nothing is invented.

## The correction pipeline (primary project)

Beyond raw ingestion, the platform models a **four-component correction
pipeline** that takes a messy first attempt to a clean, output-neutral
intermediate JSON:

```
1. Original corpus        raw source docs + named graphics (ground truth)
2. First attempt          a flawed draft OR a blank template (found to be wrong)
3. Comments / emails      human feedback: what is wrong (the judge, in draft mode)
4. Corrected JSON         output-neutral corrected representation (stopping point)
```

A **project** is the top-level container for one such correction case (a project
IS a project): it owns the corpus, template, first attempt, comments, and the
corrected output. The global project selector in the UI scopes every tab to the
chosen project; generating or uploading creates a new project.

One reconciliation engine handles both modes: the template rubric always runs,
and comment corrections are layered on top. Every corrected unit carries a
**status** (`unchanged` / `filled` / `corrected` / `needs_review` / `conflict`)
and **provenance** (which corpus doc, which correction, which template rule), at
the smallest asserted unit (field / graphic / table cell / furniture element).

The demo project (TGX-9 telemetry incident) seeds 16 traceable defects across
four classes:

- **value** — wrong firmware version, wrong outage duration, blank corrective actions
- **graphic** — mislabeled figure (relabel), misplaced figure (move), missing figure (insert)
- **table** — wrong column order, missing required column, wrong font/style
- **furniture** — dangling cross-reference, stale figure numbering, missing table
  title, missing page numbers, missing classification marking

Guardrails proven by the project: values are literal fills from corpus spans —
never computed; disagreeing corrections become a `conflict` with all candidates
preserved (never auto-resolved); required-but-unsupported fields (classification,
approvals) become `needs_review` rather than fabricated.

Verify it: `python backend\verify_project.py` (15 assertions over the defect
inventory). Inspect it: the **Correction Pipeline** tab in the frontend.

## The command center (production orchestrator)

A single **command center** (`app/command_center/`) orchestrates the work. It
decomposes a run into a bounded sub-task DAG, dispatches each task through a
parallel queue with a **deterministic order-preserving assembler**, threads
results through a shared context, and iterates to convergence (`needs_review` →
0, genuine conflicts preserved, capped rounds). The correction pipeline runs
through it **by default** — the inline path is retained as a byte-identical
fallback (`engine="direct"`), proven across all six sample projects in
`backend/tests/test_command_center.py`. It is deliberately workflow-agnostic:
the JSON→golden alignment below plugs in as additional sub-agents, not a second
framework.

## JSON → golden-JSON alignment (schema conversion)

Beyond document correction, the platform converts arbitrary source JSON into one
canonical **golden** JSON Schema — the "thousands of files across teams, few
distinct shapes" workload — on the same command center and with the same
precision-first, no-fabrication posture (`app/json_alignment/`).

- **Deterministic mapping.** Source fields map to the golden schema by a
  strongest-first cascade (exact name → normalized token-set → declared alias →
  name+description overlap). It **abstains** (`needs_review`) when nothing clears
  the floor and marks genuine ties `conflict` — it never invents a
  correspondence. Bounded, fail-closed transforms (number cast, date→year/ISO,
  enum/alias canonicalization, delimited→list) and a grounding check that
  rejects any value without a traceable source complete the slice.
- **Learn once, replay many.** A learned mapping persists as a **Conversion
  Profile**; a library of profiles (one per source shape) is keyed by a
  value-independent **shape signature**. A novel shape is researched once and
  held `provisional` until a one-time human approval, after which every future
  file of that shape **replays deterministically with zero inference**.
- **Relevance cutoff.** A per-project dial (`reject_below`) quarantines a file as
  unrelated when too few required golden fields map; zero mappable required
  fields is always a reject. Nothing unrelated is ever force-converted.
- **Drift → re-emerge.** Structural, type, and **behavioral** (fill-rate
  collapse) drift detection pulls a shape back out of fast replay into research
  on only the broken fields, then re-approval settles it again.
- **Mass batch, async.** A batch is clustered by shape and one pathway is run
  **per cluster** (replay / research / drift-repair / review / quarantine)
  concurrently, under a **cgroup-aware** worker cap (reads the pod's CPU limit,
  cores − 1) so it is safe in a container.
- **Optional model tier.** When `BEDROCK_ENABLED`, an LLM tier recovers
  abstentions by *picking among the profiled candidates* (tool-use, temperature
  0) and re-verifying every pick deterministically. Offline it is a no-op and
  the deterministic result stands.

Measured end to end in [`docs/testing/coordinator-proveout.md`](docs/testing/coordinator-proveout.md);
scored in [`docs/testing/json-alignment-benchmark.md`](docs/testing/json-alignment-benchmark.md).
Walk it in the [JSON→golden batch guide](docs/user-guide/projects/json-batch/README.md).

## Build discipline (document inspection)

Document inspection is the platform's core purpose: catching where a submitted
document violates placement and formatting rules. The **build discipline**
(`app/discipline/spec.py`) is a single declarative contract covering:

- **images** — caption required, caption format + position (below/above)
- **tables** — title required, title format + position, column order, header
  style, alignment
- **header / footer** — required content (report title; page number +
  classification)
- **page numbers** — presence + region
- **per-element text formatting** — font / size / weight / casing by element
  type (heading, body, caption, table header, field label)

**No silent defaults.** Every discipline rule is either explicitly declared in
the template's `build_discipline` block, or **learned** from the user's
presented template/draft (its own dominant per-element formatting becomes the
conformance baseline, so deviations read as inconsistencies), or — if neither —
**called out** as `needs_review` ("discipline for X is undefined; specify it").
Nothing falls back to a hardcoded style (`app/discipline/spec.py`,
`app/discipline/learn.py`).

**Effective-style resolution.** For DOCX, per-element font/size/weight is
resolved by walking the OOXML style chain the way Word renders it: run direct
properties → paragraph style → based-on ancestry → `docDefaults` → theme minor
font. So formatting carried by named styles (not just direct runs) is observed
reliably (`app/convert/docx_in.py`).

The converters extract **observable evidence** per element (`_evidence` block),
and the engine runs an **inspection pass** (`app/discipline/inspect.py`)
comparing evidence against the resolved discipline. Each deviation becomes a
`discipline` finding: `corrected` where the engine can normalize it (missing
caption/title, absent page numbers, wrong header style, formatting drift),
`needs_review` where a human is required (missing classification, undefined
rule). "Not observed" is never flagged — only positive contradictions.

Inspection coverage by format: **DOCX** high (style-chain-resolved formatting,
table titles, header/footer fields, caption adjacency), **PPTX** medium, **PDF**
partial (text-derived). `app/discipline/inject.py` builds documents with
deliberate violations; the discipline tests confirm each is detected via
inspection.

### Vector-layout tier (geometry, not pixels)

For true placement verification, a document is rendered `DOCX → LibreOffice
headless → PDF`, and `app/layout/` extracts each element's **bounding box**
(figures, captions, tables, page numbers) with `pdfplumber` (MIT, offline).
Discipline `layout` rules are then checked as **box relationships**: is a
caption's box below and horizontally aligned to its figure, is a table title
above the table, is the page number within the footer band and centered. This
is geometry — coordinates and boxes — not rasterized pixels.

The tier is **gated on LibreOffice**: when `soffice` is present it runs and
merges geometric findings into `discipline_findings`; when absent the pipeline
degrades cleanly to structural inspection. Undefined geometric rules are called
out (`needs_review`), never defaulted. Tests run against committed rendered-PDF
fixtures so they pass without LibreOffice in dev/CI.

### Pinned government standard (reproducibility)

Rendering geometry is only reproducible if the fonts are pinned — LibreOffice
substitutes missing fonts silently, so the same DOCX could otherwise render to
different box geometry across machines. The committed profile
`app/discipline/profiles/gov_standard.json` pins the standard:

- **Fonts:** the **Liberation** family (Red Hat, SIL OFL) — metric-compatible
  with Arial / Times New Roman / Courier New — plus **Carlito/Caladea** for
  Calibri/Cambria. "Metric-compatible" means each glyph has identical
  width/height to its proprietary twin, so a document specifying Arial renders
  to the *same* geometry whether the box has Arial or Liberation Sans.
- **Format:** 12pt serif body, 14pt bold sans headings, figure captions below,
  table titles above, page numbers bottom-center, 1-inch margins.

`app/discipline/fonts.py` makes the substitution **explicit**: observed and
declared fonts are normalized to their metric-compatible family before
conformance checks, so a document written in Arial is *not* falsely flagged
against a Liberation Sans rule (it conforms), while a genuinely non-compatible
font (e.g. Comic Sans) still is. A template opts into the standard with
`"build_discipline": {"profile": "gov_standard"}`. Combined with pinning the
exact font set in the image, geometry extraction is deterministic (same PDF →
identical element boxes), verified by the reproducibility tests.

### Air-gapped RHEL deployment

The stack is three modular images, each on Red Hat UBI: the **api**
(`Dockerfile.api`, lean — no LibreOffice, no embedding stack), the **worker**
(`Dockerfile.rhel`, the only image with LibreOffice + pinned fonts + CPU-only
`torch`/`sentence-transformers`), and the **frontend** (`Dockerfile.frontend`,
nginx serving the built React app). Splitting the embedding stack out of the api
drops it from ~3.6GB to ~1.7GB; only the worker carries the heavy dependency.

The worker installs `libreoffice-headless` via `dnf` and the metric-compatible
font set (Liberation / Carlito / Caladea) so rendering geometry is
deterministic. Stock public UBI carries only a subset, so those installs are
best-effort locally and come in full from the enclave's mirrored RHEL AppStream
— the enclave *adds* capability, it is not a build limitation. See
`deploy/AIRGAP_RHEL.md` for the enclave checklist: mirror the UBI images + RPMs
into the internal registry, vendor a pip wheelhouse (incl. CPU torch), bake the
embedding model, keep Bedrock disabled, and run non-root on OpenShift with a
PersistentVolume for `DATA_DIR`. LibreOffice is offline-only and optional; the
pipeline never depends on it at runtime.

## Fact pools and page-growth (stress testing)

Each project has a `corpus/fact_pool.json` — a compact, withdrawable store of
source-cited records seeded once from real **public-domain** reports (NTSB for
aviation, CISA advisories for security, FDA/CDC patterns for clinical, CPSC/NHTSA
for manufacturing, utility/telecom outage structure for hardware). Only
non-copyrightable facts are extracted and paraphrased; the JSON is committed as
data and read **offline** (no live fetch at test time).

The page-growth harness (`app/knowledge/pagegrow.py`) grows a synthetic document
one page at a time. Each page **withdraws a distinct record** from the pool, so
every page has genuinely distinct ground truth, and page-scoped retrieval keeps
a page's fields resolving against only that page's facts. Page count is **capped
at the pool's capacity** (page limit = source-document capacity); requesting more
raises `PoolExhaustedError`. The `app/knowledge/catalog.py` defect knowledge base
distributes a shuffled defect subset per page. Tests assert distinct per-page
values, the capacity cap, no fabrication, and contiguous figure/table numbering
at every size.

## Layout

```
backend/            FastAPI app (modular)
  app/
    reconcile/      the correction/reconciliation engine (models, corpus_facts, engine)
    project.py     loads the four components and runs reconciliation
    parsers, embeddings, search, aggregate, exporters, store
  seed.py           opt-in DEV tool: populate a throwaway local aggregation demo (never auto-run)
  verify_project.py  asserts all 16 defects resolve as expected
frontend/           React + Vite + TypeScript (modular components + api client)
sample_docs/
  project/         the four-component correction corpus:
    corpus/         source docs + graphics.json (named graphic refs)
    first_attempt/  incident_report_draft.json + incident_report_template.json
    corrections/    comments.json (incl. a conflicting severity pair)
```

## Quick start

### 1. Backend

```powershell
# from the repo root
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# run the API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend
```

The API listens on `http://localhost:8000`. Try `GET /health` and
`GET /projects` (empty on a fresh install) and `GET /templates` (the bundled
sample cases). The app starts with no projects; create one by instantiating a
sample (`POST /projects/from-template/{id}`), generating, or uploading
documents. There is no preloaded data.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to the backend.

### 3. Full stack via Docker Compose (RHEL/UBI)

Three modular services, one image each — `api` (lean FastAPI), `worker`
(isolated LibreOffice + pinned fonts, runs the async job loop), `frontend`
(nginx serving the built React app) — plus a shared `data` volume. Build with
BuildKit (parallel stages, better caching):

```bash
DOCKER_BUILDKIT=1 docker compose build
docker compose up
```

- Frontend on `http://localhost:8080` (proxies `/api` → the `api` service).
- API on `http://localhost:8000`.
- The `worker` claims jobs from a shared SQLite queue on the `data` volume and
  runs the heavy render/geometry/reconcile ops — the LibreOffice dependency
  lives only in that image (modular per endpoint).
- PostgreSQL is stubbed in `docker-compose.yml` for a later upgrade; the current
  store is JSON/SQLite on the volume.

See `deploy/AIRGAP_RHEL.md` for enclave specifics.

### Async jobs

Heavy work runs through a job queue (`app/jobs/`), so the API stays responsive:

- `POST /jobs` `{ "op": "pipeline", "payload": {"project_id":"1","mode":"draft","source_format":"docx"} }` → `{job_id}`
- `GET /jobs/{job_id}` → state + result
- `GET /jobs` → queue counts

Ops: `reconcile`, `converge`, `render_geometry`, `pipeline`, `batch_align` (the
mass JSON→golden conversion). The worker (`backend/worker_main.py`) processes
them; `WORKER_CONCURRENCY` sets thread count, and the batch op additionally
self-limits to cgroup-aware cores − 1 (`JSON_ALIGNMENT_MAX_WORKERS` overrides).

Correction endpoints are **project-scoped** (a project IS a correction project
in the unified model), under `/projects/{project_id}/project/...`:

- `GET /projects/{id}/project/components` — the four components and their contents
- `GET /projects/{id}/project/component/{cid}?mode=draft|template` — raw contents of one component
- `GET /projects/{id}/project/reconcile?mode=draft|template` — the corrected intermediate JSON
- `POST /projects/{id}/project/convert` — convert an uploaded real DOCX/PPTX/PDF
  into the internal first-attempt shape (the file-submission path)
- `POST /projects/{id}/project/interpret` — turn freeform reviewer feedback into
  constrained, validated correction operations (optionally Bedrock-backed); never
  invents values

Batch **JSON→golden** alignment is project-scoped too, under
`/projects/{project_id}/alignment/...`:

- `PUT /projects/{id}/alignment/target-schema` — set/replace the project's golden
  target JSON Schema (`GET` fetches it)
- `POST /projects/{id}/alignment/batch` — queue a mass conversion (`{docs}`);
  returns a `job_id` to poll via `GET /jobs/{id}`. Honors the project's
  `reject_below` dial; refuses until a golden schema is set
- `GET /projects/{id}/alignment/library` — the learned shapes with their approval
  state (approved vs provisional) and per-shape mappings
- `POST /projects/{id}/alignment/approve` — one-time human sign-off on a
  provisional shape, after which it replays automatically

The per-project relevance dial is set via `PATCH /projects/{id}`
(`{"reject_below": 0.0..1.0}`) or the Ingestion-tab slider.

The legacy flat `/project/*` routes still exist as deprecated aliases during the
transition. `GET /projects` lists every project, including the bundled demo
projects (which are surfaced as projects); the global project selector in the UI
scopes all five tabs to the chosen project.

### What the UI surfaces vs. what's API-only

To set expectations honestly: the **frontend exercises the synchronous path** —
the Correction Pipeline tab calls the project-scoped `.../project/reconcile` and
`.../project/converge` directly, and all five tabs scope to the selected project.
The following are implemented and tested at the **API/worker level but not yet
wired into the UI**:

- the **async job queue** (`POST /jobs` … ) — the UI runs projects
  synchronously; jobs are for the worker/headless path;
- **`/project/convert`** — uploading a real DOCX/PPTX/PDF as the first attempt.
  In the Correction Pipeline tab, the "source fidelity" selector chooses which
  **pre-converted** project artifact to read (JSON/DOCX/PPTX/PDF), so you can
  see how fidelity affects extraction; it does not convert a file you upload;
- **`/project/interpret`** — the feedback-to-operations interpreter.

These are roadmap items for the frontend, not hidden features. Nothing in the UI
depends on them.

## Tabs

- **Correction Pipeline** — the four-component flow (corpus → first attempt →
  comments → corrected JSON), a draft/template mode toggle, and the corrected
  report with per-unit status + provenance.
- **Ingestion** — every submitted document with live stage status (ingest →
  parse → chunk → embed → index), block/chunk/artifact counts, resolved
  effective DTG, and a canonical-JSON inspector. Also hosts the **Batch
  conversion** section (opt-in switch): set a golden schema, tune the relevance
  dial, upload many JSON files, and approve new shapes per cluster.
- **Supplementals** — add and view comments, emails, corrections, and interview
  notes; sentiment is auto-classified.
- **Search** — hybrid lexical + vector retrieval with per-hit provenance.
- **Report & Export** — the assembled intermediate report with one-click export
  to JSON / Markdown / DOCX / PPTX / PDF.

## Configuration

Copy `.env.example` to `.env` and adjust. Notable flags:

- `EMBEDDINGS_ENABLED` (default `true`) — local deterministic embedder.
- `OCR_ENABLED`, `VISION_ENABLED`, `LLM_ENABLED` (default `false`) — enrichment
  is optional; baseline extraction never depends on it.
- `OPENSEARCH_HOST` empty → local search client; populated → real OpenSearch.

## Dev tooling

`.pre-commit-config.yaml` runs ruff + ruff-format + mypy on the backend,
gitleaks for secret scanning, and oxlint on the frontend. Install with:

```powershell
pip install pre-commit
pre-commit install
```

### Accessibility audit

`frontend/a11y/` is a transferable a11y gate: axe-core (machine-checkable WCAG
2.1 A/AA) plus keyboard/focus checks axe can't do (focusability, no tab-order
traps, `:focus-visible` ring, tablist roving), driven through 9 interactive UI
states in headless Chromium. Run it after a build:

```powershell
cd frontend
npm run build
npm run audit:a11y   # non-zero exit on any violation; writes a11y/a11y-report.json
```

It is air-gap friendly (loopback only; browser binary mirrored into the
enclave). It is **not** a conformance sign-off — manual screen-reader + keyboard
testing is still required. See `frontend/a11y/ACCESSIBILITY.md` for the full
process, the requirement-driven checklist, and how to transfer it to another
project.

## Notes

Optional parsers (`python-docx`, `python-pptx`, `pypdf`, `Pillow`) and exporters
(`reportlab`) degrade gracefully: if a library is missing, that format reports a
clear message instead of crashing the service. Rendering to DOCX/PPTX/PDF is a
formatting step over already-assembled content.
