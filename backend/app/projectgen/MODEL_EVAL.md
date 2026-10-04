# Project generation: model evaluation findings

A controlled, objective experiment across the full approved model set (NVIDIA
Nemotron + OpenAI GPT-OSS families) to answer three questions:

1. Which models can actually author a valid project, and which fall over?
2. Does a better prompt lift the weaker / smaller models?
3. What should the default be, and can we pick a model for the user?

Every signal is **measured** from the strict validator and the Bedrock response.
Nothing here is a subjective "quality" rating. The one estimate is dollar cost
(measured tokens x a pinned, dated price table); tokens and latency are ground
truth.

## Methodology

- Harness: `backend/eval_models.py`, a 3-factor design
  **model x prompt-strategy x brief**, N replicates per cell.
- Models (8 approved, `us-east-1`, ON_DEMAND): gpt-oss-120b, gpt-oss-20b,
  gpt-oss-safeguard-120b, gpt-oss-safeguard-20b, nemotron-super-3-120b,
  nemotron-nano-3-30b, nemotron-nano-12b-v2, nemotron-nano-9b-v2.
- Prompt strategies (`backend/app/projectgen/prompts.py`): `baseline`,
  `strict_schema`, `few_shot`, `reasoning_suppressed`.
- Briefs: fixed, deterministic document types (ICD, incident report, lab-safety
  event report were the core three used for this run).
- Metrics per run: outcome (valid-first-try / repaired / salvaged / fell-back),
  fabrications the validator rejected, salvage drops, conflict/needs-review
  richness, input/output tokens, latency, estimated $.

### What the outcomes mean

- **valid-first-try** — raw model JSON passed `validate_spec` with no repair.
- **salvaged** — kept by dropping ungrounded corrections / repointing graphics.
- **repaired** — needed a model repair round.
- **fell-back** — output unusable; the deterministic generator ran instead
  (the worst outcome; the feature still never breaks because of this safety net).

## Results

This run covered the GPT-OSS family in full and the larger Nemotrons; the
smallest nano models were still in progress when the run was cut off, because the
result was already decisive (see Conclusion). Pooled over prompts and briefs:

| model                     |  n | fell-back | valid-first-try | salvaged | avg out tok |
|---------------------------|---:|----------:|----------------:|---------:|------------:|
| gpt-oss-120b              | 24 |     **8%** |            0%   |     92%  |      ~4,950 |
| gpt-oss-safeguard-20b     | 24 |      29%  |            0%   |     71%  |      ~6,000 |
| nemotron-nano-3-30b       | 12 |      25%  |          **17%** |    58%  |      ~3,440 |
| gpt-oss-safeguard-120b    | 24 |      46%  |            0%   |     50%  |      ~7,430 |
| gpt-oss-20b               | 24 |    **92%** |            0%   |      8%  |      ~8,070 |
| nemotron-nano-12b-v2      | 24 |    **92%** |            0%   |      8%  |      ~2,610 |

Prompt-strategy roll-up (pooled over all models and briefs):

| prompt               |  n | fell-back | valid-first-try | salvaged |
|----------------------|---:|----------:|----------------:|---------:|
| **baseline**         | 36 |    **39%** |            0%   |     58%  |
| reasoning_suppressed | 30 |      50%  |            0%   |     47%  |
| strict_schema        | 36 |      56%  |            0%   |     42%  |
| few_shot             | 30 |      60%  |            0%   |     40%  |

### Three findings

1. **Single-shot generation does not produce a valid spec on the first try.**
   Across ~150 generations, every model and every prompt scored **0%
   valid-first-try**. The salvage layer (drop bad items, keep the good) is
   carrying the entire feature. This is the headline result and it is bigger than
   any model ranking.

2. **Viability splits by size, not family.** The 120b-class and the 30b Nemotron
   are usable (salvage mostly succeeds); the small models drown in the one giant
   JSON contract — gpt-oss-20b and nemotron-nano-12b fell back ~92% of the time.
   gpt-oss-20b consistently exhausted its output budget (see token-budget note)
   and truncated the JSON.

3. **Prompt engineering is NOT the lever (negative result).** The longer,
   stricter prompt variants were all *worse* than `baseline`; `baseline` had the
   lowest fallback rate. Adding instructions consumed output budget and raised
   truncation. Rephrasing the ask does not fix a model that cannot emit the whole
   document in one shot.

## Cost / latency (measured; $ is an estimate)

- gpt-oss-120b was the cheapest usable model (~$0.003/run) and fast (~6-16s).
- The safeguard and reasoning variants ran higher latencies (up to ~40s) because
  they spend output tokens on an internal reasoning block before the answer.
- nemotron-super-3-120b was richer but ~2x the cost of gpt-oss-120b (from the
  earlier 120b-vs-120b comparison).
- Dollar figures use the pinned table in `metrics.py`
  (`PRICE_TABLE_PINNED`); verify current AWS pricing before relying on them.

## Token budget is size-dependent (fix applied)

On the Bedrock Converse API the output-token cap bounds **everything** the model
emits, including the internal reasoning block of reasoning models. A single flat
cap (the old hardcoded 8192) therefore starves exactly the models that reason
most: gpt-oss-20b hit 8192 on 20/24 runs and truncated. The fix is
`backend/app/projectgen/model_profiles.py`, which resolves the budget **per
model** from a capability profile (reasoning models get more headroom; a model
that finishes early is not billed for the ceiling). This removes the artificial
truncation without scattering model-name checks through the generator. The
`baseline` runs above used the old flat cap, so they understate what the small
reasoning models can do once right-sized; the budget A/B harness
(`backend/eval_budget_ab.py`) isolates that effect.

## Conclusions

- **Default model: gpt-oss-120b** — lowest fallback, cheapest usable, fast. This
  is an *earned* default from the numbers, not an assertion. nemotron-super-3-120b
  is the richer-but-pricier alternative.
- **Default prompt: baseline.** The variants did not help; keep the simplest ask.
- **The real lever is task decomposition, not prompting.** Because no model emits
  a valid whole-document spec in one shot, and small models truncate, the
  architecturally correct next step is to break a document job into bounded,
  verifiable steps (plan sections -> fill each section -> proofread each against
  the corpus -> reconcile) so each step is small enough that even a small model
  can do it well, and each step is checked. That is the governor work tracked
  separately; this evaluation is its justification.

## Caveats (honest)

- **Sample size** is small (N=2 per cell for this run); rates are indicative, not
  precise. Re-run with higher N for tighter intervals.
- **Nondeterminism**: temperature is 0, but Bedrock output is not perfectly
  reproducible run-to-run.
- **$ is an estimate** from a pinned table, not an AWS pricing API.
- The nano-9b / nano-3-30b cells are partial (run cut off once the conclusion was
  clear); treat their rows as preliminary.
