"""Alpha-loop A/B: does the per-model token budget fix rescue starved models?

Isolates ONE variable -- the output token budget -- for the small reasoning
models that truncated at the old flat 8192 cap. For each (model, prompt, brief)
it generates once at the OLD budget (8192) and once at the model's PROFILE
budget, and reports whether a fallback/truncation turned into usable output.

This is a controlled before/after, not the full grid (eval_models.py). Run it
AFTER the main experiment so the two do not contend for Bedrock throttle budget.

Usage:
  python backend/eval_budget_ab.py                      # the 20b reasoning models
  python backend/eval_budget_ab.py --models openai.gpt-oss-20b-1:0 --runs 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projectgen.bedrock_gen import BedrockProjectGenerator  # noqa: E402
from app.projectgen.generator import ProjectBrief  # noqa: E402
from app.projectgen.model_profiles import profile_for  # noqa: E402

OLD_FLAT_BUDGET = 8192

# The models the eval flagged as starved (small reasoning models).
DEFAULT_MODELS = ["openai.gpt-oss-20b-1:0", "openai.gpt-oss-safeguard-20b"]

BRIEFS = [
    ProjectBrief(domain="avionics interface validation",
                  doc_type="interface control document", title="Nav Bus ICD"),
    ProjectBrief(domain="hardware reliability / incident response",
                  doc_type="incident report", title="Gateway Outage Report"),
]
PROMPTS = ["baseline", "reasoning_suppressed"]


def _run(model_id: str, prompt: str, brief: ProjectBrief, budget: int) -> dict:
    """Generate once at a FORCED token budget (override the profile)."""
    gen = BedrockProjectGenerator(model_id=model_id, prompt_strategy=prompt)
    gen._profile = gen._profile.__class__(  # force the budget for this call
        family=gen._profile.family, is_reasoning=gen._profile.is_reasoning,
        max_tokens=budget, default_prompt=gen._profile.default_prompt,
        note=f"forced {budget}")
    m = gen.generate_with_metrics(brief).metrics.as_dict()
    return m


def _label(m: dict) -> str:
    return ("FELLBACK" if m["fell_back"]
            else ("ok1st" if m["valid_first_try"]
                  else ("repaired" if m["repair_rounds"] else "salvaged")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=str, default="")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              if args.models.strip() else DEFAULT_MODELS)

    rescued = 0
    total = 0
    print(f"Budget A/B: OLD={OLD_FLAT_BUDGET} vs PROFILE, {len(models)} model(s), "
          f"{len(PROMPTS)} prompt(s), {len(BRIEFS)} brief(s), {args.runs} run(s)\n")
    for mid in models:
        prof_budget = profile_for(mid).max_tokens
        print(f"== {mid.split('.')[-1]}  (profile budget {prof_budget}) ==")
        for prompt in PROMPTS:
            for brief in BRIEFS:
                for _ in range(args.runs):
                    old = _run(mid, prompt, brief, OLD_FLAT_BUDGET)
                    new = _run(mid, prompt, brief, prof_budget)
                    total += 1
                    old_bad = old["fell_back"]
                    new_good = not new["fell_back"]
                    if old_bad and new_good:
                        rescued += 1
                    arrow = "  <== RESCUED" if (old_bad and new_good) else ""
                    print(f"  {prompt:20s} {brief.title[:20]:20s}  "
                          f"old[{OLD_FLAT_BUDGET}]={_label(old):9s} out={old['output_tokens']:5d}  "
                          f"-> new[{prof_budget}]={_label(new):9s} out={new['output_tokens']:5d}"
                          f"{arrow}")
    print(f"\nRescued {rescued}/{total} previously-fallback cases by right-sizing "
          "the token budget.")


if __name__ == "__main__":
    main()
