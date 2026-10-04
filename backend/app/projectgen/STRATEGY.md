# Scenario generation: strategy + handoff

Status as of this writing: the LLM scenario generator is **built and wired**, the
**offline/deterministic path is fully proven**, and the **live Bedrock path runs
against real approved models but does not yet reliably keep the models' output**
(it falls back to the deterministic generator). This doc captures exactly where
things are and the plan to finish it.

---

## Goal (unchanged)

Generate complete, schema-valid correction scenarios (corpus + template + draft +
graphics + corrections + a known defect inventory), like the hand-authored
scenario 1, via two entry points:

1. **Per-scenario** generation from a structured brief (domain + doc type).
2. **On-the-fly** from freeform user text, using the model's best judgment.

Reproducibility guardrail (non-negotiable): the LLM is a scenario **author**, not
part of the correction path. Its output is validated and persisted as **fixed
JSON**; from then on the deterministic engine runs it. A generated scenario must
be as traceable/reproducible as a hand-authored one, and must honor **no
fabrication** (every asserted value grounded in the corpus text or stated in a
correction body).

---

## What is DONE and proven

- **Schema + validator** (`schema.py`): `ScenarioSpec` maps 1:1 to the on-disk
  scenario tree; `validate_spec` enforces engine-consumability (resolvable
  targets, section/graphic references, corpus grounding / no fabrication).
- **Persister** (`persist.py`): atomic write of the full scenario tree, then
  generates figures (`build_scenario_graphics`) + DOCX (`build_sample_docs`).
- **Deterministic generator** (`rule_generator.py`): corpus-grounded,
  incident-shaped, parameterized by domain/title. This is the air-gap fallback
  AND the proven baseline.
- **PROVEN end-to-end (offline):** generated a scenario -> persisted ->
  reconciled with the healthy shape (17 units, 1 conflict, 1 needs_review, 8
  corrected, 4 filled) -> auto-joined the scenario list -> **passed the full
  invariant suite** (indistinguishable from hand-authored).
- **API** (`main.py`): `POST /scenario/generate` and
  `POST /scenario/generate/from-text`, both with a `dry_run` preview.
- **Model allowlist** (`config.py` + `bedrock_gen.get_scenario_model`): only
  Nemotron / GPT-OSS ids accepted; verified it rejects others.
- **Model adapters** (`model_adapters.py`): `ConverseAdapter` (default) correctly
  extracts text from both Nemotron and GPT-OSS (GPT-OSS emits a
  `reasoningContent` block before the `text` block; the adapter skips it).

## Live Bedrock facts established (this environment)

- AWS creds resolve: STS `arn:aws:iam::809784555426:user/asus-tester`,
  region `us-east-1`. (Credential chain, not env vars.)
- Approved models available (ON_DEMAND, us-east-1):
  - Nemotron: `nvidia.nemotron-super-3-120b`, `nvidia.nemotron-nano-3-30b`,
    `nvidia.nemotron-nano-12b-v2`, `nvidia.nemotron-nano-9b-v2`
  - GPT-OSS: `openai.gpt-oss-120b-1:0`, `openai.gpt-oss-20b-1:0`,
    `openai.gpt-oss-safeguard-120b/20b`
- Converse works for both. **GPT-OSS needs a large token budget** (reasoning
  block consumes tokens before the answer); generation uses `max_tokens=8192`.
- Both models produce coherent, substantial ICD scenarios (~8-12KB JSON,
  12-28s). They are doing good work.

---

## The OPEN PROBLEM

Both models reliably trip the strict validator, so the generator currently
**falls back to the deterministic generator** instead of keeping their output.
Two distinct classes of deviation, found via live testing:

1. **Schema-SHAPE quirks** (cosmetic; mostly handled now via `_coerce_spec_dict`
   + tolerant Pydantic validators):
   - keys echoed with a trailing `?` (`"requires_graphic?"`)
   - `requires_graphic/table: false|true` instead of omitting
   - `draft_sections[].fields` as `[{key,value}]` instead of a dict
   - `draft_sections[].body: null`; `.graphics` as `["g3"]`; `.table` as a string
   - numeric section keys (`"1"`); `points_to_graphic: true`
   Status: largely absorbed, but new ones appear run-to-run (models are
   nondeterministic). This is whack-a-mole and should be closed structurally.

2. **SEMANTIC errors** (the validator SHOULD catch these — they are real):
   - a graphic's `source_doc` set to a title or the image filename, not a
     corpus doc `name`
   - correction `target` pointing at a non-declared field, or using
     `section.<graphicname>` instead of the literal `section.graphic`
   - occasionally an ungrounded `new_value` (fabrication)

In-flight mitigation (partly implemented, NOT yet verified to keep model output):
- `salvage_spec()` in `schema.py`: repoints bad graphic `source_doc` to the
  first corpus doc and **drops** non-resolvable / ungrounded corrections rather
  than rejecting the whole spec.
