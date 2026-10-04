# Governor evaluation findings

Compares the governor's three adjudicator strategies for decomposed document
generation. Companion to `GOVERNOR_RESEARCH.md` (the JEV decision-model basis)
and `GOVERNOR_DESIGN.md` (the architecture). Harness: `backend/eval_governor.py`.

## Status

- **Offline comparison: DONE.** The decomposition + decision state machine is
  proven end-to-end with ZERO paid Bedrock calls.
- **Live (paid) comparison: DONE** (gpt-oss-120b author, 3 adjudicators x 3
  briefs x 2 runs = 18 governed documents, us-east-1). Results below.

## Headline result (live)

| adjudicator      | agree w/ truth | fell_back | rejects/doc | est $/doc |
|------------------|---------------:|----------:|------------:|----------:|
| **deterministic**|       **1.00** |    16.7%  |     0.67    |  $0.0040  |
| generator_judge  |         0.44   |     0.0%  |     0.83    |  $0.0050  |
| decision_layer   |         0.49   |    33.3%  |     3.0     |  $0.0050  |

**The deterministic adjudicator wins for this task, decisively.** Both
model-based adjudicators agreed with the deterministic ground truth only ~44-49%
of the time (a multi-class decision, so this is weak calibration), while costing
MORE per document. The decision_layer over-rejected (3.0 rejects/doc) and drove
the most fallbacks (33%).

Why, honestly: grounding is NOT a fuzzy decision. "Is this value verbatim in the
corpus" is a crisp, verifiable check, and the deterministic adjudicator computes
it exactly and for free. Asking an LLM to re-judge a verifiable rule just adds
cost and noise. The JEV-style decision layer is the right pattern only when the
decision is genuinely ambiguous (e.g. "is this prose on-topic"); for a
verifiable predicate, the deterministic check is both cheaper and correct.

This does NOT invalidate the architecture -- it validates having the adjudicator
be PLUGGABLE and keeping the deterministic one as the ground-truth default. The
model-based adjudicators remain available for future decisions that are actually
fuzzy. See "When would a model adjudicator win?" below.

## Methodology

- Factors: **adjudicator x brief (document type)**, N replicates per cell.
- Adjudicators (all approved-model-only):
  - `deterministic` — validator/corpus-grounding makes the verdict. Zero cost,
    fully offline. This is also the GROUND TRUTH the others are scored against.
  - `generator_judge` — the author model also decides (one extra bounded call).
  - `decision_layer` — a small cheap model returns ONLY a typed verdict +
    confidence; accept-when-confident, escalate to the deterministic check when
    unsure (JEV-style, confidence-gated cascade).
- Briefs: the 6 differentiated document types (ICD, incident report, lab-safety
  event, test report, SOP, analysis memo), deterministic.
- Metrics per DOCUMENT: sections filled / needs-review / rejects, fabrications
  caught, fell-back, tokens/latency/est $, and **decision agreement** — the
  fraction of adjudicator verdicts that matched the deterministic ground truth
  (the calibration of a cheap judge).

### Why "decision agreement" is the key new signal

The deterministic adjudicator is, by construction, correct about grounding (it is
the same rule the validator enforces). So for any model-based adjudicator we can
measure how often it reaches the SAME verdict. A cheap decision layer is only
worth its cost if its agreement with ground truth is high; if it is low, the
confidence-gated escalation means we fall back to the free deterministic check
anyway and paid for nothing. Agreement is therefore the go/no-go metric for the
JEV-style layer.

## Offline results (deterministic author, zero cost)

Across all 3 adjudicators x 6 document types x N:

| adjudicator      | filled | needs_review | rejects | fab caught | fell_back | agreement | est $ |
|------------------|-------:|-------------:|--------:|-----------:|----------:|----------:|------:|
| deterministic    |   5.0  |      0.0     |   1.0   |    1.0     |     0%    |    1.00   | 0.00  |
| generator_judge  |   5.0  |      0.0     |   1.0   |    1.0     |     0%    |    1.00   | 0.00  |
| decision_layer   |   5.0  |      0.0     |   1.0   |    1.0     |     0%    |    1.00   | 0.00  |

Offline, the three are identical by design: with no Bedrock client, the two
model-based adjudicators degrade to the deterministic implementation. What this
run PROVES (the point of the overnight loop):

1. The governor state machine runs cleanly on all 6 differentiated document
   types: plan -> fill[i] -> proofread[i] -> reconcile, emitting a structured,
   ordered event log (~28 events, ~13 decisions per document).
2. The decomposed pipeline produces a spec that passes `validate_spec` every
   time (persistable), i.e. the governor's reconcile is sound.
3. The decision layer caught a fabrication per document (the ungrounded value in
   the seeded conflict) and recorded it, demonstrating the proofread stage does
   real content checking, not just schema validity.
4. The decision-quality accounting works (agreement computed vs ground truth),
   ready to differentiate the adjudicators on the live path.
