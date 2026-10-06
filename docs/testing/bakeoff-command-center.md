# Bake-off: the command-center correction engine

**Run date:** 2026-10-05
**Model:** `openai.gpt-oss-120b-1:0` (live, cached)
**Harness:** `backend/tests/command_center/`
**Results:** `backend/tests/command_center/results.json`,
`backend/tests/command_center/efficiency_results.json`

## The goal

The structure-extraction bake-off proved the correction manifest is the decisive
input. This bake-off builds and evaluates the architecture that acts on that
finding: a JEV-style **command center** — a coordinator that decomposes a
project's correction work into a bounded sub-task graph, dispatches each task to
a swappable sub-agent, runs the independent tasks through a parallel queue with a
deterministic order-preserving assembler, and iterates to convergence
(needs-review driven to zero, genuine conflicts preserved). The bake-off runs the
full cross-product of pathway × agent × manifest-strategy × project and scores
every cell, so the winning configuration is chosen from data rather than taste.

## The pieces

- **Coordinator** (`coordinator.py`): a five-task DAG —
  derive-template → extract-draft → derive-manifest → parse-corrections →
  reconcile — run round by round until convergence or a round cap.
- **Queue + assembler** (`core.py`): independent tasks can run concurrently; the
  assembler re-sorts results by a stable `(order, task_id)` key, so the final
  assembly is byte-identical regardless of finish order. Verified: parallel and
  sequential runs produce the same report once `generated_at` is stripped.
- **Sub-agents** (`agents.py`): two interchangeable implementations of the same
  tasks — a `DeterministicAgent` (pure offline rules/retrieval) and a
  `ModelAgent` (gpt-oss-120b for the one hard natural-language step, grounded and
  validated so the engine never receives an invented value).
