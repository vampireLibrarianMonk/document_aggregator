# Alpha Loop + Optimization — Comprehensive Summary (as of 2026-10-19)

A single-page map of everything built and proven this session, for anyone
picking it up. Detailed running notes live in `SESSION_HANDOFF.md`.

## 1. App fixes (production, shipped)

- **Worker/queue visibility + "processing…" fix.** Batches are async jobs drained
  by a separate worker; when none ran, the UI hung. `GET /jobs/{id}` now returns
  `worker_id`; a `WorkerQueuePanel` dropdown shows live queue counts, the active
  job, the worker, and an explicit "no worker" warning. Progress reads
  "N of M files completed · P%".
- **Approval-card UX.** Contained focus ring, scrollable reject form + mismapped
  checklist, scrollable mapping table (sticky header) + summary, scrollable
  shapes list, explicit "approval is per-shape, never cascades" note.
- **White-box perf instrument.** `app/json_alignment/perf_spans.py` + stage
  spans in `run_alignment` (zero-overhead when inactive). 55 alignment/batch
  tests pass; frontend tsc + 30 vitest + oxlint clean.

## 2. The Adversarial Alpha Loop (`backend/alpha_loop/`)

Fires JSON at the LIVE API and red-teams the JSON→golden approve/reject pipeline.
Reuses `tests/alpha_frontier` mutation axes + truth model as the correctness
oracle. Agents: A (mapping correctness + no-fabrication), B (cascade/replay
invariants), C (file-processing performance). The red team RUNS CLEAN (verified;
an earlier interruption was of separate research sub-agents, not this code).

CLI (from `backend/`, API+worker up):
  `python -m alpha_loop stages | step --stage N | run --from N | scale ... | widesweep ... | perf ...`

Ladder progress (step mode, slow-walk):
  stages 0–3 PASS (clean / shared-rename / synonym / nesting): 7–8/8 recovered,
    0 false matches, cascade+replay invariants hold.
  stage 4 (misspell@s3): safe refusal (required fields ungroundable → review, no
    fabrication). Human-in-the-loop by design; not an engine fault.
  stages 5–11: being walked now.

## 3. Optimization chase — fuzzy matcher (bench: `perf/`, `optbench.py`)

Cost model: infer_mapping time ≈ fixed overhead + k·(distinct clusters); the only
JSON-size driver is field WIDTH (O(width²)); record count / file count / nesting
depth are flat. Hot stage was Levenshtein in the fuzzy tier (call-count bound).

All variants gate-verified IDENTICAL mappings (frontier + wide + realistic):

  technique                              realistic@w400   synthetic@w400   verdict
  baseline                               1.0x             1.0x
  banded Levenshtein alone               0.8x             —                reverted
  bigram prefilter                       2.6x             —                superseded
  index + cascade (bitmask+bigram memo)  12x              4.6x             superseded
  + rapidfuzz C++/SIMD                    14x              4.8x             ceiling probe
  batched cdist SIMD+MT                   8x               —                reverted (lost index)
  TIGHT bigram-filtered index + cascade  26x              66x              CHAMPION (zero-dep)
  + cached _score_candidate (cycle 3)    noise            —                rejected

Even on the 8-field game schema the tight index is identical + 2.5–5x faster.

CHAMPION = `infer_mapping_indexed_tight` + `fuzzy_candidates.cascade_cached`,
zero dependency. rapidfuzz adds only ~7% (tight index prunes so hard the C++ DP
rarely runs) → not worth the air-gap dependency cost.

Transcript techniques exhausted: short-circuit, bitmask+popcount, precompute-
once, fewer-ops index (the big win), SIMD+parallel (tried, underperformed).
Lesson confirmed: doing LESS work beat doing work faster.

## 4. Still prototype (not promoted)

The champion lives in the apparatus, NOT in production `mapping.py`. Promotion
(tight candidate index + bigram-cascade fuzzy into `infer_mapping`/`_fuzzy_score`)
is the next real payoff, gated on the full `pytest` + frontier suites. Remaining
profile cost is in production scoring internals (per-call set rebuild, cosine
genexpr, `_jaccard`) — lower return, higher risk, a deliberate prod change.

## 5. Next

- finish stages 5–11 (slow walk)
- async/worker deep dive (queue, concurrency, claim semantics, overhead floor)
- promote the tight index to production
