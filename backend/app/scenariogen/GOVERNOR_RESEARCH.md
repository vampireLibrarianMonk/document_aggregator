# Governor research: decision models and the author/adjudicator split

Outside research that shaped the governor design. All sources are paraphrased
and cited; nothing is reproduced verbatim beyond short identifying phrases.
Content was rephrased for compliance with licensing restrictions.

## The finding that reframed the design: JEV-style decision models

A recent line of work introduces **decision models** (the "JEV" family and
look-alikes, sometimes called "System One" models). Unlike a chat LLM, a decision
model does **not emit free-form text**. It takes a piece of state plus a bounded
set of options and returns a **typed answer with a calibrated probability** per
option: a choice, a yes/no, or a score that application code can act on directly.
([Jev: System One Decision Model](https://aijev.org/);
[What Is Jev — OpenRouter](https://openrouter.ai/blog/insights/what-is-jev/))

Why this is relevant to a governor: a governor's job is a stream of **small,
repeatable, bounded choices** - is this filled section grounded in the corpus?
should we retry, downshift to a smaller model, or accept? Those are exactly the
"small repeatable choice" a decision model is built for, and because the step
needs no long output it is faster and cheaper than asking a full LLM to decide.
([A Practical Guide to Decision Models](https://huggingface.co/blog/a2aprotocol/openai-decisions-api-vs-jev-a-practical-guide);
[Jev vs Kev vs LLMs](https://aimultiple.com/decision-models))

### Supporting results

- **Latency / cost.** Substituting decision models for LLMs on bounded,
  latency-sensitive orchestration paths lowered decision latency and API fees in
  the measured service path.
  ([Replacing LLMs with Jev for Edge Orchestration](https://arxiv.org/abs/2609.22753))
- **Accept-when-confident, escalate-when-unsure.** A decision-only judge returns
  label probabilities; its confidence decides whether to accept its own verdict
  or escalate to a slower reasoning judge. This is a confidence-gated cascade.
  ([JEV-as-a-Judge](https://arxiv.org/abs/2609.26550))
- **Separate who-decides from who-generates.** Agent harnesses often delegate
  critical adjudication (confirm a finding, grade severity, pick the next step)
  to the same generative model that produced the content; a dedicated decision
  layer separates these roles.
  ([JEV and Laya as System One Decision Layers](https://arxiv.org/html/2609.28940))
- **The pattern is portable.** An LLM can be constrained to behave as a
  JEV-style decision model by having it return a categorical distribution over
  predefined options instead of prose.
  ([LLMs Are Already Jev-Style Decision Models](https://arxiv.org/abs/2610.02076))

## Design principles adopted (and why they fit THIS system)

1. **Separate author from adjudicator.** The generation model (Nemotron /
   GPT-OSS) AUTHORS content. A separate **adjudicator** makes the governor's
   control decisions. This keeps the "model is author only" rule intact and lets
   us swap the decision mechanism without touching generation.

2. **Decisions are bounded and typed, not prose.** Every governor decision is a
   small enum verdict with a confidence (e.g. `accept | needs_review | retry |
   downshift`), so application code acts on it directly - no second round of
   fragile JSON parsing.

3. **Confidence-gated escalation (cascade).** Accept a cheap verdict when
   confident; escalate to a stronger check only when unsure. This is the
   cost-efficient shape the research measured, and it is the same idea as the
   generator's own retry/downshift ladder, applied to decisions.

4. **Cost is a governed resource.** Because decomposition multiplies the number
   of calls (plan + N fills + N proofreads + reconcile), cheap-per-call does not
   mean cheap-per-document. The governor budgets total calls/tokens per document
   and the adjudicator's whole point is to keep the decision calls cheap.

5. **Allowlist-only, no new vendor model.** We do NOT add a JEV vendor model
   (that would break the Nemotron/GPT-OSS allowlist and the air-gap posture).
   Instead we implement a **JEV-STYLE decision layer using an approved model**
   constrained to a typed verdict - which the research says is legitimate
   ("LLMs are already JEV-style decision models"). The deterministic validator
   remains available as a zero-cost adjudicator and as ground truth.

## How this maps to the three adjudicators we will test

- **Deterministic adjudicator** - the existing validator / corpus-grounding
  checks make the decisions. No model, zero cost, and it is the GROUND TRUTH we
  score the others against.
- **Generator-as-judge adjudicator** - the same capable LLM that fills also
  decides (one model does both). Simplest; most tokens; the "no separation"
  baseline the agent research warns about.
- **JEV-style decision-layer adjudicator** - a small/cheap approved model emits
  a bounded typed verdict + confidence for each decision; accept-when-confident,
  escalate (to the deterministic check or a stronger model) when unsure.

The open question the harness answers: does a JEV-style cheap decision layer,
built from our own approved models, govern as well as generator-as-judge at lower
cost, and is its confidence actually calibrated against the deterministic ground
truth?
