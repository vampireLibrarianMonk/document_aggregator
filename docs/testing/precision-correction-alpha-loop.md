# Precision-correction scaling alpha loop

**Run date:** 2026-10-05
**Model:** `openai.gpt-oss-120b-1:0` (live, cached)
**Harness:** `backend/tests/command_center/alpha/`
**Results:** `backend/tests/command_center/alpha/alpha_results.json`

## The question

The command-center bake-off proved the engine is accurate on the small sample
projects. The open worry was scaling: discrete-field accuracy is strong, but does
precision hold as documents grow — especially for **prose edits**, where a
technique could "fix" the target sentence while silently drifting the surrounding
text? This alpha loop pits four precision-editing techniques against each other on
progressively larger synthetic documents and watches how each behaves as size
grows. It also settles a specific question the user raised: is a **micro-model**
worth integrating for precision editing?

## The techniques

Each technique receives the draft units and the localized correction intents and
returns an edited unit map. All four honour the hard invariants (never resolve a
genuine conflict, never emit an ungrounded value). They differ in *how* they
apply an edit:

- **T1 — anchored replace.** Replace the whole target unit with the corrected
  value. Simple, exact for fields, but coarse for prose: it loses any draft text
  that should have survived.
- **T2 — diff-constrained.** Locate the stale sentence inside the unit and splice
  in the corrected one, confined strictly to that unit. Preserves surrounding
  sentences; zero unintended change by construction.
- **T3 — model refinement.** gpt-oss-120b applies the correction in prose,
  grounded and conflict-guarded. High fluency, but a model regenerating a
  paragraph is where drift risk would show up.
- **T4 — micro-model (edit-tagger).** GECToR's "tag, not rewrite" paradigm
  implemented **deterministically**: token-level keep/delete/replace/append
  between the draft sentence and the corrected one, applied to the target unit
  only. Keeps untouched tokens by construction — the micro-model's key property —
  with no model, no network, no training.

## The scoring metric

Against the growth dataset's known edit ledger, each technique is scored on:
**recall** (of the units that should change, how many reached the right value),
**precision** (of the units it changed, how many were supposed to change),
**unintended changes** (count of units that changed but were not targeted — the
scaling early-warning signal), **fabrications** (new content grounded nowhere),
and **conflict preservation**.

## The micro-model research

Before building T4, we researched what a "micro-model" for precision editing
actually is. The canonical approach is **GECToR** (Grammarly's "Tag, Not Rewrite",
arXiv 2005.12592): a transformer encoder that assigns token-level edit tags rather
than regenerating text, which keeps correct tokens unchanged by construction and
so structurally avoids the over-correction that free-form seq2seq generation
risks. Pretrained weights exist (RoBERTa/XLNet/BERT variants, and a ~122 MB ONNX
build), but they load over the network and are trained for grammar, not our
factual field/body corrections — so an off-the-shelf GECToR cannot be used under
our air-gap, no-training-data constraints. The transferable part is the
*paradigm*, not the specific model: the tag-not-rewrite, keep-untouched-tokens
logic is implementable deterministically and offline, which is exactly what T4 is.

## Results

Sweep: sizes {1, 2, 4, 8, 16} × seeds {11, 23} × four techniques, live model.

### Summary (averaged across all sizes)

| Technique | Recall % | Precision % | Unintended | Fabrications | Conflicts |
|---|---|---|---|---|---|
| T2-diff | 100.0 | 100.0 | 0 | 0 | preserved |
| T3-model | 100.0 | 100.0 | 0 | 0 | preserved |
| T4-micro | 100.0 | 100.0 | 0 | 0 | preserved |
| T1-anchored | 79.7 | 79.7 | 0 | 0 | preserved |

### T1's recall as size grows

| Size | 1 | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| T1 recall % | 66.7 | 75.0 | 80.0 | 85.7 | 90.9 |

## What the numbers mean

Two things stand out. First, **T1 (naive whole-unit replace) never reaches 100%,
and the curve shows exactly why.** It fails the prose paragraph edit at every
size, because it replaces the whole paragraph with just the corrected sentence and
loses the surrounding context. Its recall creeps up from 66.7% to 90.9% only
because, as documents grow, that one botched prose edit becomes a smaller fraction
of the total — the underlying prose weakness is never fixed, it is merely diluted.
That is the "falling behind on paragraphs" failure made measurable: a technique
can look like it is improving with scale while the real defect is untouched.

Second, and most important for the scaling worry: **the unintended-change count
stayed at zero for every technique at every size, including the model.** When the
correction intent is grounded and well-scoped, nothing drifted — the model did not
rewrite neighbouring sentences, and the deterministic techniques cannot by
construction. So at the sizes tested, drift is not where accuracy breaks. The
three good techniques (T2, T3, T4) tie at a perfect score across the board.

## The micro-model verdict

**A trained micro-model (GECToR) is not worth integrating.** The deterministic
tag-not-rewrite implementation (T4) and the diff-splice (T2) match the live LLM
(T3) on every metric — 100% recall and precision, zero drift, zero fabrication,
conflicts preserved — while being free, offline, air-gap-native, needing no
training data, and fully deterministic. The entire value of a micro-model for
precision editing is "keep the correct tokens unchanged by construction," and we
get that property directly from the diff/tagging logic without the model.
Importing GECToR would mean a network download of grammar-trained weights to buy
nothing the deterministic version does not already deliver.

The one place a model could still earn its keep is **ambiguous natural-language
intents** — a vague reviewer note with no stated target text ("this reads
awkwardly, clean it up"). Our synthetic corrections are unambiguous and grounded,
so the loop deliberately does not stress that case. That is the same boundary the
command-center bake-off found: the model's real value is interpreting messy human
language into a scoped, grounded intent, not mechanically applying a known edit.

## Recommendation

Use the **deterministic diff/tagging approach (T2 or T4)** as the precision-editing
layer: as accurate as the LLM here, operationally strictly better, and holds the
no-fabrication and no-drift invariants by construction. Keep the model upstream
for *interpretation* — turning ambiguous reviewer prose into a scoped, grounded
intent — then hand that intent to the deterministic editor to apply. This matches
the Option-B architecture chosen in the command-center bake-off.

## Limitations

These are synthetic documents up to ~40 units with clean, grounded intents, which
is why the three good techniques tie. The loop proves drift is not the failure
mode and that deterministic precision editing suffices for well-scoped edits; it
does not prove the model is unnecessary for *ambiguous* edits, because none were
generated. Adding an ambiguous-intent track to the
[dataset generator](dataset-generator.md) is the natural next experiment to
pressure-test that boundary.
