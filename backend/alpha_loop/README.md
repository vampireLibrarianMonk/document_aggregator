# Adversarial Alpha Loop

A modular apparatus that fires JSON files at the **live** stack and red-teams
the JSON→golden approval/rejection pipeline (the `json_alignment` workflow). It
exists to **expand until we break, then learn from the break** — grow the corpus
to more files and more shapes, prove clean cascade behavior, and locate the
severity at which the matcher (or the loop's own decisions) first goes wrong.

Everything runs against the running API + worker over HTTP. That is deliberate:
we measure what a real client experiences, including the async job queue and
worker handling.

## Why it reuses `tests/alpha_frontier/`

The repo already ships a battle-tested, adversarially-reviewed malformation
generator and a **threshold-independent ground-truth model**
(`recovered` / `missed` / `false_match`) in `backend/tests/alpha_frontier/`. We
do **not** reinvent that oracle. The alpha loop:

- **reuses** the frontier's mutation axes (`rename`, `misspell`, `nesting`,
  `noise`, `typecoerce`, `split_merge`, `conflict`, `junk_leak`) as the shape
  generator, and its truth map as the correctness oracle; and
- **adds** what the frontier harness intentionally does not do — fire against
  the **live** endpoints, run the **approve/remap/reject** loop, check
  **cross-round cascade/replay** invariants, and measure **performance**.

## The agents

| Agent | Role |
|-------|------|
| **Generator / Protagonist** (`generator.py`) | Turns a staged benign→hostile ladder of `(axis, severity)` picks into fireable batch documents, carrying per-doc ground truth. |
| **Decider** (`decider.py`) — *under test* | The spiral-out auto-decision policy: `approve` when conformity ≥ bar and no needs-review; `remap` a needs-review field from gold truth (a reorg, never a value rewrite); `reject` quarantined/irrelevant shapes; else `hold`. |
| **A · Correctness** (`agents.py`) | Scores the learned mapping vs ground truth (recovered/missed/false_match) and enforces the **no-fabrication** line (no conformed value was invented). FAIL = a required-field false match or any fabricated value. |
| **B · Invariants** (`agents.py`) | Cross-round structure: approving one shape must **not cascade** to others; the **same shape re-fired must replay clean** with zero re-prompt. |
| **C · Performance** (`agents.py`) | File-processing throughput, queue wait, worker id, per-file budget. FAIL = over budget; detail carries a concrete tuning suggestion (worker concurrency, chunking, where the batch op serializes). |

A **FAIL** from any red-team agent is a "break": the controller stops so we can
dissect it and learn (add an accepted value, declare a synonym/alias, reorg a
path — never rewrite a value), then replay to confirm the fix.

## Modes

- **(a) step** — fire ONE stage, print metrics + every red-team verdict, then
  stop. We escalate together between rounds. *This is where we start.*
- **(b) run-to-break** — escalate through the ladder autonomously on all-PASS,
  stopping at the first FAIL. Enabled once we trust the verdicts at convergence.

## Isolation

The loop creates a **dedicated throwaway `json_alignment` project** and sets the
project-7 golden game schema on it. The user's real project 7 is never touched.

## Spin-up

Prerequisites: the API running (default `http://localhost:8001`) and a worker
draining the queue (`backend/worker_main.py`). From the `backend/` directory:

```bash
# list the benign -> hostile ladder
python -m alpha_loop stages

# (a) step: fire ONE stage, print the red-team verdicts, stop
python -m alpha_loop step --stage 0 --n 3

# (b) run-to-break: escalate until a red-team FAIL
python -m alpha_loop run --from 0 --n 3
```

Options: `--base URL`, `--n RECORDS_PER_SHAPE`, `--conformity 0.90`,
`--budget 2.5` (per-file seconds).

## Driving it in code / from a chat session

```python
from alpha_loop import Controller
ctl = Controller(base="http://localhost:8001", approve_conformity=0.90)
ctl.ensure_project()
report = ctl.run_stage(0, n_per_shape=3)   # one spiral
print(report.render())
# ... inspect verdicts, then escalate:
report = ctl.run_stage(1, n_per_shape=5)
```

`Controller.run_to_break(start=0)` runs mode (b).

## The stage ladder

Benign → hostile, each rung a known difficulty on a proven axis:

```
 0 benign_clean           clean (keys == golden names)
 1 benign_rename_shared   shared-token rename
 2 rename_synonym         synonym rename
 3 nesting                nested under a wrapper
 4 misspell               typo'd keys
 5 typecoerce             types coerced
 6 noise_decoys           decoy fields injected
 7 rename_abbrev          abbreviated keys
 8 conflict               ambiguous ties
 9 hostile_opaque         opaque keys
10 hostile_split_merge    split / merged fields
11 hostile_junk_leak      junk precision trap
```

## Design notes / limits

- **Shape stability for cascade:** a shape's key layout is seeded by the
  `(axis, severity)`, not the round, so re-firing the same shape yields the same
  signature — which is exactly what makes the replay/no-reprompt test meaningful.
- **Value no-fabrication check (A)** is conservative scalar-equality against the
  source multiset; it never false-alarms on a legitimately copied value and
  skips when conformed records aren't available inline.
- **`queue_wait` may read `?`** when a job goes straight to `processing` between
  polls — the finish timing is still captured. Tighten with a faster poll if the
  perf adversary needs sub-poll queue resolution.
- The apparatus imports the on-disk `tests/alpha_frontier/` harness by adding
  `backend/tests` to `sys.path`; run from `backend/` so both resolve.
