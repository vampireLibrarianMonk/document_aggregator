# Session Handoff — Document Aggregator / Alpha Loop

Last worked: 2026-10-19. Pick up from here.

## How to resume the running stack

Local run (NOT docker). Port 8000 is taken by the user's other app, so:

- **Frontend (Vite dev):** http://localhost:5173
- **API (uvicorn):** http://localhost:8001
- **DATA_DIR:** `./.local_data` (throwaway local store)

Start commands (run from repo root `/home/flaniganp/PycharmProjects/document_aggregator`):

```bash
# API on 8001
DATA_DIR="$PWD/.local_data" nohup .venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8001 --app-dir backend > /tmp/docagg_api.log 2>&1 &

# Worker (REQUIRED for batch/async jobs; concurrency 2)
DATA_DIR="$PWD/.local_data" WORKER_CONCURRENCY=2 EMBEDDING_BACKEND=hashing \
  nohup .venv/bin/python backend/worker_main.py > /tmp/docagg_worker.log 2>&1 &

# Frontend (proxies /api -> :8001)
cd frontend && VITE_API_TARGET="http://localhost:8001" nohup npm run dev \
  > /tmp/docagg_fe.log 2>&1 &
```

Health checks: `curl -s http://localhost:8001/health`, `curl -s http://localhost:8001/jobs`,
`pgrep -af worker_main`.

NOTE: background processes started inside a tool shell can die; if the API/worker
is gone on resume, just re-run the commands above. The venv already has
`requirements-api.txt` + pytest installed.

## What we changed in the app (committed to disk, verified)

1. **Worker-visibility + "processing…" fix** (the original bug: batch stuck at
   "processing…" because no worker was running).
   - `backend/app/main.py`: `/jobs/{id}` now returns `worker_id`.
   - `frontend/src/api/types.ts`: added `worker_id` to `JobStatus`, added
     `JobCounts`.
   - `frontend/src/api/client.ts`: added `getJobCounts()` (GET /jobs).
   - `frontend/src/components/JsonAlignment.tsx`:
     - `batchProgressLabel` now reads "N of M files completed · P%" (fallback
       "waiting for a worker…").
     - New `WorkerQueuePanel` dropdown below the batch pane (live queue counts,
       active job detail, worker id, stalled-queue warning).
   - Tests updated: `frontend/src/components/__tests__/batchProgress.test.tsx`.

2. **Approval-card UX fixes** (JsonAlignment.tsx):
   - Reject form: contained focus ring (styles.css), consistent spacing,
     scrollable labeled region, scrollable mismapped-fields checklist.
   - Approval mapping table: scrollable (maxHeight 360, sticky header) + a
     "N mapped, M need review" summary.
   - Whole provisional-shapes list: scrollable (maxHeight 640).
   - Added an explicit note: "Each shape is approved independently — approving
     one does NOT cascade." (Confirmed in code: approve/override are per-profile.)
   - `frontend/src/styles.css`: `input/textarea/select:focus-visible` ring with
     `outline-offset: -1px`.

3. **White-box perf instrument** (behavior-neutral, zero-overhead when off):
   - NEW `backend/app/json_alignment/perf_spans.py` — contextvar span timer.
   - `backend/app/json_alignment/pipeline.py`: `run_alignment` stages wrapped in
     `span(...)` (profile_source / extract_target / infer_mapping /
     apply_semantic_tier / execute_mapping / validate). No behavior change.
   - Verified: 55 alignment/batch tests still pass.

Frontend: `npx tsc -b` clean, 30/30 vitest pass, oxlint 0 errors.

## The Adversarial Alpha Loop apparatus (NEW)

Location: `backend/alpha_loop/` — fully documented in `backend/alpha_loop/README.md`.

Purpose: fire JSON at the LIVE API, red-team the JSON->golden approve/reject
pipeline, expand until break, then learn. Reuses the proven
`backend/tests/alpha_frontier/` mutation axes + threshold-independent truth model
as the correctness oracle (do NOT reinvent that).

Modules:
- `client.py`     thin live-API client (fires at :8001, times queue/worker)
- `generator.py`  Protagonist — 12-stage benign->hostile ladder over alpha_frontier axes
- `agents.py`     red team: A correctness, B invariants/cascade, C performance
- `decider.py`    spiral-out policy (approve >=90% conformity & no needs_review; remap; reject; hold)
- `controller.py` orchestrator: `run_stage` (mode a), `run_to_break` (mode b), `run_scale`
- `__main__.py`   CLI
- `perf/`         A/B bench: probes.py (instrument), variants.py (swappables), experiment.py (correctness-gated A/B)

