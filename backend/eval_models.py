"""Alpha-loop model evaluation: score + cost comparison for scenario generation.

For each approved model, generate scenarios from a FIXED set of briefs N times,
record the objective per-run metrics (from the validator + Bedrock usage), and
print an aggregated comparison table. Writes eval_models_report.json.

Every score is measured, not judged:
  valid_first_try%   raw model output passed validation with no repair
  repaired%          needed one model repair round
  salvaged%          kept via dropping bad items (good work, minor fixes)
  fell_back%         model unusable -> deterministic generator (worst)
  fabrication/run    corrections the model invented (validator caught) - LOWER IS BETTER
  conflict% / nr%    richness: produced the required conflict / needs_review
Cost (tokens + latency are ground truth; $ is a pinned-table estimate):
  in/out tokens, latency, est $ per run

Usage:
  python backend/eval_models.py                 # all approved models, N=3
  python backend/eval_models.py --runs 5        # N=5
  python backend/eval_models.py --models nvidia.nemotron-super-3-120b,openai.gpt-oss-120b-1:0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.scenariogen.bedrock_gen import BedrockScenarioGenerator, list_approved_models  # noqa: E402
from app.scenariogen.generator import ScenarioBrief  # noqa: E402
from app.scenariogen.metrics import PRICE_TABLE_PINNED  # noqa: E402

# Fixed, deterministic briefs covering a few document types.
BRIEFS = [
    ScenarioBrief(domain="avionics interface validation",
                  doc_type="interface control document", title="Nav Bus ICD"),
    ScenarioBrief(domain="hardware reliability / incident response",
                  doc_type="incident report", title="Gateway Outage Report"),
    ScenarioBrief(domain="clinical laboratory safety",
                  doc_type="safety event report", title="Reagent Spill Report"),
]


def _aggregate(runs: list[dict]) -> dict:
    n = len(runs)
    if n == 0:
        return {}
    def pct(key: str) -> float:
        return round(100.0 * sum(1 for r in runs if r.get(key)) / n, 1)
    def avg(key: str) -> float:
        return round(sum(r.get(key, 0) for r in runs) / n, 2)
    return {
        "runs": n,
        "valid_first_try_pct": pct("valid_first_try"),
        "repaired_pct": round(100.0 * sum(1 for r in runs if r.get("repair_rounds", 0) > 0) / n, 1),
        "fell_back_pct": pct("fell_back"),
        "fabrication_per_run": avg("fabrication_rejections"),
        "salvage_dropped_per_run": avg("salvage_dropped"),
        "conflict_pct": pct("has_conflict"),
        "needs_review_pct": pct("has_needs_review"),
        "avg_input_tokens": avg("input_tokens"),
        "avg_output_tokens": avg("output_tokens"),
        "avg_latency_ms": avg("latency_ms"),
        "avg_est_usd": round(sum(r.get("est_usd", 0) for r in runs) / n, 6),
    }


def _print_table(report: dict) -> None:
    cols = [
        ("model", 30), ("runs", 5), ("valid1st%", 10), ("repaired%", 10),
        ("fellback%", 10), ("fab/run", 8), ("conflict%", 10),
        ("in_tok", 8), ("out_tok", 8), ("lat_ms", 8), ("est_$", 9),
    ]
    header = "  ".join(name.ljust(w) for name, w in cols)
    print("\n" + header)
    print("-" * len(header))
    for row in report["models"]:
        a = row["aggregate"]
        vals = [
            row["model"][:30], str(a["runs"]), f'{a["valid_first_try_pct"]}',
            f'{a["repaired_pct"]}', f'{a["fell_back_pct"]}',
            f'{a["fabrication_per_run"]}', f'{a["conflict_pct"]}',
            f'{a["avg_input_tokens"]:.0f}', f'{a["avg_output_tokens"]:.0f}',
            f'{a["avg_latency_ms"]:.0f}', f'{a["avg_est_usd"]:.5f}',
        ]
        print("  ".join(v.ljust(w) for v, (_, w) in zip(vals, cols)))
    print(f"\n($ is an ESTIMATE from a pinned price table: {report['price_pinned']}; "
          "tokens + latency are measured from Bedrock.)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="generations per model per brief")
    ap.add_argument("--models", type=str, default="", help="comma-separated model ids (default: all approved)")
    args = ap.parse_args()

    info = list_approved_models()
    if not info["available"]:
        print("Bedrock not available in this environment "
              f"(enabled={info['bedrock_enabled']}, error={info.get('error','none')}).")
        print("The eval harness needs live Bedrock access + approved models. Skipping.")
        return

    if args.models.strip():
        model_ids = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        model_ids = [m["id"] for m in info["models"]]

    print(f"Evaluating {len(model_ids)} model(s) x {len(BRIEFS)} brief(s) x {args.runs} run(s)...")
    report = {"price_pinned": PRICE_TABLE_PINNED, "region": info["region"], "models": []}

    for mid in model_ids:
        print(f"\n== {mid} ==")
        runs: list[dict] = []
        try:
            gen = BedrockScenarioGenerator(model_id=mid)
        except Exception as exc:
            print(f"  skip ({exc})")
            continue
        for brief in BRIEFS:
            for i in range(args.runs):
                res = gen.generate_with_metrics(brief)
                m = res.metrics.as_dict()
                runs.append(m)
                flag = "FELLBACK" if m["fell_back"] else ("ok1st" if m["valid_first_try"] else "salvaged")
                print(f"  {brief.title[:24]:24s} run {i + 1}: {flag:9s} "
                      f"fab={m['fabrication_rejections']} out_tok={m['output_tokens']} "
                      f"lat={m['latency_ms']}ms ${m['est_usd']:.5f}")
        report["models"].append({"model": mid, "aggregate": _aggregate(runs), "runs": runs})

    out = Path(__file__).resolve().parent / "eval_models_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _print_table(report)
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
