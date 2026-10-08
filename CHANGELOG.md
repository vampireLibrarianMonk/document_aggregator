# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Post-0.3.0 vetting: an end-to-end UI test suite, an expanded accessibility
audit, a Diagnostics-page audit and outfit, and the UI/doc fixes that work
surfaced.

### Added
- End-to-end UI suite (`frontend/e2e/`, Playwright): 20 flows across five specs
  that drive the **built** app through a real browser against the **live** Docker
  stack and assert the on-screen values the project guides promise —
  nav/tab-gating, all six sample correction cases (draft + template), the
  four-pathway ingestion upload, the JSON→golden batch section (all five
  pathways), and the Diagnostics page. On-demand (`npm run e2e`), deliberately
  not wired into pre-commit/CI. Shares the static-server-with-`/api`-proxy with
  the a11y audit (`frontend/a11y/serve.mjs`). See `frontend/e2e/README.md`.
- Expanded the accessibility audit from 9 to 17 interactive UI states, adding the
  Batch-conversion section and the Diagnostics page; still 0 axe violations and
  0 keyboard/focus issues (`frontend/a11y/flows.mjs`).
- Diagnostics page audit and outfit: every service row (embeddings / OCR / layout
  geometry / Bedrock) now reports concrete, probed detail; Refresh re-probes;
  versions, offline guards, and the data directory render; the overall posture
  rolls up correctly (Bedrock-offline is not an error). Audit documented in
  `docs/testing/diagnostics-audit.md`, covered by `backend/tests/test_diagnostics.py`.
- Wired the **Supplementals** tab into the app shell. The panel and its API were
  already implemented and the report already rendered supplementals, but the
  add/view page was not reachable from the navigation; it is now a tab (scoped to
  the selected project), matching the user guide.

### Fixed
- Sample/correction projects could never open the Correction Pipeline or Report
  tabs in the UI: those tabs were gated solely on ingestion `readiness.ready`,
  which only flips once documents are uploaded, so fixture-only projects (zero
  uploads) stayed locked. The tabs now also unlock when a project carries
  correction fixtures (`useProject` exposes `hasCorrectionData`; `App.tsx` gates
  on `hasCorrectionData || inputsReady`).
- The Ingestion upload picker greyed out the sample files: the accepted-extension
  list omitted `.json`, but the sample template / first-draft / corrections are
  all JSON. Added `.json` to the accepted extensions.
- Diagnostics reported a disabled Bedrock integration as green: the state was
  `ok` whenever the endpoint was merely reachable, even with
  `BEDROCK_ENABLED=false` (credentials mounted on dev boxes). The state is now
  `ok` only when the integration is both enabled and available, otherwise
  `offline`; the panel shows explicit enabled / reachable / in-use rows.
- The Diagnostics footer reported a stale pipeline version (`0.1.0`,
  hardcoded) while the app was at `0.3.0`. `PIPELINE_VERSION` now reads the
  repo-root `VERSION` file (overridable via the `PIPELINE_VERSION` env var,
  with a safe fallback), so the footer, the app version, and the changelog
  stay in lockstep. The `VERSION` file is now copied into the api/worker images.
- The Diagnostics Bedrock block implied nothing was orchestrating when Bedrock
  was disabled ("In use: no (offline generator used)"). It now states the
  **model tier** status explicitly and adds an **Orchestrators** row making
  clear the coordinator (corrections) and governor (project generation) run on
  every request regardless of Bedrock — Bedrock only swaps the model tier they
  drive (live Bedrock vs the deterministic/offline tier). Retitled the block to
  "Bedrock (optional model tier)".
- The approved-models list is now a scrollable list box (one model per line,
  with default / recommended / inference-profile tags) instead of a single
  comma-joined line.

### Docs
- Corrected the project-scoped correction endpoint paths in the README: they are
  `/projects/{id}/reconcile`, `/components`, `/converge`, `/resolve`, `/convert`,
  `/interpret` (no stray `/project/` segment), and removed the inaccurate claim
  that flat `/project/*` aliases still exist.
- Corrected the Diagnostics location in the user guide: it is a separate
  `/diagnostics` page reached from a footer link (gated on
  `DIAGNOSTICS_ENABLED`), not one of the top-bar tabs.
- Added the conflict candidate pair each correction walkthrough surfaces, for
  parity with the TGX-9 guide.

## [0.3.0] - 2026-10-07

Phase 4: the command center promoted to the production orchestrator, a
deterministic JSON→golden-JSON schema-alignment capability, and a mass batch
conversion layer for the "thousands of files across teams" workload — all run
through the one command center, benchmarked and proven out on both pipelines.