Run from `backend/` with API+worker up:
```bash
cd backend
../.venv/bin/python -m alpha_loop stages
../.venv/bin/python -m alpha_loop step --stage N --n 3          # mode a (one rung)
../.venv/bin/python -m alpha_loop scale --shapes 0,1,2 --files-per-shape 5 --records 50
../.venv/bin/python -m alpha_loop perf --stage 7 --semantic     # A/B hashing vs MiniLM
```

Agreed operating mode: **(a) step/confirm now**, graduate to **(b) run-to-break**
at convergence. User is deliberately slow-walking the rungs.

## Progress through the ladder (all live fires)

- stage 0 benign_clean      → 8/8 recovered, auto-approved, no cascade, clean replay. PASS
- stage 1 rename_shared     → 7/8, remapped genres<-genre_list[]. PASS
- stage 2 rename_synonym    → 7/8, remapped publisher<-label. PASS
- stage 3 nesting           → 7/8, remapped publisher<-level1.level0.label. PASS
- stage 4 misspell@s3       → FIRST BREAK: keys too corrupted, zero required fields
                              grounded, routed to `review` with NO profile (safe refusal,
                              no fabrication). Decider correctly `hold`.
- stages 5-11               → NOT YET FIRED.

## Key findings

- **Cascade is per-shape** (verified in code AND live): approving one shape never
  touches others; same shape re-fired replays clean, zero re-prompt.
- **No-fabrication holds**: zero false_matches across all rungs; matcher abstains
  rather than guess. Value evidence can veto/corroborate/re-rank a mapping but
  NEVER grounds a pair or rewrites a value (confirmed in `mapping.py`
  `_score_candidate` / `infer_mapping`).
- **Scaling cost model** (from `scale` sweeps): time ≈ fixed ~0.21s batch/job/
  worker overhead + k·(distinct clusters). Records/file = FLAT (3→500 same).
  Many files same shape = FLAT (clustering collapses to 1 inference). The only
  real driver is DISTINCT SHAPE count, and even 11 clusters stayed under the
  0.21s overhead floor. The live path is overhead-bound, not compute-bound.
- **Hot stage** (white-box spans, in-process): `infer_mapping` ≈ 73% of pipeline
  time (~1.8ms). That's the optimization target, only visible in-process (the
  `perf` bench), not on the live path (hidden under the 0.21s job overhead).

## USER DECISIONS / BOUNDARIES (honor these)

- Do NOT build a value-first proposer / let input values inform field mapping
  when KEYS are malformed — user does not expect garbled-key inputs; don't
  engineer for errors we don't expect. (Parked permanently unless user revives.)
- Correctness is the HARD GATE for perf; speed only counts if the mapping outcome
  is identical (`experiment.py` enforces this).
- Fire against the LIVE API always (the whole point).

## OPEN THREAD — where we stopped (next session starts here)

### DONE this session: size-factor + perf optimization

**Size factor fully measured** (via new `backend/alpha_loop/wide.py` + `widesweep`
CLI, in-process to isolate from the 0.21s job overhead):
- records/file, #files (same shape), nesting depth → ALL FLAT.
- **field WIDTH is the only cost driver: O(width²).** 8 flds≈1ms, 100≈190ms,
  400≈4.5s. Width×depth are orthogonal (no interaction).

**Profiler found the real hotspot:** `_levenshtein` in the fuzzy tier = 72% of
infer_mapping time (432k calls at width 400). NOT the candidate scan.

