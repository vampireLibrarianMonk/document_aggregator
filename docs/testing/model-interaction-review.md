# Model-interaction adversarial review

**Scope:** how this app invokes a Bedrock foundation model at each call-site, how
each approved model family *should* be prompted and parsed, and — adversarially —
where a given model could silently break the two guarantees the whole system
rests on: **no fabrication** and **conflict preservation**.

**Why this exists:** every model-backed study to date ran on exactly one model,
`openai.gpt-oss-120b-1:0` (see [bakeoff-command-center.md](bakeoff-command-center.md),
[precision-correction-alpha-loop.md](precision-correction-alpha-loop.md), and the
testing [README](README.md)). The `recommend_model()` ranking that names 120b the
winner is therefore an *assertion*, not a measured head-to-head. The
[model sweep](#the-sweep-harness) turns that into data; this document is the
qualitative companion — what to watch for per model.

> This is a design/safety review of the *integration*, not a benchmark. The
> numbers live in `model_sweep_results.json` once the sweep is run.

---

## The one principle that makes model choice low-stakes

The deterministic engine is the **authoritative floor** at every call-site. A
model never writes final state; it only *proposes*, and every proposal passes a
deterministic gate before it can affect output:

- **Corrections:** the model proposes operations; `interpret_feedback` validates
  each against a fixed operation schema and an enumerated set of resolvable
  targets, and `refine_corrections` additionally drops any proposed value that
  is not grounded in the corpus or the reviewer feedback, and refuses to touch a
  target a structured correction already owns (so a genuine conflict can't be
  collapsed by a model). (`app/corrections/interpreter.py`,
  `app/corrections/refine.py`.)
- **JSON alignment:** the model picks among a *closed candidate set*; every pick
  is re-verified to be a real source field, within the offered set, and
  type-compatible, or it stays `needs_review`. (`app/json_alignment/semantic.py`,
  `apply_semantic_tier`.)
- **Project generation:** the authored spec runs through `validate_spec` →
  `salvage_spec` (drops ungrounded corrections) → at most one repair round →
  fall back to the deterministic generator. (`app/projectgen/bedrock_gen.py`,
  `_finalize`.)
- **Governor adjudication:** a model verdict that fails to parse defaults to
  `needs_review` (never silently `accept`); the decision-layer escalates to the
  free deterministic verdict when the model is not confident.
  (`app/projectgen/governor/adjudicators.py`.)

So a *worse* model mostly means **more abstentions / more fallbacks**, not wrong
output. The adversarial question is narrower and sharper: **is there any model
behavior that slips past these gates?** The sections below answer that per
call-site and per family.

---

## The call-sites (how each talks to a model)

All runtime calls go through the Converse API via one portable adapter
(`app/projectgen/model_adapters.py`, `ConverseAdapter`): `temperature=0` always,
`maxTokens` resolved per model, and the response read by concatenating only the
`text` content blocks — **deliberately skipping GPT-OSS `reasoningContent`
blocks**. An `InvokeModelAdapter` exists as a fallback but is not auto-selected.

| # | Call-site | File | Shape | Output contract |
|---|---|---|---|---|
| 1 | Correction feedback interpreter | `corrections/interpreter.py` | **Tool-use** (forced `propose_corrections`) | ops from a fixed enum |
| 2 | JSON-alignment semantic tier | `json_alignment/semantic.py` | **Tool-use** (forced `propose_field_mappings`) | one candidate per target, or `abstain` |
| 3 | Project-generation author | `projectgen/bedrock_gen.py` | **Free-text, strict JSON** in prose | a `ProjectSpec` JSON object |
| 4 | Governor adjudicator | `projectgen/governor/adjudicators.py` | **Free-text, strict JSON** | `{verdict, confidence, reason}` |

Two different output disciplines: **tool-use** (1, 2) constrains the model
structurally; **free-text JSON** (3, 4) relies on the model emitting parseable
JSON, with tolerant extraction (`_extract_json`) and schema validation behind it.
That split is the main axis of per-model risk.

### A concrete model/config mismatch worth flagging

The two tool-use call-sites (interpreter, semantic tier) read
`settings.BEDROCK_MODEL`, whose **default is a Claude id**
(`us.anthropic.claude-haiku-4-5-...`). But the project-generation allowlist
(`BEDROCK_SCENARIO_MODEL_ALLOWLIST`) is `nemotron,gpt-oss` — Claude is **not** on
it. So out of the box the correction/semantic tiers would call a model the
generation tier would refuse, and the diagnostics "approved models" list (which
is filtered by the allowlist) will not even show the model those tiers default
to. This is not a correctness bug (the gates still hold), but it is an
inconsistency: **the sweep covers the generation/allowlist models, not the Claude
default of the tool-use tiers.** Decide one of: (a) move the tool-use tiers onto
`BEDROCK_SCENARIO_MODEL` too, (b) add Claude to the allowlist if it is genuinely
approved, or (c) document that the interpreter/semantic model is chosen
separately and sweep it separately. Until then, treat the sweep as covering the
*generation* path's model behavior; the tool-use tiers inherit the same
family-level hazards below but have not been swept on their default model.

---

## Per-family interaction guide

Three approved families, two output disciplines. "Hazard" = how this family can
behave badly; "Our guard" = what already catches it; "Residual risk" = what a
sweep must actually watch.

### GPT-OSS (reasoning) — `gpt-oss-120b`, `gpt-oss-20b`

- **Defining trait:** emits an internal `reasoningContent` block before the
  answer. On Converse, reasoning tokens **count against `maxTokens`**, so a tight
  budget truncates the *answer*, not the reasoning.
- **Correct interaction:**
  - Read only `text` blocks, not reasoning — `ConverseAdapter` already does this.
    *Adversarial note:* if a future adapter change concatenated reasoning text,
    `_extract_json` could latch onto a JSON-looking fragment the model was merely
    "thinking about" and never committed to. Keep the text-only extraction.
  - Give reasoning models output headroom. `model_profiles.py` sets
    `gpt-oss-120b` → 12288 and `gpt-oss-20b` → 16384 max tokens (the small model
    gets the *most* headroom because it routinely truncated at 8192 — a
    documented, measured behavior).
- **Hazard (free-text JSON path):** truncated JSON when the budget is too small →
  parse failure → repair round → deterministic fallback. This is a *quality/cost*
  failure (more fallbacks), not a safety failure.
- **Hazard (tool-use path):** generally honors forced tool calls well; the main
  risk is emitting the tool input as a *string* rather than an object — both
  interpreter and semantic-tier extractors already `json.loads` a string
  payload, so this is handled.
- **Residual risk the sweep watches:** does the 20b model, under truncation
  pressure, ever emit a *partial* op list that happens to include an ungrounded
  value? The grounding gate drops it, so the expected failure mode is lower
  value% / more `needs_review`, never a fabrication. The sweep asserts
  `fabrications == 0` to confirm.

### GPT-OSS Safeguard — `gpt-oss-safeguard-120b`, `gpt-oss-safeguard-20b`

- **Defining trait:** a safety-tuned reasoning variant. Same reasoning-block and
  budget behavior as plain GPT-OSS; profiled identically (`reasoning`, same token
  tiers; the 20b uses the `reasoning_suppressed` default prompt).
- **Adversarial note specific to Safeguard:** a safety-tuned model is **more
  likely to refuse or hedge** on content it reads as sensitive (incident reports
  mention breaches, spills, failures). A refusal returns prose, not JSON/tool-use.
  - *Tool-use path:* a forced `toolChoice` makes an outright refusal less likely,
    but if the model returns an empty/again-prose message, `_extract_ops` /
    `_extract` simply yield no operations → the deterministic result stands. Safe,
    but worth measuring: a Safeguard model that over-refuses would show up as
    **systematically lower value%** than plain GPT-OSS of the same size.
  - *Free-text path (generation/adjudication):* a refusal is prose with no JSON →
    `_extract_json` raises → fallback. Again safe, lower yield.
- **Our guard:** same gates as GPT-OSS. No special handling exists for refusals
  beyond "no parseable output → deterministic floor."
- **Residual risk the sweep watches:** refusal rate masquerading as low accuracy.
  If a Safeguard model's `projects_run` is high but `value%` is depressed with no
  fabrications, suspect over-refusal, not incompetence — the review notes to
  check the per-project `error`/`needs_review` detail in the results.

### Nemotron (non-reasoning) — `nemotron-super-3-120b`, `nemotron-nano-{3-30b,12b,9b}`

- **Defining trait:** treated as **non-reasoning** for budget purposes (did not
  show the reasoning-truncation pattern); profiled at the default 8192 max
  tokens. No `reasoningContent` block to skip.
- **Correct interaction:**
  - Standard Converse text; strict-JSON-in-prose for the generation/adjudication
    path. `_extract_json` tolerates a markdown fence or leading prose.
  - *Adversarial note:* non-reasoning models are **more prone to emit prose
    around the JSON** or to "explain" rather than answer. The outermost-`{...}`
    extraction handles a single wrapped object; it will **not** correctly handle
    a model that emits *two* JSON objects (e.g. an example followed by the real
    one) — it takes from the first `{` to the last `}`, which would merge them
    into invalid JSON → fallback. That is safe (fallback), but a measurable
    yield hit for a chatty Nemotron.
  - The nano models (9b/12b/30b) are the smallest; expect the **highest fallback
    rate** on the generation path (whole-spec JSON is a large, structured output).
    The per-section author (`governor/section_author.py`) exists precisely to
    bound small-model output, though today it still authors via the whole-doc
    generator.
- **Hazard (tool-use path):** tool-use support/quality varies more across the
  Nemotron sizes than within GPT-OSS. If a nano model ignores the forced
  `toolChoice` and answers in prose, the tool extractors yield nothing →
  deterministic result. No fabrication possible; just no model lift.
- **Residual risk the sweep watches:** the nano models breaking the *conflict*
  invariant is the one to scrutinize. The deliberate conflict (two reviewers
  disagreeing on severity) must stay `conflict`. The model path can only ADD ops
  for targets structured corrections don't already own
  (`refine_corrections`), so the conflict target is protected by construction —
  but the sweep still asserts `conflicts_preserved == conflicts_expected` per
  model so a regression in that protection would be caught, not assumed.

---

## Adversarial summary: where could a model actually break a guarantee?

Walking each guarantee against each path:

1. **Fabrication (an ungrounded value reaches output).**
   - *Corrections:* blocked twice — op-schema validation + corpus/feedback
     grounding gate in `refine_corrections`. A model would have to propose a
     value that (a) parses as a valid op, (b) targets a resolvable unit not
     already owned by a structured correction, AND (c) appears verbatim in the
     corpus/feedback blob. If all three hold, it isn't fabrication — it's
     grounded. **No known gap.**
   - *JSON alignment:* blocked by the closed-candidate-set + type re-verification.
     A model inventing a source path fails "is a real source field." **No known
     gap.**
   - *Generation:* `salvage_spec` drops ungrounded corrections; the invariant
     suite (`verify_project` / `test_invariants`) re-checks persisted projects.
     The one place to watch is a model emitting a *grounded-looking but wrong*
     body value that the salvage step doesn't catch because it only checks
     corrections, not free prose. The sweep's `fabrications` metric
     (`score_against_gold`) checks emitted field values against the grounding
     blob, so this would surface as a fabrication count > 0 — **the sweep is the
     backstop here.**
2. **Conflict collapse (the deliberate disagreement silently resolved).**
   - Protected by construction in corrections (model may not touch an owned
     target). The sweep asserts preservation per model regardless, so a code
     regression that widened what the model may touch would be caught.
3. **Silent accept on garbage (adjudicator).**
   - `_parse_verdict` defaults to `needs_review` on parse failure and the
     decision-layer escalates below a confidence threshold. A model returning
     high-confidence-but-wrong `accept` JSON is the residual risk; that is a
     *judgment* error, bounded by the deterministic adjudicator being the ground
     truth the governor can always fall back to.

**Bottom line:** the architecture makes model quality a dial on *yield and cost*,
not on *safety* — provided the gates stay in place. The sweep exists to prove
that empirically for every model, and the pytest wrapper
(`backend/tests/test_model_sweep.py`) fails loudly if any model that ran shows a
fabrication or a collapsed conflict.

---

## The sweep harness

- **Code:** `backend/tests/command_center/model_sweep.py`
- **Config swept:** the winning bake-off cell — **draft pathway + S1-authored
  manifest + model agent** — held fixed; the model id is the only axis varied.
- **Scored per model:** `value%`, `status%`, `fabrications` (must be 0),
  `conflicts_preserved`, tokens / latency / estimated USD. The hard-invariant
  flag is `fabrications == 0 AND all conflicts preserved`.
- **Results:** `backend/tests/command_center/model_sweep_results.json`.
- **Caching:** every call is cached on disk under
  `tests/command_center/_model_cache/<model_id>/`, keyed by
  `(model_id, system, user, max_tokens)`, so a warm re-run spends no tokens.

### Running it (on-demand, NOT CI)

```powershell
cd backend
$env:BEDROCK_ENABLED = "true"          # plus AWS creds + region in the environment
$env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
$env:DATA_DIR = "$env:TEMP\sweep"
..\.venv\Scripts\python.exe -m tests.command_center.model_sweep

# a specific subset:
..\.venv\Scripts\python.exe -m tests.command_center.model_sweep --models gpt-oss-120b,gpt-oss-20b
```

The pytest guard rail (`backend/tests/test_model_sweep.py`) runs the same sweep
over a small subset and asserts the invariants; it **skips** unless
`BEDROCK_ENABLED=true`, so CI stays offline.

> **Live-call caveat:** `ModelClient.available()` returns true whenever a boto3
> `bedrock-runtime` client can be *constructed* (credentials present), regardless
> of `BEDROCK_ENABLED`. On a box with mounted creds the sweep will therefore make
> live calls even with the app flag off — the same reachable-vs-enabled nuance
> the diagnostics audit flagged (finding D1). The disk cache means a warm re-run
> is free; a cold run spends tokens. Run it deliberately.

---

## External comparison notes (to be filled in)

> A sibling project's observations on interacting with the GPT-OSS, Claude, and
> Nemotron models will be slotted here and reconciled against the findings above
> — specifically whether their experience of reasoning-block handling, tool-use
> support, refusal behavior (Claude/Safeguard), and JSON-emission quality matches
> what our gates assume. _Pending hand-off._
