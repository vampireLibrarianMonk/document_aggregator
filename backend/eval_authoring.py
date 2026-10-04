"""Authoring-mode evaluation: whole-document vs per-section authoring.

The model evaluation (MODEL_EVAL.md) left one lever untested: WHOLE-DOCUMENT
single-shot authoring starves small models (gpt-oss-20b, nemotron-nano-12b fell
back ~92%), because the whole ProjectSpec JSON is too big to emit in one call.
PER-SECTION authoring fills one section per call -- small output -- so a small
model should stop truncating. This harness measures that.

Design (one factor): AUTHORING MODE {whole_doc, per_section} x BRIEF x N.
The adjudicator is fixed to `deterministic` because the adjudicator eval
(GOVERNOR_EVAL.md) already showed it is the right choice; here we isolate
authoring.

Metrics per DOCUMENT: fell_back, sections filled/needs_review/rejects,
fabrications caught, author_calls, tokens/latency/est $.

OFFLINE by default (deterministic author, zero Bedrock) to prove the logic.
Pass --live --model <id> for the real test, especially with a SMALL model to
see whether per-section rescues it. Live costs money; a per-section live run
makes more calls than whole-doc (that is the point), so a hard per-document call
ceiling (GovernorBudget) bounds the spend.

Usage:
  python backend/eval_authoring.py                                  # offline
  python backend/eval_authoring.py --live --model openai.gpt-oss-20b-1:0 --runs 2 --briefs 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projectgen.generator import ProjectBrief  # noqa: E402
from app.projectgen.governor import (  # noqa: E402
    make_adjudicator,
    make_section_author,
    run_governed,
)

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

MODES = ["whole_doc", "per_section"]


def _run_once(mode: str, brief, live: bool, model_id: str | None):
    """One governed run in the given authoring mode."""
    adj = make_adjudicator("deterministic")  # fixed; authoring is the variable
    if mode == "per_section":
        if live and model_id:
            import boto3
            from app.config import settings
            from app.projectgen.model_adapters import adapter_for
            client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
            sa = make_section_author(model_id, client=client, adapter=adapter_for(model_id))
            author_model = model_id
        else:
            sa = make_section_author()           # offline deterministic
            author_model = "offline"
        return run_governed(brief, section_author=sa, adjudicator=adj,
                            author_model=author_model).summary()
    # whole_doc
    if live and model_id:
        from app.projectgen.bedrock_gen import BedrockProjectGenerator
        author = BedrockProjectGenerator(model_id=model_id)
        author_model = author.model_id
    else:
        from app.projectgen.rule_generator import RuleProjectGenerator
        author = RuleProjectGenerator()
        author_model = "offline"
    return run_governed(brief, author=author, adjudicator=adj,
                        author_model=author_model).summary()


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
        "fell_back_pct": pct("fell_back"),
        "avg_sections_filled": avg("sections_filled"),
        "avg_needs_review": avg("sections_needs_review"),
        "avg_rejects": avg("rejects"),
        "avg_fabrications_caught": avg("fabrications_caught"),
        "avg_author_calls": avg("author_calls"),
        "avg_output_tokens": avg("output_tokens"),
        "avg_latency_ms": avg("latency_ms"),
        "avg_est_usd": avg("est_usd"),
    }


def _print_table(report: dict) -> None:
    cols = [
        ("mode", 13), ("n", 4), ("fellback%", 10), ("filled", 8), ("rejects", 8),
        ("fab", 5), ("calls", 7), ("out_tok", 9), ("lat_ms", 8), ("est_$", 9),
    ]
    header = "  ".join(name.ljust(w) for name, w in cols)
    print("\n=== Authoring mode comparison (pooled over briefs) ===")
    print(header)
    print("-" * len(header))
    for row in report["modes"]:
        a = row["aggregate"]
        if not a.get("runs"):
            continue
        vals = [
            row["mode"][:13], str(a["runs"]), f'{a["fell_back_pct"]}',
            f'{a["avg_sections_filled"]}', f'{a["avg_rejects"]}',
            f'{a["avg_fabrications_caught"]}', f'{a["avg_author_calls"]}',
            f'{a["avg_output_tokens"]:.0f}', f'{a["avg_latency_ms"]:.0f}',
            f'{a["avg_est_usd"]:.5f}',
        ]
        print("  ".join(v.ljust(w) for v, (_, w) in zip(vals, cols)))
    print("\n(The question: does per_section lower fell_back% for small models vs "
          "whole_doc? Offline both are deterministic and identical by design -- the "
          "difference only appears live with a real author model.)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--briefs", type=int, default=0, help="first K briefs (default all)")
    ap.add_argument("--modes", type=str, default="",
                    help=f"comma list (default both: {','.join(MODES)})")
    ap.add_argument("--live", action="store_true", help="use a Bedrock author (COSTS MONEY)")
    ap.add_argument("--model", type=str, default="", help="author model id for --live")
    args = ap.parse_args()

    modes = ([m.strip() for m in args.modes.split(",") if m.strip()]
             if args.modes.strip() else MODES)
    briefs = BRIEFS[:args.briefs] if args.briefs > 0 else BRIEFS
    model_id = args.model.strip() or None

    started = time.time()
    label = "LIVE (Bedrock, paid)" if args.live else "OFFLINE (deterministic, free)"
    print(f"Authoring eval [{label}]: {len(modes)} mode(s) x {len(briefs)} "
          f"brief(s) x {args.runs} run(s). Model: {model_id or 'offline'}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "offline",
        "model": model_id or "offline",
        "design": {"authoring_modes": modes, "briefs": [b.title for b in briefs],
                   "runs_per_cell": args.runs, "adjudicator": "deterministic"},
        "modes": [],
    }

    for mode in modes:
        print(f"\n== authoring: {mode} ==")
        runs: list[dict] = []
        for brief in briefs:
            for i in range(args.runs):
                try:
                    s = _run_once(mode, brief, args.live, model_id)
                except Exception as exc:   # never let one cell kill the run
                    print(f"  {brief.title[:24]:24s} run {i + 1}: ERROR {exc}")
                    continue
                runs.append(s)
                print(f"  {brief.title[:24]:24s} run {i + 1}: "
                      f"fellback={s['fell_back']} filled={s['sections_filled']} "
                      f"rej={s['rejects']} fab={s['fabrications_caught']} "
                      f"calls={s['author_calls']} out_tok={s['output_tokens']}")
        report["modes"].append({"mode": mode, "aggregate": _aggregate(runs), "runs": runs})

    report["elapsed_sec"] = round(time.time() - started, 1)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent
    out = out_dir / f"eval_authoring_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "eval_authoring_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")

    _print_table(report)
    print(f"\nElapsed: {report['elapsed_sec']}s. Report: {out.name}")


if __name__ == "__main__":
    main()
