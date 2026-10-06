# Bake-off: structure extraction from raw uploads

**Run date:** 2026-10-05
**Harness:** `backend/tests/bakeoff/` (`approach_a.py`, `approach_b.py`,
`approach_c.py`, `harness.py`, `run.py`)
**Results:** `backend/tests/bakeoff/results.json`

## The question

The correction engine is known to produce a correct result when it is fed the
hand-authored structured inputs for a project (the manifest of discrete fields,
the structured template, the structured draft). The question this bake-off asked
is narrower and more honest: **can the engine reproduce that same gold result
starting only from the raw documents a user actually uploads** — the corpus text
files, the template DOCX, the first-draft DOCX, and the reviewer emails — with no
hand-authored structured JSON?

If it could, the pipeline would need nothing but the uploads. If it could not,
the gap would tell us exactly what extra input the real product has to carry.

## Method

The gold standard is the current engine run on the structured inputs
(`project.run_reconciliation("draft", pid, "json")`). Three approaches each try
to reproduce that gold from the raw uploads of all six sample projects, and are
scored against it on value accuracy, status accuracy, fabrication count, and
conflict preservation:

- **A — deterministic.** Rules + retrieval extract a template, a draft, and a
  heuristic manifest from the raw documents, then run the real reconcile engine.
- **B — governor + model.** The project-generation governor is repurposed to
  propose structure, with the model (gpt-oss-120b) assisting; also run offline.
- **C — simple pass.** A naive baseline that fills from the corpus without the
  manifest's discrete-field structure.

## Results

| Approach | Value % | Status % | Fabrications | Conflicts preserved | Offline |
|---|---|---|---|---|---|
| A: deterministic | 27.8 | 43.3 | 1 | 0/6 | yes |
| B: governor + model | 27.8 | 44.4 | 1 | 0/6 | yes |
| B: governor offline | 27.8 | 44.4 | 1 | 0/6 | yes |
| C: simple pass | 0.0 | 5.6 | 28 | 0/6 | yes |

## What the numbers mean

The headline is blunt: **none of the approaches came close to reproducing the
gold result from raw uploads.** The two serious approaches (A and B) plateaued at
27.8% value accuracy, and the naive baseline (C) was essentially useless at 0%
with 28 fabrications. Adding the model to approach B moved the needle by a
fraction of a point on status and not at all on value — so the model was not the
missing ingredient either.

The reason is structural, and it is the single most important finding in all of
the correction-pipeline research. The engine corrects at the level of discrete
units — this field is the severity, that field is the firmware version, this
block is the timeline prose. It knows those units exist because the project's
hand-authored **manifest** declares them, along with where each one's true value
lives in the corpus. A raw template DOCX does not carry that manifest. It carries
headings and formatting, but it does not say "there is a severity field here and
its value is found by this query." When the approaches tried to rediscover the
manifest from the raw documents, they could recover a rough section skeleton but
not the discrete-field inventory, so corrections had nowhere precise to land and
most unit values never matched gold.

The fabrication and conflict columns are the other half of the story, and they
are reassuring. Approaches A and B held fabrications to a single edge case and
the naive approach C exploded to 28 — which is exactly what you would expect from
a technique that fills freely without grounding. That contrast validated the
grounding discipline: the moment you stop checking values against the corpus, the
system invents. Every approach failed to preserve conflicts (0/6), but that
turned out to be a consequence of the same root cause — without the manifest's
field structure, the two disagreeing severity corrections had no shared target to
collide on, so no conflict was ever detected.

## Conclusion

The correction **manifest is the decisive input**, not the engine and not the
model. Raw documents alone are insufficient; the pipeline must be given (or must
reliably author) the field inventory. This finding set the agenda for the
command-center bake-off, whose entire middle act is a comparison of three ways to
supply that manifest.
