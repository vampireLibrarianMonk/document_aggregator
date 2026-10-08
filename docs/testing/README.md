# Testing & Research

This directory documents the experiments that decided the Correction Pipeline's
engine architecture. Each study is a self-contained bake-off harness under
`backend/tests/` with persisted results; the documents here capture the method,
the numbers, and the reasoning so the decisions are reproducible and auditable.

Everything is local-first and air-gap safe. The original model-backed studies
use the approved Bedrock model `openai.gpt-oss-120b-1:0`; the **model sweep**
generalizes that single-model result across every approved model (GPT-OSS,
GPT-OSS Safeguard, and Nemotron families). Every other technique runs fully
offline. All model calls are cached on disk so re-runs do not re-spend tokens
and are reproducible given a warm cache.

## Index

| Study | What it decided | Harness | Doc |
|---|---|---|---|
| Structure-extraction bake-off | Whether the engine can reproduce the gold corrected report from raw uploads alone | `backend/tests/bakeoff/` | [bakeoff-structure-extraction.md](bakeoff-structure-extraction.md) |
| Command-center bake-off | The Phase-3 correction-engine architecture (coordinator, sub-agents, manifest strategy, pathway) | `backend/tests/command_center/` | [bakeoff-command-center.md](bakeoff-command-center.md) |
| Precision-correction alpha loop | Which precision-editing technique to use, and whether a micro-model is worth integrating | `backend/tests/command_center/alpha/` | [precision-correction-alpha-loop.md](precision-correction-alpha-loop.md) |
| Model sweep + interaction review | How the winning config behaves across every approved model, and how to correctly prompt/parse each family without breaking the no-fabrication / conflict invariants | `backend/tests/command_center/model_sweep.py` | [model-interaction-review.md](model-interaction-review.md) |
| Synthetic growth dataset | The air-gap-clean, domain-matched test data the alpha loop runs on | `backend/tests/command_center/datagen/` | [dataset-generator.md](dataset-generator.md) |

## Reading order

Read them in the order above. The structure-extraction bake-off establishes the
central finding — the correction manifest is the decisive input, not the engine
or the model. The command-center bake-off builds the JEV-style architecture that
acts on that finding and proves it end-to-end. The alpha loop then stress-tests
the one remaining worry — does precision hold as documents grow — and settles the
micro-model question. The dataset generator is the shared test substrate the
alpha loop depends on.

## The invariants every study holds to

Across all studies the same hard guarantees are measured and must never break:

- **No fabrication.** A value is only emitted if it is grounded in the corpus, a
  correction, or (for figures) the corpus graphics manifest. Nothing is invented.
- **Conflict preservation.** When reviewers genuinely disagree, the unit stays a
  `conflict` for a human to resolve; it is never silently collapsed to one value.
- **Determinism.** The deterministic path produces byte-identical output on
  re-run (after stripping the `generated_at` timestamp). The model path is not
  bit-reproducible but is cached, and the assembly order is always deterministic.

## Dates

All three studies in this directory were run on **2026-10-05** against the live
`openai.gpt-oss-120b-1:0` model. The persisted result files
(`results.json`, `efficiency_results.json`, `alpha/alpha_results.json`) carry the
machine-readable record.
