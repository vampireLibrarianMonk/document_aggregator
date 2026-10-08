# Model sweep — results

**Run date:** 2026-10-07
**Harness:** `backend/tests/command_center/model_sweep.py`
**Raw results:** `backend/tests/command_center/model_sweep_results.json` (gitignored;
regenerate on demand)
**Config (fixed):** the winning bake-off cell — **draft pathway + S1-authored
manifest + model agent** — across all six sample projects. The model id is the
only axis varied.
**Region:** us-east-1. **Models:** all 8 approved + available (GPT-OSS,
GPT-OSS Safeguard, Nemotron families). **Bedrock calls:** cached on disk per
model (gpt-oss-120b was warm from the original bake-off; the other seven were
cold and spent live tokens on this run).

This closes the gap called out in [model-interaction-review.md](model-interaction-review.md):
the architecture had only ever been measured on one model (`gpt-oss-120b`), so
`recommend_model()` naming it the winner was an assertion. It is now measured.

---

## Scorecard

| Model | Value % | Status % | Fabrications | Conflict kept as `conflict` | In tok | Out tok | Est. USD |
|---|---|---|---|---|---|---|---|
| **gpt-oss-120b** | **100.0** | **100.0** | **0** | **6/6** | 11,400 | 21,272 | 0.0154 |
| **nemotron-nano-12b-v2** | **100.0** | **100.0** | **0** | **6/6** | 10,556 | 7,368 | **0.0033** |
| nemotron-super-3-120b | 98.9 | 98.9 | 0 | 5/6 | 10,580 | 6,592 | 0.0182 |
| nemotron-nano-3-30b | 96.6 | 96.6 | 0 | 3/6 | 10,580 | 4,932 | 0.0051 |
| nemotron-nano-9b-v2 | 95.5 | 95.5 | 0 | 2/6 | 10,484 | 22,240 | 0.0062 |
| gpt-oss-20b | 94.4 | 94.4 | 0 | 1/6 | 11,400 | 23,496 | 0.0078 |
| gpt-oss-safeguard-120b | 94.4 | 94.4 | 0 | 1/6 | 11,400 | 23,036 | 0.0166 |
| gpt-oss-safeguard-20b | 93.3 | 93.3 | 0 | 0/6 | 11,400 | 24,480 | 0.0175 |

Est. USD is the pinned-price-table estimate for the model's 6-project run; tokens
are ground truth from the Bedrock responses. (gpt-oss-120b / 20b ran from a warm
cache on this pass, so their recorded latency is 0; the cold models carry real
latency.)

---

## The two headline findings

### 1. Zero fabrications across every model — the floor holds universally

