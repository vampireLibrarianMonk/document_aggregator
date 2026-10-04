# Governor: decomposed, verifiable document generation

## Why

The model evaluation (`MODEL_EVAL.md`) proved that single-shot generation never
produces a valid project on the first try (0% across all models and prompts),
and that small models truncate the one-giant-JSON contract and fall back ~92% of
the time. Prompting did not help. The evidence points at one remedy: **decompose
the work into small, bounded, individually verifiable steps** so each step fits
even a small model, and each step is checked before the next.

The governor is that orchestration layer. It does not replace the fast
single-shot generator; it sits **alongside** it as a higher-quality mode.

## Non-negotiable constraints (unchanged)

- **Air-gap / offline first.** Everything degrades to the deterministic generator
  with no Bedrock.
- **Determinism of the artifact.** The model is an AUTHOR only. Every step's
  output is validated; the final project is persisted as fixed JSON and the
  deterministic engine remains authoritative at runtime. A governed project is
  as reproducible as a hand-authored one.
- **Approved models only** (Nemotron / GPT-OSS allowlist).
- **No fabrication.** The proofread step strengthens, not weakens, the existing
  grounding guarantees.

## What already exists (we build ON these, not around them)

- `jobs/queue.py` — `JobQueue` protocol + `SqliteJobQueue` (atomic claim, retry,
  priority, Postgres-ready). `jobs/worker.py` — generic `Worker` loop dispatching
  named `WorkerOp`s via `register_op`. `jobs/runtime.py` — shared queue singleton.
- `projectgen/schema.py` — `ProjectSpec`, `validate_spec`, `salvage_spec`.
- `projectgen/bedrock_gen.py` — `BedrockProjectGenerator` (per-model profile +
  prompt), `generate_with_metrics` -> `GenerationResult(spec, metrics)`.
- `projectgen/model_profiles.py` — size-aware budgets + recommended prompt.
- `projectgen/persist.py` — `persist_spec` (validate + write + build assets).
- `projectgen/rule_generator.py` — the deterministic fallback.

## The governed pipeline (state machine)

A document job is decomposed into ordered, bounded steps. Each step is small
enough for a small model and is validated before the next runs.

```
PLAN        model proposes the section skeleton (headings + which need a
            table/graphic/fields). Small output -> small models can do it.
            Validate: non-empty, unique keys, sane count. Else deterministic plan.
   |
FILL[i]     for each planned section, fill ONLY that section (fields + body +
   |        table/graphic refs) grounded in the corpus. One section per call =
   |        small output, no truncation. Validate per section; retry-or-downshift.
   v
PROOFREAD[i] re-read each filled section AGAINST the corpus: every asserted value
            must be grounded (verbatim in corpus or stated in a correction body),
            else mark needs_review or drop. This is the content check the current
            pipeline lacks (today only schema validity + salvage).
   |
RECONCILE   assemble the validated sections into a ProjectSpec, run the existing
            validate_spec + salvage_spec, then persist_spec. Deterministic.
   |
DONE        project_id, with the full step log attached.
```

Every step records objective metrics (reusing `RunMetrics`): model, prompt,
tokens, latency, est $, outcome, fabrications caught.

## Governor responsibilities (what makes it a "governor")

1. **Sequencing** — owns the step order and the per-section loop.
2. **Budgeting** — a per-step token/attempt budget (from `model_profiles` +
   governor policy). A step that exceeds its budget is retried or downshifted,
   never allowed to run away.
3. **Retry-or-downshift policy** — on a failed/invalid step: retry once on the
   same model; if still bad, DOWNSHIFT to a cheaper/smaller model for that step
   (decomposition makes small models viable per section); if still bad, use the
   deterministic result for that step. The job NEVER hard-fails -- worst case is
   a fully-deterministic project, exactly today's floor.
4. **Accounting** — aggregate per-step metrics into a job-level report.
5. **Progress events** — emit a structured, append-only event per step
   transition for the live log (task 7).

## Where it runs

- IMPLEMENTED: a `generate_document` WorkerOp (`jobs/ops.py`) runs the governor
  and stores the full event log + run summary as the job result, so a governed
  job is enqueued via `POST /jobs` and polled via `GET /jobs/{id}` using the
  existing generic job API (no governor-specific endpoints needed). The governor
  logic stays in the `governor/` package the op calls; it is unit-tested without
  the queue.
