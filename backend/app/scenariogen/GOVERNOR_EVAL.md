# Governor evaluation findings

Compares the governor's three adjudicator strategies for decomposed document
generation. Companion to `GOVERNOR_RESEARCH.md` (the JEV decision-model basis)
and `GOVERNOR_DESIGN.md` (the architecture). Harness: `backend/eval_governor.py`.

## Status

- **Offline comparison: DONE** (this document). The decomposition + decision
  state machine is proven end-to-end with ZERO paid Bedrock calls.
- **Live (paid) comparison: PENDING your approval** (cost estimate below). The
  real differentiation between adjudicators only appears on the live path.

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

## What the LIVE comparison will answer

On the live path the author is a Bedrock model and the model-based adjudicators
make real typed-verdict calls. The open questions:

- Does `generator_judge` or `decision_layer` reach high AGREEMENT with the
  deterministic ground truth (is the model judge calibrated)?
- Is `decision_layer` (small cheap model) meaningfully cheaper per document than
  `generator_judge` (big model judging), as the research predicts, while keeping
  agreement high?
- Does decomposition let the previously-starved small models (gpt-oss-20b,
  nemotron-nano-12b) succeed per-section where they failed on the whole document?

## Live run cost estimate (for approval)

A governed document is roughly: 1 author call + up to N section author calls +
N proofread/decision calls. For the 6 briefs the author produces ~5-6 sections,
so per document the model-based adjudicators add ~6-12 short decision calls.

Rough per-cell (one document) call budget:
- `generator_judge`: ~6-12 judge calls on the big author model (~512 tok each).
- `decision_layer`: ~6-12 judge calls on a small model (~256 tok each) + any
  escalations (free).

For a comparison of 2 model-based adjudicators x 6 briefs x N=2 that is on the
order of a few hundred short calls. At the pinned gpt-oss rates (~$0.0002-0.0006
per 1k tokens) the decision calls are cents-scale; the author calls dominate.
A bounded estimate: **well under $1 for a N=2, 6-brief live comparison**, but it
is real spend and real wall-clock (reasoning-model latency). The harness has a
per-document call ceiling (`GovernorBudget.max_total_author_calls`) as a hard
cost governor (the Jevons safeguard).

**Recommendation:** approve a small live run (e.g. `--live --runs 2 --briefs 3`,
one strong author model) to measure agreement + cost, before any larger sweep.

## Caveats (honest)

- Offline runs do not exercise model judgment; they prove LOGIC, not model
  calibration. The headline "which adjudicator is best" needs the live run.
- `est $` uses the pinned table (`metrics.py`), not an AWS pricing API.
- Decision agreement treats the deterministic grounding check as ground truth,
  which is correct for FABRICATION but conservative for needs_review nuance; a
  human spot-check of a few live documents is worthwhile before trusting a cheap
  judge in production.