- `_finalize()` in `bedrock_gen.py`: ladder of valid -> salvage -> one model
  repair round -> salvage -> else fall back.
- Contract prompt (`_SPEC_CONTRACT`) tightened on target format + source_doc rule.

Last observed: still `fallback=YES` for both models. ROOT CAUSE NOT YET
CONFIRMED — the probe's fallback heuristic may be misreporting, OR salvage is not
producing a valid spec. **First task on resume: instrument whether `_finalize`
returns model-authored vs None, and if None, print the residual `validate_spec`
problems after salvage.** Do not trust the probe's `fallback=` heuristic; check
the actual corrections/sections that survive.

---

## Strategy to finish (ordered)

1. **Diagnose the fallback.** In `backend/_probe_gen.py` (temporary), print, per
   model: the parsed spec's section keys + corpus names, `validate_spec` BEFORE
   salvage, the salvaged spec's surviving correction count, and `validate_spec`
   AFTER salvage. Determine whether salvage yields a valid spec and the heuristic
   is lying, or salvage genuinely fails (and why).

2. **Close schema-shape quirks structurally**, not field-by-field. Options:
   - a single recursive `_coerce_spec_dict` that strips `?` keys everywhere and
     coerces the known loose shapes; AND/OR
   - make the Pydantic models uniformly tolerant (mode="before" validators) so
     any field can absorb null/number/loose-list without a bespoke patch.
   Goal: shape never causes a rejection; only SEMANTICS can.

3. **Make salvage the primary path, fallback the last resort.** A spec with N
   good corrections and 1 bad one should persist the N (bad one dropped with a
   recorded note), not fall back. Ensure salvage also: drops orphan graphics /
   sections that reference nothing, and guarantees at least one conflict + one
   needs_review survive (re-inject from the deterministic generator if the model
   produced none, so the scenario still demonstrates the hard cases).

4. **Decide the quality bar.** A salvaged scenario must still be *interesting*
   (has the conflict + needs_review + graphic defects). Add a post-salvage
   "richness" check; if too thin, either repair-prompt again or merge in
   deterministic defects. Document the bar.

5. **Compare the two model families** on: valid-first-try rate, repair rounds
   needed, fabrication-rejection rate, latency, token cost, and scenario
   realism. Pick a default `BEDROCK_SCENARIO_MODEL`. Record findings in
   `MODEL_TESTING.md`.

6. **UI (optional, after backend is solid):** a "Generate scenario" affordance
   (brief form + freeform box) calling the two endpoints with a dry-run preview
   before persist. Not started.

7. **Guardrail re-audit before shipping:** confirm a persisted generated
   scenario (a) reconciles, (b) passes the invariant suite, (c) contains no
   ungrounded values, (d) is deterministic on re-reconcile. Run full pytest +
   ruff + a11y.

---

## Files (all UNCOMMITTED)

```
backend/app/scenariogen/
  __init__.py          exports ScenarioSpec, validate_spec
  schema.py            ScenarioSpec + validate_spec + salvage_spec (+ tolerant validators)
  persist.py           persist_spec -> on-disk tree + figures + docx
  generator.py         ScenarioBrief, ScenarioGenerator Protocol, get_generator()
  rule_generator.py    deterministic offline generator (proven)
  model_adapters.py    ConverseAdapter (default) + InvokeModelAdapter
  bedrock_gen.py       BedrockScenarioGenerator + _finalize + _coerce_spec_dict
  MODEL_TESTING.md     how to enable + test real Bedrock models
  STRATEGY.md          this file
backend/app/config.py  + BEDROCK_SCENARIO_MODEL, BEDROCK_SCENARIO_MODEL_ALLOWLIST
backend/app/main.py    + POST /scenario/generate, /scenario/generate/from-text
backend/_probe_gen.py  TEMPORARY live-model probe (delete before commit)
```

Also still uncommitted from earlier UI-polish work: header subtitle/intro copy,
"Page elements" rename, scenario-JSON jargon cleanup, project-name header
removal, redundant flow-label removal, mode-description em-dash fixes, and the
human-in-the-loop resolve feature was already committed (2799e70).

## How to run the live probe on resume

```powershell
# creds already resolve in this env (us-east-1)
cd backend
..\.venv\Scripts\python.exe _probe_gen.py      # tests both models, raw + full generator
```

Enable the real path in the app:
```
BEDROCK_ENABLED=true
BEDROCK_SCENARIO_MODEL=nvidia.nemotron-super-3-120b   # or openai.gpt-oss-120b-1:0
# allowlist defaults to nemotron,gpt-oss
```

## Decisions locked

- LLM = author only; output validated + persisted as fixed JSON; engine stays
  deterministic. Reproducibility + no-fabrication preserved.
- Approved models ONLY: Nemotron, GPT-OSS (allowlist-enforced).
- Offline deterministic generator is the always-available fallback + air-gap path.
- Prefer SALVAGE (keep good, drop bad) over wholesale fallback — pending verify.