- **Manifest strategies** (`strategies.py`): the three ways to supply the
  decisive input — **S1 authored** (use the project's hand-authored manifest),
  **S2 model** (the model proposes a grounded field inventory), **S3 body**
  (no discrete fields, section bodies only).
- **Two pathways** (engine `mode`): **draft** (seed the correction from the
  first-draft document) and **template** (seed from the blank template).
- **Review** (`review.py`): a per-round staged-evolution report plus a
  final-product render check that renders the corrected report to all five
  deliverable formats and confirms each opens with its content intact.

## Precursor: the model-efficiency study

Before the matrix, a short study measured the cheapest reliable way to use the
model for the natural-language step (parsing reviewer emails into correction
operations). Three prompt shapes, same five target operations:

| Shape | Calls | Input tok | Output tok | Latency | Accuracy |
|---|---|---|---|---|---|
| batched (one call, all emails) | 1 | 493 | 821 | 1,492 ms | 4 / 5 |
| per-email | 5 | 1,107 | 779 | 4,374 ms | 4 / 5 |
| whole-document | 1 | 1,080 | 915 | 13,866 ms | 4 / 5 |

Batched prompting won decisively — one call, the fewest input tokens, and roughly
3× faster than per-email and nearly 10× faster than whole-document, at the same
accuracy. All three shapes missed the same single item: a figure relabel whose
exact filename lives in the corpus graphics manifest, not in the email prose. The
model correctly refused to invent the filename — a good failure, not a bad one —
and that refusal is what later motivated a deterministic corpus-grounding step to
resolve the prose reference to the exact file. The study set the model sub-agent's
operating rules: one batched, cached, temperature-0 call per project, paired with
deterministic grounding.

## The matrix

72 cells = 2 pathways × 2 agent types × 3 manifest strategies × 6 projects. Every
cell ran clean (0 errors), every cell was deterministic on re-run, and every cell
rendered all five output formats openable.

### Summary (averaged across the 12 cells per configuration)

| Configuration | Value % | Status % | Conflicts preserved |
|---|---|---|---|
| model / S1-authored | **96.6** | **76.1** | all |
| deterministic / S1-authored | 95.0 | 74.4 | mixed |
| model / S2-model | 24.5 | 44.5 | no |
| deterministic / S2-model | 24.5 | 43.3 | no |
| model / S3-body | 24.5 | 44.5 | no |
| deterministic / S3-body | 24.5 | 43.3 | no |

On the clean, like-for-like path — **draft pathway + authored manifest + model
agent — all six projects reach 100% value and 100% status, zero fabrications,
conflicts preserved**, once the figure-grounding fix (below) is in place.

## What the numbers mean

The decisive split is not between the agents and not between the pathways — it is
between the manifest strategies. S1 (authored manifest) scored around 95–97%
while S2 (model-authored manifest) and S3 (body-only) both collapsed to roughly
24%. This is the structure-extraction finding reproduced inside the full
architecture: when the engine is handed the real field inventory it is nearly
perfect, and when it has to work without one it cannot do discrete-field
corrections no matter how good the agent is. Notably, letting the model *author*
the manifest (S2) did not beat the dumb body-level fallback (S3) — proposing the
schema from scratch is the hard, still-unsolved part, which is a pointed signal
for where future model investment should go.

The gap between the top two rows is the honest measure of what gpt-oss-120b buys
on top of an already-strong deterministic baseline: value rose from 95.0 to 96.6,
status from 74.4 to 76.1, and — importantly — the model configuration preserved
conflicts across all cells where the deterministic-only configuration missed one
(project 6's prose approval-conflict, which needs language understanding the
rules did not have). It is a refinement, not a transformation, and it never
fabricated, because every value the model proposed had to pass a grounding check
before the engine would accept it.

The figures that look alarming in the aggregate — the nonzero fabrication counts
and the "not all conflicts preserved" flags on some rows — were traced and are
scoring artifacts of the template pathway, not the engine inventing facts. In
template mode the first-attempt is the blank template, whose boilerplate header
("Standard Incident Report Template") is carried verbatim into the output; that
header is legitimately not in the *corpus*, so the grounding metric, which is
tuned for the draft pathway where everything should come from source material,
flags it. The engine invented nothing. The draft pathway is the clean one, and it
is the one recommended for production.

## The grounding fix that reached 100%

The one recurring miss on the clean path was a figure that was valued correctly
but labelled `filled` instead of `corrected`. Reviewer emails describe a figure
in prose ("the packet-loss-versus-temperature chart") and never name the file, so
the parser correctly refused to invent the filename. A deterministic
corpus-grounding step was added: when an email expresses figure-relabel intent
without a literal filename, it resolves the intended figure by matching the
reviewer's words against the corpus graphics manifest's captions and titles for
the referenced section, and emits a relabel operation carrying the exact filename
from the manifest. The filename always comes verbatim from the corpus, so there
is still zero fabrication. With this in place, draft + authored-manifest + model
reaches a clean 100% on all six projects.

## Recommendation (the chosen Phase-3 architecture)

- **Winner: authored manifest (S1) + deterministic reconcile baseline, with the
  model agent refining the natural-language correction-parse on top** — the
  "Option B" design. The deterministic engine is the always-on floor (offline,
  reproducible, zero fabrication); the model sharpens the email→operation parse
  and conflict detection when available, and never replaces the grounded engine.
- **Draft pathway** (corpus + first-draft + corrections) is the primary,
  provably-clean path. The template pathway stays supported but scores lower on
  the draft-tuned metric because of template boilerplate.
- **Practical implication:** the product needs the user (or an upstream authoring
  step) to supply the field manifest. A model cannot reliably invent it, and
  body-level alone cannot carry discrete-field corrections.

## Limitations

The six sample projects are small by design. The matrix proves correctness,
determinism, no-fabrication, conflict preservation, and render integrity at that
scale, but it does not by itself show how precision behaves as documents grow —
that is the job of the [precision-correction alpha loop](precision-correction-alpha-loop.md).
