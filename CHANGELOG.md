# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