### Added
- Promoted the command-center orchestrator from the bake-off harness into
  production (`app/command_center/`): a JEV-style Coordinator that decomposes a
  correction run into a bounded sub-task DAG (load_artifacts → parse_corrections
  → reconcile), dispatches each task through a parallel queue with a
  deterministic order-preserving assembler, and iterates to convergence
  (needs_review → 0, genuine conflicts preserved, capped rounds). It is
  deliberately generic so future workflows (e.g. JSON schema alignment) plug in
  as additional sub-agents without a second orchestration framework.
- Deterministic JSON→golden-JSON schema-alignment capability
  (`app/json_alignment/`), orchestrated by the command center as a five-task DAG
  (profile_source → extract_target → infer_mapping → execute → validate).
  Precision-first and non-fabricating: it maps source fields to a target JSON
  Schema via a strongest-first cascade (exact name → normalized token-set →
  declared alias → name+description token overlap), **abstains** (`needs_review`)
  when nothing clears the floor and marks genuine ties `conflict`, applies
  bounded fail-closed transforms (number cast, date→year/ISO, enum/alias
  canonicalization, delimited→list), and validates every produced value against
  the schema plus a grounding check that rejects any value without a traceable
  source. Learned mappings persist as reusable Conversion Profiles for
  deterministic replay on future batches.
- Benchmark harness + our own metric code (`app/json_alignment/scoring.py`,
  `benchmark.py`): schema-match precision/recall/F1 and category-wise value
  normalization accuracy. A committed, air-gap-clean self-generated games
  benchmark is the enforced primary (`backend/tests/fixtures/json_alignment/`);
  MaDI-Bench is supported as an optional external comparison read from a local
  checkout via `MADI_BENCH_PATH` (no MaDI data or code is committed or imported).
  Results and methodology documented in
  `docs/testing/json-alignment-benchmark.md`.