Not one model, from the strongest to the weakest, pushed an ungrounded value
into the output. This is the whole thesis of the architecture, now proven across
the fleet rather than assumed from one model: the deterministic grounding gate
(`refine_corrections` + the agent's grounding check) is what guarantees
no-fabrication, so **model choice moves accuracy and cost, not safety.**

### 2. The "lost conflict" cases are re-flagged, NOT silently resolved

The right-hand column shows weaker models "keeping" fewer conflicts. This looked
alarming, so it was investigated per project. The pattern is clean and
reassuring: **every time a conflict is not kept as `conflict`, it reappears as
`needs_review`, with fabrications still 0.** For example, gpt-oss-safeguard-20b
kept 0/6 as `conflict`, but each of those projects shows `needs_review` rising
by exactly the amount `conflict` fell — the deliberate severity disagreement is
still surfaced for a human, just under the more generic "needs review" flag
instead of the specific "conflict" flag.

So no model ever did the dangerous thing (silently pick a side, or invent a
value). The weaker models are simply less precise about *which* unresolved-state
label they attach. Both `conflict` and `needs_review` are human-must-decide
terminal states; neither hides the disagreement.

**Caveat on the metric:** the sweep's `conflict_preserved` invariant is
deliberately strict — it requires the exact `conflict` label. By that strict
bar, six of eight models "fail." By the bar that actually matters for safety —
*the disagreement is never silently resolved and nothing is fabricated* — **all
eight pass.** The scorecard reports the strict count; this paragraph is the
honest interpretation.

---

## What it means for model choice

- **`gpt-oss-120b` and `nemotron-nano-12b-v2` are the only two perfect runs**
  (100% value/status, 6/6 conflicts kept precisely, 0 fabrications). The
  `recommend_model()` ranking that crowns gpt-oss-120b is no longer unopposed —
  **nemotron-nano-12b matched it on quality at roughly a fifth of the estimated
  cost and a third of the output tokens.** That is a strong, data-backed
  alternative recommendation worth wiring into the ranking.
- **Reasoning models cost more tokens.** The GPT-OSS / Safeguard families burned
  ~21k–24k output tokens (reasoning counts against the budget); the Nemotron
  non-reasoning models mostly ran far leaner (nano-3-30b: ~4.9k, super: ~6.6k).
  The one exception, nemotron-nano-9b at ~22k out, is the smallest model
  over-emitting — consistent with the "nano over-produces structure" hazard.
- **Safeguard underperformed plain GPT-OSS at the same size** (safeguard-20b
  93.3% / 0 conflicts vs gpt-oss-20b 94.4% / 1 conflict), with the highest token
  spend. Consistent with the predicted refusal/hedging tendency depressing yield;
  worth avoiding for this task unless its safety tuning is specifically needed.

**Bottom line:** every approved model is *safe* to use (0 fabrications, no silent
conflict resolution). They differ in how precisely they preserve the `conflict`
label and in token cost. For this correction workload, **gpt-oss-120b (ceiling)
and nemotron-nano-12b (best value) are the two to prefer.**

---

## Cross-project corroboration (external hand-off)

A sibling project running the same model families on a *document-generation*
workload (with live tool-use search, not our tool-forced correction ops)
reported failure modes that sharpen — and in one case extend — the guidance in
[model-interaction-review.md](model-interaction-review.md). Recorded here because
they are load-bearing for anyone integrating these models over Bedrock Converse:

- **GPT-OSS tool-name mangling.** Over Converse these models decorate the tool
  name with a harmony-channel suffix (e.g. `propose_corrections<|channel|>commentary`)
  and sometimes emit the name in a `contentBlockDelta` rather than the tool-use
  *start* event. A strict exact-name match then rejects the call and the tool
  "never fires" — which looks like "the model can't use tools" but is pure name
  mangling. **Action for us:** our `_extract_ops` / `_extract` match the tool
  name exactly (`propose_corrections`, `propose_field_mappings`); they should
  **sanitize the streamed tool name** (strip any `<|channel|>…` suffix) before
  dispatch, or we will under-count GPT-OSS tool-use capability. Also: 120b blanks
  in *streaks* (2–3 empty drafts), so a retry budget must exceed one miss.
- **Nemotron argument corruption.** The model can leak framing like
  `<parameter=query>` *inside* the argument value, producing well-formed JSON
  with a poisoned value — a tolerant outer-`{…}` parser will not catch it because
  the envelope is valid. **Action for us:** our semantic tier's closed-candidate
  re-verification (the pick must be a real source field within the offered set)
  should already reject a poisoned `source_path`; confirm it does, and consider a
  value-level sanitizer on any model-supplied string.
- **Two deterministic gates, not one.** Their strongest correction to the review:
  for grounded/factual output you need a deterministic gate for **values** *and*
  a separate one for **citations/provenance**, because a model can be fully
  "successful" (parses, fills every field) while asserting sources that do not
  exist. We gate values hard; provenance is currently attached, not independently
  re-verified against real corpus/correction ids. **This is a genuine gap** — a
  provenance-resolvability gate (drop any `provenance.corpus` / `corrections`
  reference that does not resolve to a real loaded source) is the natural next
  hardening, and this sweep's 0-fabrication result only covers *values*, not
  provenance.

Their caveat matches ours: their full cross-model sweep was still rolling up when
they shared this, so the family-level guidance is load-bearing and their
per-model numbers are pending — as are refinements to ours.


---

## Staging / runbook (for a fresh full re-run)

One command, from `backend/`:

```powershell
.\tests\command_center\run_full_sweep.ps1
```

It wipes the on-disk model cache, sets `BEDROCK_ENABLED=true` + the offline
guards + a temp `DATA_DIR`, runs the sweep across all approved models, and
redirects the verbose output to `tests/command_center/_sweep_run.log`. Monitor by
polling `tests/command_center/model_sweep_results.json` (mtime) rather than
tailing the log, so an agent session stays token-frugal. Expect roughly 15–20
minutes of live Bedrock calls on a cold cache.

**Preconditions (verified 2026-10-07, re-check before a run):**

- Bedrock reachable with credentials — `list_approved_models().available == True`,
  8 models catalogued (GPT-OSS ×2, Safeguard ×2, Nemotron ×4).
- `BEDROCK_ENABLED=true` in the environment the sweep runs in.
- The bounded botocore timeout is present in `ModelClient.available()`
  (`modelcall.py`) so a slow/non-invokable model fails fast instead of stalling.
- `_model_cache/` and `model_sweep_results.json` are gitignored (the committed
  record is this markdown, not the raw JSON).
- Offline test suite green and the harness imports/resolves 8 model ids on a dry
  run.

**Important — what this sweep does and does not exercise.** The sweep's model
agent uses the **text** path (`mc.complete` + `extract_json`), i.e. it asks the
model for a JSON array in free text, not Bedrock tool-use. So:

- The recent **tool-name sanitizer fix does NOT move these numbers** — that fix
  repairs the *production* `BEDROCK_ENABLED` tiers (the feedback interpreter and
  the JSON-alignment semantic picker, which DO use forced tool-use), not this
  harness. Do not expect a re-sweep to change the per-model scores because of
  that fix.
- A fresh cold re-run is still worthwhile for a **uniform dataset with real
  latency on every model** (the first run reused a warm cache for two models, so
  their recorded latency was 0) and to confirm stability of the scores.

**Future (not built):** a separate harness that drives the *production* tool-use
correction path (`BedrockInterpreter`) per model would measure what the
tool-name fix actually touched — the sweep cannot, by design.

### Run 2 — fresh cold cache (PENDING)

> Placeholder for the next full run. After `run_full_sweep.ps1` completes, paste
> the scorecard here (same columns as Run 1) and note the run date, whether all
> 8 models stayed invokable, and any score drift vs Run 1.

| Model | Value % | Status % | Fabrications | Conflict kept as `conflict` | In tok | Out tok | Latency ms | Est. USD |
|---|---|---|---|---|---|---|---|---|
| gpt-oss-120b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| gpt-oss-20b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| gpt-oss-safeguard-120b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| gpt-oss-safeguard-20b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| nemotron-super-3-120b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| nemotron-nano-3-30b | _ | _ | _ | _/6 | _ | _ | _ | _ |
| nemotron-nano-12b-v2 | _ | _ | _ | _/6 | _ | _ | _ | _ |
| nemotron-nano-9b-v2 | _ | _ | _ | _/6 | _ | _ | _ | _ |
