# Scenario generation: testing with real Bedrock models

The scenario generator is built and fully alpha-looped against the offline
deterministic `RuleScenarioGenerator`. To exercise the real LLM path with the
approved models (NVIDIA Nemotron, OpenAI GPT-OSS), run it where AWS Bedrock
credentials are available (this dev box has none, so only the offline path was
testable here).

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