- Mass batch JSON→golden conversion for the "thousands of files across teams"
  workload, run through the command center. New in `app/json_alignment/`:
  - `relevance.py` — a per-project reject dial (`Project.reject_below`, default
    0.5, editable via `PATCH /projects/{id}` and the UI slider): a source is
    quarantined as unrelated when the share of required golden fields it can map
    falls below the dial; zero mappable required fields is always a reject.
  - `signature.py` — value-independent source-shape fingerprints + banded
    matching, so an incoming document routes to the right learned shape cheaply.
  - `profile_store.py` `ProfileLibrary` — a catalog of learned shapes (one per
    team shape), each `provisional` until a one-time human approval flips it to
    `approved` so it replays automatically; drift bumps a version back to
    provisional for re-approval.
  - `drift.py` — structural + type + **behavioral** (fill-rate collapse) drift
    detection that decides when a shape must leave deterministic replay.
  - `concurrency.py` — cgroup-aware `available_cores()` (reads the pod's CPU
    limit, not the node's) with a cores−1 worker cap for safe async in k8s.
  - `batch.py` + `batch_agent.py` — clusters a batch by shape and decides +
    runs ONE pathway per cluster (replay approved / research novel → provisional
    profile / drift-repair the broken fields / review / quarantine), through the
    command center, concurrently. Quarantine/review emit no golden records.
  - `semantic.py` — an optional, `BEDROCK_ENABLED`-gated LLM tier that recovers
    abstentions by picking among the profiled source candidates (tool-use,
    temperature 0) and re-verifying every pick deterministically; offline no-op.
  Exposed via `PUT/GET /projects/{id}/alignment/target-schema`,
  `POST /projects/{id}/alignment/batch` (async job), `GET …/library`, and
  `POST …/approve`, with a Batch conversion section on the Ingestion tab (mode
  switch, relevance dial, multi-file upload, per-cluster approval cards).
- Prove-out of both pipelines through the coordinator, measured and documented
  in `docs/testing/coordinator-proveout.md`: the correction pipeline across all
  six sample projects (both modes) and a 1,350-document / 9-shape JSON→golden
  batch — ~1.1k docs/sec, model/human cost bounded to distinct shapes (not file
  count), 75% zero-model replay once shapes are approved, zero fabrication, and
  the drift→re-emerge→re-settle lifecycle.

### Changed
- The Correction Pipeline now runs through the command center by default. The
  Coordinator reorganizes HOW the pipeline executes (DAG + queue + convergence),
  not WHAT it computes — it calls the same production refine + reconcile, so the
  output is byte-identical to the previous inline path (proven across all six
  sample projects and both pathways in `backend/tests/test_command_center.py`).
  The inline path is retained as a fallback via `engine="direct"` on
  `run_reconciliation` and the `?engine=` query parameter on
  `GET /projects/{id}/reconcile`.

## [0.2.0] - 2026-10-05

Phase 2 (document ingestion UX) and Phase 3 (the correction-engine
architecture: research bake-offs + integration of the winner into the app).

### Added

**Ingestion board (Phase 2)**
- A four-area ingestion board (corpus / template / corrections / first draft)
  with a readiness rule gating the move to the Correction Pipeline: corpus and
  corrections are required, plus at least one of {template, first draft} — the
  two valid upload pathways. A small circular progress indicator sits to the
  left of the upload buttons during uploads.
- Document CRUD on uploaded materials (delete, move between areas) and a
  DocumentViewer with Raw/Canonical views and a Close control.
- Inline preview rendering for image / PDF / DOCX / PPTX via a new
  `app/preview.py` (LibreOffice headless → PDF, cached, air-gap safe, degrades
  cleanly when LibreOffice is absent).
- New-project isolation: a fresh project no longer shows a previous project's
  documents.
- Human correction emails: each sample project now ships raw reviewer emails at
  `sample_docs/project/<id>/corrections/emails/email_N.txt` (the raw upload
  form), alongside the machine-digested `comments.json`.
- The template component moved to its own sibling folder (`template/…`) in all
  six sample projects, separating it from the first-draft.

**Human-in-the-loop resolution (Phase 2)**
- A `conflict` row offers a "Use <value>" button per candidate, and a
  `needs_review` row offers an input + "Set value". The decision is recorded via
  `POST /project/resolve` as a fresh correction round, so the engine's
  last-good-wins collapse supersedes the conflict (or fills the needs_review)
  while leaving the rest of the report unchanged. Never fabricates: a value is
  required, and the target must be a resolvable unit.

**Correction-engine integration — the bake-off winner (Phase 3)**
- A deterministic corpus-grounding step for figure relabels
  (`app/corrections/refine.py`): when reviewer feedback describes a figure in
  prose without naming the file ("the Timeline chart is wrong, it should be the
  packet-loss-versus-temperature figure"), the intended graphic is resolved
  against the corpus graphics manifest and emitted as a relabel op carrying the
  exact filename. The filename comes verbatim from the corpus — nothing is
  invented, and an ambiguous reference emits nothing.
- Option-B model refinement on top of the deterministic reconcile baseline: the
  deterministic engine always runs; when Bedrock is enabled, the feedback
  interpreter proposes additional validated, grounded operations that AUGMENT
  (never override) the structured corrections, so genuine conflicts are
  preserved and a no-fabrication gate drops any ungrounded model value. Offline,
  this is a no-op and the pipeline runs on the structured corrections alone.
- Both these refinements run in `run_reconciliation` (and the freeform
  `/project/interpret` apply path), so both upload pathways (draft / template)
  and both the API and worker reconcile paths benefit. The draft + authored
  manifest path reproduces the gold corrected report on all six sample projects.

**Correction-engine research (Phase 3, harness-only)**
- Structure-extraction bake-off (`backend/tests/bakeoff/`): proved the engine
  cannot reproduce the gold corrected report from raw uploads alone (~27.8%
  value accuracy across approaches); the correction manifest is the decisive
  input.
- Command-center bake-off (`backend/tests/command_center/`): a JEV-style
  coordinator + sub-task DAG + swappable sub-agents (deterministic + model-backed
  gpt-oss-120b) + deterministic order-preserving queue assembler + convergence
  loop; a 72-cell matrix (pathway × agent × manifest-strategy × project) that
  selected the integrated architecture above. A model-efficiency precursor fixed
  the prompting shape (one batched, cached, temperature-0 call).
- Precision-correction scaling alpha loop (`backend/tests/command_center/alpha/`):
  pits four precision-editing techniques against each other as documents grow;
  concluded a deterministic diff/edit-tagger matches the LLM with zero drift, so
  a trained micro-model is not needed under the air-gap/no-training constraints.
- Synthetic growth-dataset generator (`backend/tests/command_center/datagen/`):
  deterministic, air-gap-clean, domain-matched documents at increasing sizes
  with a known edit ledger, for the scaling study.

**Documentation + tests**
- `docs/testing/`: modular write-ups of all three bake-offs and the dataset
  generator, with method, metrics, results, and the recommended architecture;
  linked from the Developer Guide.
- `backend/tests/test_corrections_refine.py`: grounding + refinement coverage
  (prose→filename, vague→no-op, idempotence, conflict preservation,
  determinism, both pathways across all six projects). The suite is pinned
  offline/deterministic by default via a conftest fixture.

### Changed
- The Correction Pipeline now routes loaded corrections through the refinement
  step before reconciliation. The deterministic baseline is unchanged when no
  refinement applies.

## [0.1.0] - 2026-09-28

First tagged release: a local-first, offline document aggregation and correction
platform with a document-driven correction pipeline, an async worker, a React
frontend, and an air-gapped RHEL/UBI container stack.

### Added

**Correction pipeline**
- Four-stage correction model: original corpus -> first attempt -> comments ->
  corrected intermediate JSON.
- Reconciliation engine that resolves each unit at the smallest granularity
  (field / graphic / table cell / furniture element) with a status
  (`unchanged` / `filled` / `corrected` / `needs_review` / `conflict`) and full
  provenance (which corpus doc, which correction, which rule).
- Guardrails: values are literal fills from corpus spans (never computed);
  disagreeing corrections become a `conflict` with all candidates preserved and
  no auto-resolution; required-but-unsupported fields become `needs_review`
  rather than fabricated.
- Multi-round convergence view (last-good-wins across correction rounds).

**Document-driven projects**
- Projects are driven by real documents: a well-formatted template DOCX is the
  authoritative rubric, and the build discipline (formatting/placement rules) is
  learned from the template document's own evidence, falling back to a pinned
  `gov_standard` profile only where the template is silent (no silent defaults).
- The first-attempt draft is a real DOCX (with seeded defects) converted through
  the same production path clients would use.
- Real PNG figures with a managed naming convention, a centered title baked into
  each image, and controlled size/position, tracked across template, draft, and
  the final intermediate JSON, and inspected against the template-derived rubric.
- Five projects (telemetry incident, security incident, lab-safety event, QC
  defect, reliability failure), each with corpus docs, figures, corrections, and
  a template + draft.

**Document ingestion + search**
- DOCX/PPTX/PDF/TXT/MD/PNG ingestion into a canonical JSON representation with
  immutable original bytes (SHA-256), per-block parser provenance, and resolved
  effective date-time group.
- OOXML style-chain resolution (run -> paragraph style -> based-on -> docDefaults
  -> theme) so inherited formatting is observed, plus table/figure/header/footer
  extraction and image size/alignment/title capture.
- Hybrid search: local vector embeddings + lexical overlap fused with Reciprocal
  Rank Fusion, returning OpenSearch-shaped hits with provenance.
- Export of the assembled report to JSON / Markdown / DOCX / PPTX / PDF.

**Async worker + job queue**
- SQLite-backed job queue behind an interface (Postgres-ready), with atomic
  claim (SKIP-LOCKED-equivalent), status lifecycle, and retries.
- Templated worker loop dispatching composable ops (reconcile / converge /
  render_geometry / pipeline).

**Deployment**
- Three modular RHEL/UBI images built with BuildKit: `api` (lean FastAPI, no
  embedding stack, ~1.7GB), `worker` (LibreOffice + pinned metric-compatible
  fonts + CPU-only torch, ~835MB), `frontend` (nginx serving the built React
  app), plus a shared data volume. `docker-compose.yml` wires them together.
- Split dependencies (`requirements-api.txt` / `requirements-worker.txt`) so only
  the worker carries the heavy embedding stack.
- Air-gap posture: offline by default (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`,
  hashing embedder), OpenShift-compatible non-root group-0 file ownership.

**Frontend**
- React + Vite SPA with five tabs: Correction Pipeline, Ingestion, Supplementals,
  Search, Report & Export.
- Accessibility toolkit (`frontend/a11y/`): axe-core WCAG 2.1 A/AA plus
  keyboard/focus checks (reachability, focus-visible, tablist roving) driven
  through nine interactive UI states; runnable as `npm run audit:a11y`.

### Fixed
- Conflict fields no longer display a misleading resolved value: on a `conflict`
  the value is blanked (the original wrong value is retained as `original_value`
  for audit) and the UI shows "unresolved - choose a candidate below".
- Reconciliation output no longer leaks internal telemetry to reviewers: raw
  rule/retrieval strings and correction IDs are demoted to a hover; prose rows
  are labeled per section instead of a repeated "Body"; footer/table internal
  tokens render as human-readable text; candidate author emails are shortened.
- nginx `/api` proxy: fixed startup on the compose network (resolver + variable
  upstream) and prefix stripping so proxied API paths resolve.
- Accessibility: added visible focus rings, keyboard-operable flow boxes, an ARIA
  tablist with arrow-key roving, aria-live status regions, higher-contrast muted
  text, accessible names on selects, and keyboard-focusable scrollable regions.
- Per-component scrollable report sections so large real documents stay usable.

### Notes
- Local-first and offline: no AWS, OpenSearch, or external AI service is required.
  Cloud pieces (e.g. a Bedrock feedback interpreter) are optional adapters behind
  the same interfaces and are disabled by default.

[0.2.0]: https://example.com/releases/0.2.0
[0.1.0]: https://example.com/releases/0.1.0