**Perf optimization prototyped + proven** (`backend/alpha_loop/perf/infer_indexed.py`,
variants `infer:baseline` vs `infer:hash-indexed`):
- Attempt 1 candidate hash-index: 1.1x (length window couldn't prune).
- Attempt 2 banded Levenshtein: 0.8x SLOWER (cost is call COUNT not length).
- Attempt 3 **character-bigram lower-bound prefilter on fuzzy tier: ~3x faster**
  (2.4x@w50 → 2.95x@w400), CORRECTNESS-GATE PASS (identical mappings on 150
  frontier cases + wide sweep + game schema). This answers the user's "string
  hashing method" question: YES, a bigram hash-set lower bound on edit distance.

**Perf optimization prototyped + proven** (`backend/alpha_loop/perf/`,
`optbench.py` is the reusable correctness-gate + speed harness):

Obsessive iteration (transcript-driven; every variant gate-verified IDENTICAL
mappings on frontier + wide + game + realistic schemas):
- banded Levenshtein alone: 0.8x SLOWER (per-call overhead on short strings) — REJECTED
- count-L1 lower bound: 1.7x — superseded
- bigram set lower bound: 2.6x — superseded
- **CHAMPION = index + cascade fuzzy** (`infer_indexed.py` +
  `fuzzy_candidates.py::cascade_cached`): cheapest-first SOUND lower-bound
  cascade len -> 26-bit char-presence bitmask popcount(XOR)/2 -> bigram-set
  bound -> banded Levenshtein, with per-string signatures MEMOIZED, plus a
  candidate-shortlist index in infer_mapping. Speedups:
    synthetic uniform-length (index's WORST case): ~4.6x @ w400
    realistic varied-length/diverse-token names:   ~14x  @ all widths
  (synthetic understates because uniform lengths defeat the length filter; real
  field names prune hard — candidate set 360 -> ~47-187.)
- enum-vocab memo layered on top: noise/negative — REJECTED.

Exhausted transcript techniques: short-circuit, bitmask+popcount, precompute-once,
fewer-ops index. NOT pursued (pure-Python ceiling): SIMD/compiler unroll and
in-infer parallelization. A C-ext / rapidfuzz variant could break past the
ceiling but is a dependency decision (air-gap posture) — left for user.

### NOT YET DONE (next steps)

1. **Promote the index+cascade fuzzy optimization to production** `mapping.py`
   (`infer_mapping` + `_fuzzy_score`). Prototype lives in
   `backend/alpha_loop/perf/infer_indexed.py` + `fuzzy_candidates.py::cascade_cached`.
   MUST re-run full `pytest` + frontier after. Get user go-ahead.
2. Resume the stage ladder at **stage 5** (typecoerce) if going back to the loop.
3. Still NO `new_spec` dataset found; synthetic wide generator fills the gap.

### Prior open items (still relevant)

- User ruled OUT a value-first proposer for malformed KEYS (don't engineer for
  unexpected errors). Honor this.
- Correctness is the HARD GATE for all perf work.

## Older notes (size-factor investigation, superseded by DONE above)

User asked about the **"file length factor" for BIGGER JSONs** and mentioned a
`new_spec` directory with datasets.

- **Searched: there is NO `new_spec` dir and NO project-owned large JSON dataset.**
  Only venv library JSON exists. The dataset the user remembers is not in this
  repo (maybe a sibling project under ~/PycharmProjects, or a different context).
  ASK THE USER where it is, or synthesize.
- The real "length factor" is NOT records-per-file (proven flat). It is
  **record WIDTH + DEPTH**: field count and nesting. `infer_mapping` is
  O(source_fields × target_fields), so a 200-field record vs a 200-field schema
  = 40k candidate scorings/cluster. Our 8-field game schema CANNOT reveal this.

### Proposed next step (not yet built, awaiting go-ahead)

Add a wide/deep synthetic generator: a `--width` (field count) / `--depth`
(nesting) knob producing a large golden schema + matching mutated records, then
sweep JSON SIZE while holding file-count fixed, measuring the `infer_mapping`
span. This answers "how does it scale with JSON size" — deterministic, offline,
no dataset download needed. Alternatively: point the generator at a REAL large
JSON if the user supplies a path.

5000-files is a throughput sample (we showed throughput is cheap/clustered);
bigger JSONs are the per-cluster inference cost (untested). They are orthogonal.

## Environment notes

- No network/fetch tool available; stack is offline by design. Can't download
  datasets — synthesize or user-provides.
- sentence-transformers MiniLM DOES load locally (384-dim); the perf A/B
  `embed:sentence-transformers` variant works, but on easy stages both backends
  produce identical outcomes (contrast only appears on hard stages 7/9).
- Pre-commit tools (ruff/mypy) not in venv; code byte-compiles + tests pass.

## Ceiling chase (rapidfuzz C++/SIMD) — DONE

Added rapidfuzz 3.10.1 (MIT) variants + a cdist-batched (SIMD+multithread)
prototype. All gate-PASS (identical mappings). Definitive numbers (9 reps):

  profile            py-champ(zero-dep)   rf-champ(C++)   batched-cdist
  synthetic worst    4.6x                 4.8x            (n/a)
  REALISTIC w400     11.9x                14.2x           8.3x (lost the index)

Verdict: the CEILING is ~14.2x (index + rapidfuzz Levenshtein). Pure-Python
index+cascade hits 11.9x = ~84% of ceiling with ZERO dependencies. rapidfuzz
adds only ~20% and only on realistic data; batched cdist UNDERPERFORMED because
it dropped the index (fuzzy was no longer the bottleneck — per-pair
_score_candidate overhead is).

RECOMMENDATION (not yet implemented): keep pure-Python cascade as the production
path (air-gap/zero-dep posture); make rapidfuzz an OPTIONAL accelerator used if
importable, clean fallback if not. rapidfuzz is NOT yet in requirements; it was
pip-installed into the local venv only for this measurement.

Files: perf/fuzzy_candidates.py (rapidfuzz_plain, rapidfuzz_cascade),
perf/infer_batched.py (cdist prototype), optbench.py (the gate+speed harness).

## Chase cycles 2 (more optimization) — DONE

Re-profiled after rapidfuzz: the top cost moved from Levenshtein to the sheer
NUMBER of _score_candidate calls (417k @ w400), driven by the fuzzy length-
window index pulling ~160 of ~210 candidates/target (same-length but token-
disjoint fields that can only match via fuzzy).

BREAKTHROUGH: _SourceIndexTight + infer_mapping_indexed_tight
(backend/alpha_loop/perf/infer_indexed.py). Moves the SOUND bigram lower-bound
AHEAD of the scoring loop: a length-window candidate is admitted only if its
bigram overlap with the target could allow dist<=budget. Cuts _score_candidate
calls directly. Gate PASS over 164 cases (frontier+wide+realistic).

Results (9-rep medians, tight index + pure-Python cascade_cached fuzzy):
  SYNTHETIC worst-case: 4.6x -> 67.8x  (the length-window bloat was its whole problem)
  REALISTIC w400:       12x  -> 25.2x
With rapidfuzz on top: ~28x realistic, ~67x synthetic (rapidfuzz now barely
helps / sometimes hurts, because the tight index prunes so hard few pairs reach
the DP). => pure-Python tight index essentially REACHES the ceiling alone.

REJECTED this round:
  - cascade_joincache with id(tgt) cache: UNSOUND (object-id reuse after GC
    returned stale target entries) — gate caught a wrong result. Fixed to key by
    the target token-tuple; now PASS but NO speed benefit atop the tight index,
    so not adopted.
  - batched cdist: still slower (lost the index).

CURRENT CHAMPION: infer_mapping_indexed_tight + cascade_cached fuzzy
  = ~25x realistic / ~68x synthetic, ZERO dependency, correctness-gate PASS.
Promotion-to-production candidate (mapping.py infer_mapping + _fuzzy_score +
the tight candidate index). Still prototype-only; gate on full pytest+frontier.

## Chase cycle 3 (final) — REJECTED, champion confirmed

Cycle 3 tried caching _score_candidate's per-source-invariant rebuilds
(set(tokens), join, bag) keyed by token tuple. Gate PASS but speed was NOISE
(19x vs 18x @w100; slightly WORSE @w400): the tight index already prunes so hard
that few _score_candidate calls remain, so per-call micro-caching cancels out.
REJECTED (fast-but-not-faster). Prototype kept as make_score_candidate_cached /
infer_mapping_tight_scored for reference but not adopted.

FINAL definitive numbers (11-rep medians):
  SYNTHETIC w400: CHAMPION 66.4x | +rapidfuzz 67.0x (rapidfuzz adds nothing)
  REALISTIC w400: CHAMPION 25.9x | +rapidfuzz 27.8x (rapidfuzz adds ~7%)

FINAL CHAMPION = infer_mapping_indexed_tight + cascade_cached fuzzy, ZERO
dependency, correctness-gate PASS on 164 cases. rapidfuzz is NOT worth the
dependency (the tight index prunes so aggressively the C++ DP rarely runs).

Optimization chase CLOSED at diminishing returns. Remaining profile cost is
inside production mapping.py internals (per-call set rebuild, cosine genexpr,
_jaccard) — lower return, higher risk; a deliberate production change, not more
apparatus prototyping. Next payoff = promote the tight index to production
(full pytest+frontier gated).

## Ladder slow-walk: stages 5-7 — DONE (all PASS)

- stage 5 typecoerce:   8/8 recovered, 0 false, auto-approved, cascade+replay OK.
                        (type coercion didn't hurt: keys stayed recognizable.)
- stage 6 noise_decoys: 8/8 recovered, 0 false, auto-approved. Decoys
                        (internal_id/_scraped_at) correctly NOT mapped (no-fab).
- stage 7 rename_abbrev: 1/8 recovered, 7 missed, 0 FALSE. Abbreviations (nm/yr/
                        dvlpr/pub/hw/gnr/cs) are too token-sparse to ground
                        confidently; matcher ABSTAINED (no fabrication), Decider
                        remapped 7 from gold truth. Honest matcher limit, correct.

Red team confirmed RUNNING CLEAN (does not crash). Tight-index optimization
confirmed identical + 2.5-5x faster even on the 8-field game schema (end-to-end).
Stages 8-11 remaining.
