# Tool-use spiral review (production tool-use path)

**Status:** harness built + offline-validated; **live run PENDING** (awaiting go).
**Harness:** `backend/tests/command_center/tooluse_spiral.py`
**Results:** `backend/tests/command_center/tooluse_spiral_results.json` (gitignored)

## Why this exists

The [model sweep](model-sweep-results.md) measures the correction agent's **text**
path (JSON-in-free-text). It never exercises the **production** `BEDROCK_ENABLED`
tiers, which use forced Converse **tool-use** — `BedrockInterpreter` (reviewer
feedback → correction ops) and `BedrockSemanticProposer` (JSON-alignment). That
tool-use path is exactly where the known hazards live (the harmony-channel tool-
name mangling we already fixed; the sibling project's Nemotron argument
corruption). This review is the adversarial, small→big ladder that measures it.

## The ladder (each rung isolates one failure mode; a fail stops that model)

| Rung | Question | Cost | What a failure tells us |
|---|---|---|---|
| 1 | Does **forced tool-use** even work? One minimal `converse` with our exact `toolConfig` + forced `toolChoice`. | 1 tiny call/model | Model rejects forced choice (some families only support `auto`), or answers in prose, or decorates the tool name. |
| 2 | Does the tool **input parse + validate + stay uncorrupted**? Run the model's ops through the real `validate_op` + an argument-corruption check. | reuses rung-1 call | Malformed ops, or framing (`<parameter=…>`, `<\|…\|>`) leaked into a value — well-formed JSON, poisoned content. |
| 3 | **One real email, one project** through `interpret_feedback()`. | a few calls | Interpreter falls off the Bedrock path; real per-model yield (accepted/rejected). |
| 4 | **Full project**, scored vs gold (value/status/conflict/fabrication). | full run | Any fabrication = a value escaped the grounding gate (safety). |

Gating is strict: a model that fails rung N is recorded (with `failure_reason` +
`fix_hint`) and **skipped** for N+1, so no tokens are wasted chasing a model that
already fell out at the cheap rung.

## Running it (on-demand, NOT CI)

From `backend/` with Bedrock reachable:

```powershell
$env:BEDROCK_ENABLED = "true"
$env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
$env:DATA_DIR = "$env:TEMP\spiral"
..\.venv\Scripts\python.exe -m tests.command_center.tooluse_spiral --rung 2
#   --rung N      run rungs 1..N (start at 2 — the cheap diagnostic pair)
#   --models a,b  subset (substring-matched to the live catalog)
```

Start at `--rung 2` (one tiny call per model, highest signal: who even honors
forced tool-use, and whose input survives validation). Escalate to `--rung 3`
then `--rung 4` only for the models that cleared the cheap rungs. Monitor by
polling `tooluse_spiral_results.json`, not the console.

The pure parsing rungs (`rung1_from_response`, `rung2_from_response`) are
unit-tested offline against synthetic responses in
`backend/tests/test_tooluse_spiral.py` (including a harmony-decorated name and a
`<parameter=…>`-corrupted argument), so the ladder's logic is proven before any
live call. One harness defect was already caught offline: `validate_op` requires
an `id` the tool schema never asks the model for, so the harness stamps a
synthetic id before validating — otherwise every model would falsely fail rung 2.

## Results — PENDING

### Rung 1–2 (forced tool-use + input validation)

> After `--rung 2`, paste one row per model.

**Run:** 2026-10-08, `--rung 2`, all 8 approved models, ~46s. **Headline: all 8
pass both rungs cleanly** — every model honored forced `toolChoice`, returned a
`toolUse` block, and emitted valid, uncorrupted ops. No forced-choice errors, no
prose-only refusals, and (notably) **no harmony-channel name decoration observed
in this run** — the sanitizer is defensive insurance here, not load-bearing. No
`<parameter=…>` argument corruption on any model, including all four Nemotrons
(the specific worry). This is the production tool-use path the text-path sweep
never measured; it holds for every model at the cheap rungs.

| Model | Rung reached | Forced tool-use | Name decorated | Input valid | Failure / fix hint |
|---|---|---|---|---|---|
| gpt-oss-120b | 2 | yes | no (clean) | yes | — |
| gpt-oss-20b | 2 | yes | no (clean) | yes | — |
| gpt-oss-safeguard-120b | 2 | yes | no (clean) | yes | — |
| gpt-oss-safeguard-20b | 2 | yes | no (clean) | yes | — |
| nemotron-super-3-120b | 2 | yes | no (clean) | yes | — |
| nemotron-nano-3-30b | 2 | yes | no (clean) | yes | — |
| nemotron-nano-12b-v2 | 2 | yes | no (clean) | yes | — |
| nemotron-nano-9b-v2 | 2 | yes | no (clean) | yes | — |

### Rung 3 (real yield — one sample email through the production interpreter)

**Run:** 2026-10-08, `--rung 3`, ~43s. **All 8 pass.** Every model drove the real
`interp=bedrock` path (not a fallback) and produced accepted, grounded ops with
**zero rejections** — each proposed op cleared `validate_op` + target
resolvability on the real email. The two larger GPT-OSS models recovered 2 ops
from the first email vs 1 for the rest (slightly more thorough NL extraction).

| Model | Interpreter | Accepted ops | Rejected ops |
|---|---|---|---|
| gpt-oss-120b | bedrock | 2 | 0 |
| gpt-oss-20b | bedrock | 2 | 0 |
| gpt-oss-safeguard-120b | bedrock | 1 | 0 |
| gpt-oss-safeguard-20b | bedrock | 1 | 0 |
| nemotron-super-3-120b | bedrock | 1 | 0 |
| nemotron-nano-3-30b | bedrock | 1 | 0 |
| nemotron-nano-12b-v2 | bedrock | 1 | 0 |
| nemotron-nano-9b-v2 | bedrock | 1 | 0 |

### Rung 4 (full project through tool-use path, scored vs gold)

> Paste after `--rung 4`.

| Model | Value % | Status % | Fabrications | Conflict kept |
|---|---|---|---|---|
| _ | _ | _ | _ | _ |

### Fixes identified

> Record each per-model failure and the concrete fix (e.g. "model X needs
> toolChoice=auto", "sanitize tool-input values for Nemotron", etc.) as the
> ladder surfaces them.