- IMPLEMENTED: a synchronous entry (`run_governed(brief, ...)`) for tests and the
  live SSE endpoint (`GET /project/governed/stream`), plus the async enqueue
  path above for backgrounded runs. The op accepts `per_section` to select
  per-section authoring and degrades to the deterministic/offline path on any
  model failure, so a backgrounded job never hard-fails.

## Progress / streaming (task 7 preview)

Each step appends a `GovernorEvent{ts, step, status, model, prompt, detail,
metrics?}` to a per-job event log (persisted with the job). The API streams these
(SSE) so the frontend shows a live cumulative log: "planned 5 sections",
"filling 'Signal Definitions' with gpt-oss-20b", "proofread: 2 values unmatched
-> needs_review", "reconciled -> project_7". Offline: same events, deterministic
models, no Bedrock.

## Auto-pick (task 8 preview)

`recommend_model()` reads the eval results to pick the default (currently
gpt-oss-120b: lowest fallback, cheap, fast) and exposes WHY. The governor uses
the recommended model for PLAN, and may downshift per-section for FILL. The user
can always override in the existing picker.

## The adjudicator (pluggable decision layer)

Research (`GOVERNOR_RESEARCH.md`) reframed the design around separating the
AUTHOR (the generation model) from the ADJUDICATOR (what makes the governor's
control decisions). The adjudicator is a small, pluggable interface so we can
measure three implementations against each other -- all within the
Nemotron/GPT-OSS allowlist, no new vendor model.

### The decision

At each checkpoint (after PLAN, after each FILL, after each PROOFREAD) the
governor asks the adjudicator for a **bounded, typed verdict** about a unit:

```
Verdict = accept | needs_review | retry | downshift | reject
```

plus a `confidence` in [0,1] and a short reason. The governor acts on the enum
directly -- no second fragile JSON parse.

### The interface

```python
class Adjudicator(Protocol):
    name: str
    def judge(self, decision: Decision) -> Verdict: ...
    # Decision carries: kind (plan|fill|proofread), the unit under review,
    # the corpus (ground truth), and the step's RunMetrics so far.
```

### The three implementations (all approved-model-only)

- **DeterministicAdjudicator** -- the existing `validate_spec` + corpus-grounding
  checks produce the verdict. No model, zero cost, fully offline. This is also
  the GROUND TRUTH the others are scored against.
- **GeneratorJudgeAdjudicator** -- the same capable LLM that fills also decides,
  via one extra Converse call. Simplest; most tokens; the "no separation"
  baseline.
- **DecisionLayerAdjudicator (JEV-style)** -- a small/cheap approved model
  (e.g. gpt-oss-20b or a nano Nemotron) is prompted to return ONLY a typed
  verdict + confidence over the bounded options. Accept-when-confident; when
  confidence is below a threshold, escalate to the deterministic check (or a
  stronger model). Cheap per decision; the research-backed cost shape.

Adding a fourth adjudicator = one new class implementing `judge`. The governor
never changes.

### Confidence-gated escalation (cascade)

`DecisionLayerAdjudicator` holds a confidence threshold. Below it, the verdict is
not trusted and the governor escalates (default: fall through to the
deterministic adjudicator, which is free and authoritative). This bounds the
blast radius of a miscalibrated cheap judge: worst case, we pay for one cheap
call and then use the deterministic truth anyway.

### Decision quality is measurable

Because the deterministic adjudicator is ground truth, we can score a model-based
adjudicator's calibration: precision/recall of its `needs_review` / fabrication
verdicts versus what the validator actually finds. A cheap judge is only worth it
if it agrees with ground truth often enough. The governor-eval harness measures
exactly this.

## Explicitly out of scope (for now)

- True token-by-token streaming of model output to the UI (we stream STEP events,
  which is what "watch operations transpire" needs; token streaming is a possible
  later UX nicety).
- Changing the deterministic engine or the correction path.
- New infrastructure (no broker, no new datastore) -- reuse the SQLite queue.

## Build order

1. `governor.py` — the state machine + policy, synchronous, unit-tested with the
   deterministic generator (no Bedrock needed to prove the decomposition logic).
2. Per-section fill + proofread helpers on top of the existing generator seam.
3. The `generate_document` WorkerOp + API enqueue/status.
4. Streaming events + frontend live log (task 7).
5. `recommend_model` + picker default (task 8).
```
