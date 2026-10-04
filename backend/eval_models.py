"""Alpha-loop model evaluation: a scientific score + cost experiment.

Design (a controlled experiment, one factor at a time where possible):
  factors    MODEL  x  PROMPT STRATEGY  x  BRIEF (document type)
  replicates N runs per cell (default 3; raise for stability)
  measure    objective per-run metrics from the strict validator + Bedrock usage
             (nothing is a subjective "quality" rating)

Metrics (all MEASURED unless noted):
  valid_first_try%   raw model output passed validation with no repair  (higher better)
  repaired%          needed one model repair round                       (lower better)
  salvaged%          kept by dropping bad items (minor automatic fixes)
  fell_back%         model unusable -> deterministic generator (worst)   (lower better)
  fabrication/run    corrections the model invented, validator caught    (LOWER better)
  conflict% / nr%    richness: produced the required conflict/needs_review (higher better)
  in/out tokens      Bedrock usage (GROUND TRUTH)
  latency_ms         Bedrock metrics (GROUND TRUTH)
  est $/run          tokens x PINNED price table (ESTIMATE, not an AWS API)

Composite (transparent, not a black box) - "usable_score" in [0,1]:
  starts at 1.0 per run; subtract penalties:
    fell_back            -> 1.00  (unusable without the deterministic safety net)
    else repaired        -> 0.25
    else salvaged        -> 0.10
    valid_first_try      -> 0.00
    + 0.10 per fabrication the validator had to reject (capped)
  usable_score = 1 - mean(penalty).  We SHOW the components; the single number is
  only a convenience for ranking. Ties/closeness are expected at small N.

Usage:
  python backend/eval_models.py                       # all models x all prompts, N=3
  python backend/eval_models.py --runs 5
  python backend/eval_models.py --prompts baseline,strict_schema
  python backend/eval_models.py --models nvidia.nemotron-nano-9b-v2 --briefs 2
  python backend/eval_models.py --quick               # 2 models, baseline only, N=1 (smoke)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projectgen.bedrock_gen import BedrockProjectGenerator, list_approved_models  # noqa: E402
from app.projectgen.generator import ProjectBrief  # noqa: E402
from app.projectgen.metrics import PRICE_TABLE_PINNED  # noqa: E402
from app.projectgen.prompts import list_prompt_strategies  # noqa: E402

# Fixed, deterministic briefs covering a spread of document types. Ordered so
# `--briefs K` takes a stable prefix (ICD, incident, lab-safety are the core 3).
BRIEFS = [
    ProjectBrief(domain="avionics interface validation",
                  doc_type="interface control document", title="Nav Bus ICD"),
    ProjectBrief(domain="hardware reliability / incident response",
                  doc_type="incident report", title="Gateway Outage Report"),
    ProjectBrief(domain="clinical laboratory safety",
                  doc_type="safety event report", title="Reagent Spill Report"),
    ProjectBrief(domain="environmental field sampling",
                  doc_type="test report", title="Groundwater Sampling Test Report"),
    ProjectBrief(domain="manufacturing quality assurance",
                  doc_type="standard operating procedure", title="Line Changeover SOP"),
    ProjectBrief(domain="structural engineering review",
                  doc_type="analysis memo", title="Beam Deflection Analysis Memo"),
]


def _usable_penalty(r: dict) -> float:
    """Per-run penalty in [0,1+]; see module docstring. Lower is better."""
    if r.get("fell_back"):
        return 1.0
    if r.get("repair_rounds", 0) > 0:
        base = 0.25
    elif not r.get("valid_first_try"):
        base = 0.10  # salvaged
    else:
        base = 0.0
    base += min(0.30, 0.10 * r.get("fabrication_rejections", 0))
    return min(1.0, base)


def _aggregate(runs: list[dict]) -> dict:
    n = len(runs)
    if n == 0:
        return {"runs": 0}

    def pct(key: str) -> float:
        return round(100.0 * sum(1 for r in runs if r.get(key)) / n, 1)

    def avg(key: str) -> float:
        return round(sum(r.get(key, 0) for r in runs) / n, 2)

    def spread(key: str) -> float:
        return round(statistics.pstdev([r.get(key, 0) for r in runs]), 1) if n > 1 else 0.0

    penalties = [_usable_penalty(r) for r in runs]
    return {
        "runs": n,
        "usable_score": round(1.0 - (sum(penalties) / n), 3),
        "valid_first_try_pct": pct("valid_first_try"),
        "repaired_pct": round(100.0 * sum(1 for r in runs if r.get("repair_rounds", 0) > 0) / n, 1),
        "salvaged_pct": round(100.0 * sum(
            1 for r in runs
            if not r.get("fell_back") and not r.get("valid_first_try")
            and r.get("repair_rounds", 0) == 0) / n, 1),
        "fell_back_pct": pct("fell_back"),
        "fabrication_per_run": avg("fabrication_rejections"),
        "salvage_dropped_per_run": avg("salvage_dropped"),
        "conflict_pct": pct("has_conflict"),
        "needs_review_pct": pct("has_needs_review"),
        "avg_input_tokens": avg("input_tokens"),
        "avg_output_tokens": avg("output_tokens"),
        "out_tokens_sd": spread("output_tokens"),
        "avg_latency_ms": avg("latency_ms"),
        "latency_ms_sd": spread("latency_ms"),
        "avg_est_usd": round(sum(r.get("est_usd", 0) for r in runs) / n, 6),
    }


def _print_cell_table(report: dict) -> None:
    """Per (model, prompt) rows, sorted by the transparent composite."""
    cols = [
        ("model", 26), ("prompt", 20), ("n", 4), ("score", 7), ("valid1st%", 10),
        ("fellback%", 10), ("fab/run", 8), ("conflict%", 10),
        ("out_tok", 9), ("lat_ms", 8), ("est_$", 9),
    ]
    header = "  ".join(name.ljust(w) for name, w in cols)
    print("\n=== Per model x prompt (sorted by usable_score, then est_$) ===")
    print(header)
    print("-" * len(header))
    rows = sorted(
        report["cells"],
        key=lambda c: (-c["aggregate"].get("usable_score", 0),
                       c["aggregate"].get("avg_est_usd", 9)),
    )
    for row in rows:
        a = row["aggregate"]
        if not a.get("runs"):
            continue
        vals = [
            row["model"].split(".")[-1][:26], row["prompt"][:20], str(a["runs"]),
            f'{a["usable_score"]}', f'{a["valid_first_try_pct"]}',
            f'{a["fell_back_pct"]}', f'{a["fabrication_per_run"]}', f'{a["conflict_pct"]}',
            f'{a["avg_output_tokens"]:.0f}', f'{a["avg_latency_ms"]:.0f}',
            f'{a["avg_est_usd"]:.5f}',
        ]
        print("  ".join(v.ljust(w) for v, (_, w) in zip(vals, cols)))


def _print_prompt_rollup(report: dict) -> None:
    """Does a prompt strategy help on average (across all models)?"""
    by_prompt: dict[str, list[dict]] = {}
    for cell in report["cells"]:
        for r in cell["runs"]:
            by_prompt.setdefault(cell["prompt"], []).append(r)
    print("\n=== Prompt strategy roll-up (all models pooled) ===")
    print("prompt                n     score   valid1st%  fellback%  fab/run")
    print("-" * 66)
    for prompt, runs in sorted(by_prompt.items()):
        a = _aggregate(runs)
        print(f'{prompt[:20]:20s}  {a["runs"]:<4}  {a["usable_score"]:<6}  '
              f'{a["valid_first_try_pct"]:<9}  {a["fell_back_pct"]:<9}  '
              f'{a["fabrication_per_run"]}')


def _print_model_rollup(report: dict) -> None:
    """Best prompt per model + its usable score, pooled across briefs."""
    by_model: dict[str, dict[str, list[dict]]] = {}
    for cell in report["cells"]:
        by_model.setdefault(cell["model"], {})[cell["prompt"]] = cell["runs"]
    print("\n=== Best prompt per model (pooled across briefs) ===")
    print("model                          best_prompt           score   fellback%")
    print("-" * 74)
    ranked = []
    for model, prompts in by_model.items():
        best = max(prompts.items(), key=lambda kv: _aggregate(kv[1]).get("usable_score", 0))
        a = _aggregate(best[1])
        ranked.append((a["usable_score"], model, best[0], a["fell_back_pct"]))
    for score, model, prompt, fb in sorted(ranked, reverse=True):
        print(f'{model.split(".")[-1][:30]:30s}  {prompt[:20]:20s}  {score:<6}  {fb}')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3, help="replicates per cell (default 3)")
    ap.add_argument("--models", type=str, default="",
                    help="comma-separated model ids (default: all approved)")
    ap.add_argument("--prompts", type=str, default="",
                    help="comma-separated prompt strategies (default: all)")
    ap.add_argument("--briefs", type=int, default=0,
                    help="use the first K briefs (default: all)")
    ap.add_argument("--quick", action="store_true",
                    help="smoke run: 2 models, baseline only, N=1")
    args = ap.parse_args()

    info = list_approved_models()
    if not info["available"]:
        print("Bedrock not available in this environment "
              f"(enabled={info['bedrock_enabled']}, error={info.get('error', 'none')}).")
        print("The eval harness needs live Bedrock access + approved models. Skipping.")
        return

    all_models = [m["id"] for m in info["models"]]
    all_prompts = list_prompt_strategies()

    if args.quick:
        model_ids = all_models[:2]
        prompts = ["baseline"]
        runs_n = 1
        briefs = BRIEFS[:2]
    else:
        model_ids = ([m.strip() for m in args.models.split(",") if m.strip()]
                     if args.models.strip() else all_models)
        prompts = ([p.strip() for p in args.prompts.split(",") if p.strip()]
                   if args.prompts.strip() else all_prompts)
        runs_n = args.runs
        briefs = BRIEFS[:args.briefs] if args.briefs > 0 else BRIEFS

    total_cells = len(model_ids) * len(prompts)
    total_gens = total_cells * len(briefs) * runs_n
    started = time.time()
    print(f"Experiment: {len(model_ids)} model(s) x {len(prompts)} prompt(s) x "
          f"{len(briefs)} brief(s) x {runs_n} run(s) = {total_gens} generations.")
    print(f"Models:  {', '.join(m.split('.')[-1] for m in model_ids)}")
    print(f"Prompts: {', '.join(prompts)}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "price_pinned": PRICE_TABLE_PINNED,
        "region": info["region"],
        "design": {
            "models": model_ids, "prompts": prompts,
            "briefs": [b.title for b in briefs], "runs_per_cell": runs_n,
        },
        "cells": [],
    }

    done = 0
    for mid in model_ids:
        for prompt in prompts:
            print(f"\n== {mid.split('.')[-1]}  [{prompt}] ==")
            try:
                gen = BedrockProjectGenerator(model_id=mid, prompt_strategy=prompt)
            except Exception as exc:
                print(f"  skip ({exc})")
                report["cells"].append(
                    {"model": mid, "prompt": prompt, "error": str(exc),
                     "aggregate": {"runs": 0}, "runs": []})
                continue
            runs: list[dict] = []
            for brief in briefs:
                for i in range(runs_n):
                    try:
                        res = gen.generate_with_metrics(brief)
                        m = res.metrics.as_dict()
                    except Exception as exc:  # never let one cell kill the run
                        print(f"  {brief.title[:22]:22s} run {i + 1}: ERROR {exc}")
                        done += 1
                        continue
                    runs.append(m)
                    done += 1
                    flag = ("FELLBACK" if m["fell_back"]
                            else ("ok1st" if m["valid_first_try"]
                                  else ("repaired" if m["repair_rounds"] else "salvaged")))
                    print(f"  [{done}/{total_gens}] {brief.title[:22]:22s} run {i + 1}: "
                          f"{flag:9s} fab={m['fabrication_rejections']} "
                          f"out_tok={m['output_tokens']} lat={m['latency_ms']}ms "
                          f"${m['est_usd']:.5f}")
            report["cells"].append(
                {"model": mid, "prompt": prompt,
                 "aggregate": _aggregate(runs), "runs": runs})

    report["elapsed_sec"] = round(time.time() - started, 1)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent
    out = out_dir / f"eval_models_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest = out_dir / "eval_models_report.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")

    _print_cell_table(report)
    _print_prompt_rollup(report)
    _print_model_rollup(report)
    print(f"\n($ is an ESTIMATE from a pinned price table: {report['price_pinned']}; "
          "tokens + latency are measured from Bedrock.)")
    print(f"Elapsed: {report['elapsed_sec']}s. Reports: {out.name} (+ eval_models_report.json)")


if __name__ == "__main__":
    main()