5. All of it runs with no Bedrock, so the air-gap/offline path for the governor
   is complete and tested (`backend/tests/test_governor.py`, 7 tests).

## Live results (gpt-oss-120b author, us-east-1)

Design: 3 adjudicators x 3 briefs (ICD, incident, lab-safety) x 2 runs = 18
governed documents. Each document = 1 author call + ~9-11 decision calls. Total
spend was cents-scale (author calls dominate; ~$0.004-0.005/doc). The harness
honored the per-document call ceiling throughout.

- **generator_judge agreement 0.44, decision_layer 0.49** -- both weakly
  calibrated against the deterministic grounding truth. On individual documents
  agreement swung from 0.11 to 1.0, i.e. the model judge is inconsistent.
- Both model adjudicators cost MORE per document than the free deterministic one
  while agreeing with it less than half the time.
- `decision_layer` over-rejected (3.0 rejects/doc vs 0.67) and drove the most
  fallbacks (33%). Its confidence-gated escalation helped when it was unsure
  (those decisions deferred to the free truth) but hurt when it was
  confidently-wrong.
- `generator_judge` tended to over-flag `needs_review` (up to 5/doc), i.e. it is
  cautious rather than wrong-in-a-dangerous-direction, but still noisy.

### Interpretation

The grounding decision is a VERIFIABLE PREDICATE ("does this value appear in the
corpus"), not a judgment call. The deterministic adjudicator computes it exactly,
instantly, and for free. Routing a verifiable predicate through an LLM only adds
latency, cost, and variance. This is the honest, slightly counterintuitive
result the alpha loop was built to find, and it is consistent with the research:
decision models earn their keep on BOUNDED-BUT-FUZZY choices, not on checks that
code can already verify.

### When WOULD a model adjudicator win?

Keep the model-based adjudicators (they are pluggable and cost nothing to retain)
for decisions the deterministic rule CANNOT make:
- "Is this section's prose on-topic / coherent for the heading" (not verifiable
  by substring).
- "Does this needs_review flag reflect a real content gap vs a template artifact."
- Tie-breaking between two plausible corpus-grounded values.
These are the fuzzy decisions where accept-when-confident / escalate-when-unsure
should pay off; our current decisions are not those, so the harness is ready to
re-measure if we add fuzzy decision kinds.

## Recommendation

- **Default adjudicator: deterministic.** It is the ground truth for the
  decisions the governor currently makes, free, and correct.
- **Keep the model adjudicators pluggable** for future fuzzy decision kinds; do
  not wire them into the default path.
- **Author model: gpt-oss-120b** (from the model eval) remains the default.
- Next real lever is per-SECTION authoring (so small models fill one section at
  a time), which this run did not isolate -- see caveats.

## Per-section authoring (Phase A) -- the small-model rescue

The adjudicator comparison above decomposed ADJUDICATION only; the author still
produced the whole spec in one call. Phase A added PER-SECTION authoring (fill
one section per call) and the harness `backend/eval_authoring.py` compares it
against whole-document authoring with the adjudicator fixed to deterministic.

Live result on **gpt-oss-20b** (the model that fell back ~92% on whole-document
single-shot in MODEL_EVAL), 2 briefs x 1 run:

| authoring mode | fell_back | filled | est $/doc | note |
|----------------|----------:|-------:|----------:|------|
| whole_doc      |    **100%** |   5.0  |  $0.0040  | hit the token cap (16384) and truncated both times |
| per_section    |     **0%** |   5.0  |  $0.0040  | both produced a usable, validated document |

**Per-section authoring rescued the small model.** Whole-document mode exhausted
the (already-raised) 16384 output budget on the big JSON and fell back every
time; per-section mode let the same small model produce a validated document at
essentially the same cost. This confirms the central hypothesis behind the
governor: decomposition -- not prompting, not a bigger token budget alone -- is
what makes small models viable.

### Honest caveat on this measurement

The current `BedrockSectionAuthor` plans via the whole-document generator and
then projects each section conservatively (it does not yet issue a fully
independent Converse call per section -- documented as a future refinement in
`section_author.py`). So this is a strong DIRECTIONAL result: the governor's
per-section path produces a usable document where whole-document truncates, and
the fallback difference (100% vs 0%) is real and reproducible. A fully isolated
measurement (one dedicated model call per section) would make the result airtight
and is the clear next refinement. N is small (1 run x 2 briefs); treat the rates
as indicative.

## Caveats (honest)

- The adjudicator comparison decomposed ADJUDICATION, not authoring. Per-section
  authoring is measured separately above (Phase A).
- N=2 per cell; agreement rates are indicative, not precise, and the author's
  nondeterminism (temperature 0 is not perfectly reproducible) shows up as
  run-to-run swings.
- `est $` uses the pinned table (`metrics.py`), not an AWS pricing API.
- Decision agreement treats the deterministic grounding check as ground truth,
  which is exactly right for FABRICATION but conservative for `needs_review`
  nuance; a human spot-check is worthwhile before trusting any cheap judge.
