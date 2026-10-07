# Coordinator prove-out — both pipelines, measured

**Date:** 2026-10-07
**What this shows:** the command-center coordinator (`backend/app/command_center/`)
running **both** production workloads end to end — the document **Correction
Pipeline** and the mass **JSON→golden** batch — on real inputs, with the
efficiency numbers that matter at scale.

Reproduce (from `backend/`, fully offline):

```powershell
$env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:BEDROCK_ENABLED="false"
.\..\.venv\Scripts\python.exe _proveout.py
```

All inputs are committed fixtures (`backend/tests/fixtures/json_alignment/`) or
the six bundled sample projects. No external data is used or committed. The LLM
tier is off (`BEDROCK_ENABLED=false`), so every number below is the deterministic
floor; a live model only adds recall on the research pathway.

## (A) Correction pipeline through the coordinator

`run_reconciliation(engine="coordinator")` across the six sample projects, both
modes (draft + template). Every run went through the command center's sub-task
DAG + deterministic queue assembler + convergence loop.

| | result |
|---|---|
| runs | 12 (6 projects × {draft, template}) — all succeeded |
| units per run | 16–17 |
| steady-state latency | ~1.3 s/run |
| first-run latency | ~16.5 s (one-time embedding/model weight load, then warm) |

The point: the coordinator is the real production path for correction, not a
toy — it reconciles all six projects in both modes with the same engine that
runs alignment. Correctness/parity with the inline `engine="direct"` path is
proven separately (byte-identical) in `backend/tests/test_command_center.py`.

## (B) Mass JSON→golden batch through the coordinator

A mixed batch of **1,350 documents** — ~8 distinct "team" shapes (the committed
variation levels) × 150 copies each, plus 150 unrelated junk files — run through
`run_batch_via_coordinator`. This models the real workload: *thousands of files,
few shapes.*

Container-aware worker cap (cores − 1, cgroup-aware): **15** on the test host.

### Pass 1 — cold library (every shape is new)

| metric | value |
|---|---|
| throughput | ~1,135 docs/sec |
| distinct shape clusters | 9 |
| pathways | `novel_research`: 600 · `review`: 750 |
| provisional profiles to approve | 4 |
| **model-calls bound** | **per cluster (9), NOT per document (1,350)** |

The headline cost property: the expensive, ambiguity-prone work (inference, and
later the LLM tier) is **bounded to the number of distinct shapes**, not the
number of files. 1,350 documents cost at most 9 research passes.

### Pass 2 — warm library (shapes approved once)

A human approves the 4 provisional shapes **once**. The identical batch is
re-run:

| metric | value |
|---|---|
| throughput | ~1,145 docs/sec |
| pathways | `replay_clean`: 450 · `review`: 750 · `drift_repair`: 150 |
| **zero-model replay** | **450 / 600 producing docs = 75%** replayed with no inference |
| conformed records | 600 |
| quarantined (fabricated) | **0** |

Once a shape is approved, every future file of that shape **replays
deterministically with zero inference**. The `review` docs are the opaque /
array-wrapped shapes that legitimately need a human (or the LLM tier) — they are
never force-converted. Junk files are quarantined, never fabricated into the
golden schema.

> Note on the warm/cold wall-clock being ~1× here: at this batch size the run is
> dominated by clustering + deterministic execution (both cheap), not by
> inference, so replay and research cost about the same *wall-clock*. The win is
> not latency at 1k docs — it is that **model/human cost stays flat as document
> count grows**: approve 9 shapes once, convert a million files. Replay's
> advantage widens exactly when the per-shape work gets expensive (the LLM tier)
> or the file count gets large.

### Drift → re-emerge → re-settle

An approved shape whose mapped `criticScore` values turn non-numeric (values
stop grounding, keys unchanged) is correctly routed to **`drift_repair`**, not
silently mis-converted:

```
approved shape + corrupted values -> {'drift_repair': 5}
```

`drift_repair` re-opens **only the broken field** for re-research; a human
re-approves the delta, and the shape settles back into deterministic replay.
This is the full lifecycle the design promised: *research → settle deterministic
→ drift breaks it → re-emerge to research on the broken field → re-settle.*

## What this proves against the goals

- **Coordinator is the production orchestrator for both pipelines** — correction
  and JSON→golden run through the same command center.
- **Efficient + async** — per-cluster/per-file parallelism under a cgroup-aware
  cores−1 cap; ~1.1k docs/sec on the test host.
- **Mass JSON→golden works** — 1,350 docs across 9 shapes converted/routed; 75%
  zero-model replay once shapes are approved.
- **Precision-first holds at scale** — zero fabrication; unrelated files
  quarantined; uncertain shapes sent to review; drift flagged, not mis-applied.
- **Cost scales with shape variety, not file count** — the central economic
  claim of the design, measured.
