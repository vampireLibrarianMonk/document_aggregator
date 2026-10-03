# Scenario generation: testing with real Bedrock models

The scenario generator runs against the offline deterministic
`RuleScenarioGenerator` by default and, when AWS Bedrock credentials resolve,
against the approved models (NVIDIA Nemotron, OpenAI GPT-OSS). Both the live
single-generation path and the model-comparison eval harness have been exercised
against real models in `us-east-1`.

## What is already proven (offline, deterministic)

- `ScenarioSpec` schema + `validate_spec` (no-fabrication grounding, resolvable
  targets, engine-consumability).
- `persist_spec` writes a complete scenario tree + figures + DOCX.
- A generated scenario reconciles with the healthy shape (conflict + needs_review
  + corrected/filled) and passes the full invariant suite, indistinguishable
  from a hand-authored scenario.
- Two API endpoints: `POST /scenario/generate` (structured brief) and
  `POST /scenario/generate/from-text` (freeform, model best-judgment), both with
  a `dry_run` preview.
- Model allowlist enforcement: only Nemotron / GPT-OSS model ids are accepted;
  anything else is refused. On any Bedrock failure the generator falls back to
  the deterministic generator, so the feature never breaks the system.

## Enabling the Bedrock path

Set these (env or `.env`), then restart the api:

```
BEDROCK_ENABLED=true
BEDROCK_REGION=us-east-1                      # your Bedrock region
AWS_ACCESS_KEY_ID=...                          # or an instance/role profile
AWS_SECRET_ACCESS_KEY=...
BEDROCK_SCENARIO_MODEL=<approved model id or inference-profile id>
# allowlist already defaults to: nemotron,gpt-oss
```

Example model ids to try (confirm the exact id/inference-profile in your
account's Bedrock console; these vary by region and availability):

- Nemotron family: a `...nemotron...` model or cross-region inference profile id.
- GPT-OSS family: a `...gpt-oss...` model id (e.g. a 20B/120B deployment).

The allowlist (`BEDROCK_SCENARIO_MODEL_ALLOWLIST`, default `nemotron,gpt-oss`)
is a substring match against the model id; set it explicitly if your ids differ.

## How to test each model

1. Dry-run a structured brief and inspect the returned spec:
   ```
   POST /scenario/generate
   {"domain":"avionics interface validation",
    "doc_type":"interface control document",
    "title":"Nav Bus ICD Review","dry_run":true}
   ```
   Check: `generator` reads `bedrock` (not `rule-based (offline)`), and the
   `spec` is coherent for the domain.

2. Dry-run the freeform path:
   ```
   POST /scenario/generate/from-text
   {"text":"<describe a real situation and its document>","dry_run":true}
   ```

3. What to evaluate per model (Nemotron vs GPT-OSS):
   - Does it return STRICT JSON (no prose/markdown)? The generator tolerates a
     stray fence and does one repair round, but a model that needs repair often
     is noisier.
   - Does `validate_spec` pass first try? Watch for fabrication rejections
     (a corrected value not present in the generated corpus) — that is the model
     inventing facts, exactly what the validator must catch.
   - Are the defects realistic and the conflict/needs_review present?
   - Latency and token cost.

4. When satisfied, drop `dry_run` to persist. The scenario is then a FIXED
   artifact: it reconciles deterministically and joins the invariant suite like
   any hand-authored scenario. Review the generated JSON before committing it.

## Model-invocation seam

`model_adapters.py` abstracts invocation. The default `ConverseAdapter` uses the
Bedrock Converse API and asks for strict JSON in the prompt (portable across the
approved families, no tool-use dependency). If a specific GPT-OSS deployment
only exposes `invoke_model`, switch `adapter_for()` to `InvokeModelAdapter`.
Add a model-specific adapter here if a family needs a different payload shape.

## Reproducibility note

The LLM is a scenario AUTHOR, never part of the correction path. Its output is
validated and written to disk as fixed JSON; from then on the deterministic
engine runs it. So a generated scenario is as reproducible and traceable as a
hand-authored one, and nothing about correctness depends on the model at
runtime.

## Choosing a model in the UI

The Correction Pipeline tab has a **"+ New scenario"** button that opens the
generator panel. There you pick a **model** (the offline deterministic generator,
or any live approved Bedrock model from `GET /scenario/models`), enter a brief
(structured domain/doc-type/title, or freeform), and either **Dry run (preview)**
or **Generate & save**. After each run the panel shows the per-run score + cost
readout (outcome, fabrications caught, tokens, latency, estimated $).

If Bedrock is not enabled / no creds (e.g. the default compose stack), the picker
shows only the offline generator — the feature degrades gracefully.

## Model evaluation harness (score + cost)

`backend/eval_models.py` runs each approved model over a fixed set of briefs N
times and prints an aggregated comparison, writing `eval_models_report.json`.

```
python backend/eval_models.py                 # all approved models, N=3
python backend/eval_models.py --runs 5
python backend/eval_models.py --models nvidia.nemotron-super-3-120b,openai.gpt-oss-120b-1:0
```

### How to read the numbers (all measured, none subjective)

Score (from the strict validator):
- **valid1st%** — raw model output passed validation with no repair (higher better).
- **repaired%** — needed one model repair round.
- **fellback%** — model output was unusable, so the deterministic generator ran
  instead (lower better; this is the worst outcome).
- **fab/run** — corrections the model INVENTED that the validator caught and
  dropped (lower better). This directly measures the platform's core promise of
  no fabrication; a high number means the model hallucinates values.
- **conflict% / needs-review%** — richness: did it produce the required hard
  cases.

Cost:
- **in_tok / out_tok** — token usage, measured from the Bedrock response (ground
  truth). GPT-OSS spends output tokens on a reasoning block before the answer,
  so its out_tok runs high; a model that hits the token cap tends to fall back.
- **lat_ms** — server-side latency, measured.
- **est_$** — an ESTIMATE: measured tokens x a PINNED, dated price table
  (`metrics.py`, `PRICE_TABLE_PINNED`). Bedrock does not expose live prices via
  API, so verify current AWS pricing before relying on the dollar figure. Tokens
  and latency are exact; only $ is estimated.

### Example finding (us-east-1, 2026-05, 120B models)

A first comparison (nemotron-super-3-120b vs gpt-oss-120b) showed a real
trade-off rather than a clear winner: GPT-OSS was cheaper, faster, and always
produced usable output but fabricated more and less reliably included the
required conflict; Nemotron fabricated less and nailed richness but fell back
more often and cost ~2x. Neither was "valid first try" — the salvage layer
(drop bad items, keep the good) carries both. Re-run the harness with more runs
to get stable rates before picking a default `BEDROCK_SCENARIO_MODEL`.
