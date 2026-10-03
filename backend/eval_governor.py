"""Governor evaluation: compare adjudicator strategies for the governed pipeline.

A controlled experiment over the governor's pluggable decision layer:
  factors    ADJUDICATOR  x  BRIEF (document type)
  replicates N runs per cell
  measure    per-DOCUMENT objective signals from the governed run + a
             DECISION-QUALITY signal (does the adjudicator agree with the
             deterministic ground truth)

Adjudicators (all approved-model-only; see GOVERNOR_RESEARCH.md):
  deterministic     validator/grounding makes the call (ground truth, zero cost)
  generator_judge   the author model also decides
  decision_layer    a small cheap model returns a typed verdict + confidence,
                    escalating to the deterministic check when unsure (JEV-style)

Per-document metrics:
  sections_filled / needs_review / rejects   governed outcome
  fabrications_caught                         no-fabrication guarantee at work
  fell_back                                   governed result not persistable
  decision_agreement                          fraction of adjudicator verdicts
                                              that matched the deterministic truth
                                              (calibration of a cheap judge)
  tokens / latency / est $                    cost (measured on the live path;
                                              zero on the offline path)

OFFLINE by default: the deterministic author + offline adjudicators run with NO
Bedrock, which proves the decomposition + decision logic for free. Pass
--live to use a Bedrock author/judge (costs money; needs approved creds).

Usage:
  python backend/eval_governor.py                      # offline, all adjudicators, N=3
  python backend/eval_governor.py --runs 5 --briefs 4
  python backend/eval_governor.py --adjudicators deterministic,decision_layer
  python backend/eval_governor.py --live --model openai.gpt-oss-120b-1:0   # paid
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.scenariogen.generator import ScenarioBrief  # noqa: E402
from app.scenariogen.governor import (  # noqa: E402
    ADJUDICATORS,
    make_adjudicator,
    run_governed,
)

# Reuse the differentiated document types from the model eval, deterministic.
BRIEFS = [
    ScenarioBrief(domain="avionics interface validation",
                  doc_type="interface control document", title="Nav Bus ICD"),
    ScenarioBrief(domain="hardware reliability / incident response",
                  doc_type="incident report", title="Gateway Outage Report"),
    ScenarioBrief(domain="clinical laboratory safety",
                  doc_type="safety event report", title="Reagent Spill Report"),
    ScenarioBrief(domain="environmental field sampling",
                  doc_type="test report", title="Groundwater Sampling Test Report"),
    ScenarioBrief(domain="manufacturing quality assurance",
                  doc_type="standard operating procedure", title="Line Changeover SOP"),
    ScenarioBrief(domain="structural engineering review",
                  doc_type="analysis memo", title="Beam Deflection Analysis Memo"),
]


def _build_adjudicator(name: str, live: bool, model_id: str | None):
    """Offline: always the deterministic implementation (model-based ones
    degrade with no client). Live: wire a Bedrock client + adapter."""
    if not live:
        return make_adjudicator(name), "offline"
    # Live path: construct a Bedrock client + adapter for the judge model.
    import boto3
    from app.config import settings
    from app.scenariogen.model_adapters import adapter_for
    client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
    jm = model_id or settings.BEDROCK_SCENARIO_MODEL
    return make_adjudicator(name, model_id=jm, client=client,
                            adapter=adapter_for(jm)), jm


def _author(live: bool, model_id: str | None):
    """Offline: deterministic generator. Live: a Bedrock generator."""
    if not live:
        from app.scenariogen.rule_generator import RuleScenarioGenerator
        return RuleScenarioGenerator(), "offline"
    from app.scenariogen.bedrock_gen import BedrockScenarioGenerator
    gen = BedrockScenarioGenerator(model_id=model_id)
    return gen, gen.model_id


def _aggregate(runs: list[dict]) -> dict:
    n = len(runs)
    if n == 0:
        return {"runs": 0}

    def avg(key: str) -> float:
        vals = [r[key] for r in runs if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def pct(key: str) -> float:
        return round(100.0 * sum(1 for r in runs if r.get(key)) / n, 1)

    return {
        "runs": n,
        "avg_sections_filled": avg("sections_filled"),
        "avg_needs_review": avg("sections_needs_review"),
        "avg_rejects": avg("rejects"),
        "avg_fabrications_caught": avg("fabrications_caught"),
        "fell_back_pct": pct("fell_back"),
        "avg_decision_agreement": avg("decision_agreement"),
        "avg_decisions": avg("decisions_total"),
        "avg_output_tokens": avg("output_tokens"),
        "avg_latency_ms": avg("latency_ms"),
        "avg_est_usd": avg("est_usd"),
    }


def _print_table(report: dict) -> None:
    cols = [
        ("adjudicator", 18), ("n", 4), ("filled", 7), ("needs_rev", 10),
        ("rejects", 8), ("fab", 5), ("fellback%", 10), ("agree", 7),
        ("decisions", 10), ("out_tok", 8), ("est_$", 9),
    ]
    header = "  ".join(name.ljust(w) for name, w in cols)
    print("\n=== Governor adjudicator comparison (pooled over briefs) ===")
    print(header)
    print("-" * len(header))
    for row in report["adjudicators"]:
        a = row["aggregate"]
        if not a.get("runs"):
            continue
        vals = [
            row["adjudicator"][:18], str(a["runs"]), f'{a["avg_sections_filled"]}',
            f'{a["avg_needs_review"]}', f'{a["avg_rejects"]}',
            f'{a["avg_fabrications_caught"]}', f'{a["fell_back_pct"]}',
            f'{a["avg_decision_agreement"]}', f'{a["avg_decisions"]}',
            f'{a["avg_output_tokens"]:.0f}', f'{a["avg_est_usd"]:.5f}',
        ]
        print("  ".join(v.ljust(w) for v, (_, w) in zip(vals, cols)))
    print("\n(agree = fraction of adjudicator verdicts matching the deterministic "
          "ground truth. Offline runs have zero tokens/$: the decomposition + "
          "decision logic is exercised without Bedrock.)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--briefs", type=int, default=0, help="first K briefs (default all)")
    ap.add_argument("--adjudicators", type=str, default="",
                    help=f"comma list (default all: {','.join(ADJUDICATORS)})")
    ap.add_argument("--live", action="store_true",
                    help="use a Bedrock author + judge (COSTS MONEY)")
    ap.add_argument("--model", type=str, default="",
                    help="author/judge model id for --live")
    args = ap.parse_args()

    adjs = ([a.strip() for a in args.adjudicators.split(",") if a.strip()]
            if args.adjudicators.strip() else ADJUDICATORS)
    briefs = BRIEFS[:args.briefs] if args.briefs > 0 else BRIEFS
    model_id = args.model.strip() or None

    author, author_model = _author(args.live, model_id)
    started = time.time()
    mode = "LIVE (Bedrock, paid)" if args.live else "OFFLINE (deterministic, free)"
    print(f"Governor eval [{mode}]: {len(adjs)} adjudicator(s) x {len(briefs)} "
          f"brief(s) x {args.runs} run(s). Author: {author_model}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "offline",
        "author_model": author_model,
        "design": {"adjudicators": adjs, "briefs": [b.title for b in briefs],
                   "runs_per_cell": args.runs},
        "adjudicators": [],
    }

    for name in adjs:
        print(f"\n== adjudicator: {name} ==")
        runs: list[dict] = []
        for brief in briefs:
            for i in range(args.runs):
                adj, _ = _build_adjudicator(name, args.live, model_id)
                res = run_governed(brief, author=author, adjudicator=adj,
                                   author_model=author_model)
                s = res.summary()
                runs.append(s)
                print(f"  {brief.title[:24]:24s} run {i + 1}: "
                      f"filled={s['sections_filled']} nr={s['sections_needs_review']} "
                      f"rej={s['rejects']} fab={s['fabrications_caught']} "
                      f"fellback={s['fell_back']} agree={s['decision_agreement']}")
        report["adjudicators"].append({"adjudicator": name, "aggregate": _aggregate(runs),
                                       "runs": runs})

    report["elapsed_sec"] = round(time.time() - started, 1)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent
    out = out_dir / f"eval_governor_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "eval_governor_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")

    _print_table(report)
    print(f"\nElapsed: {report['elapsed_sec']}s. Report: {out.name}")


if __name__ == "__main__":
    main()
