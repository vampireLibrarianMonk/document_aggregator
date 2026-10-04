"""Adversarial reproducibility review: can the recommended model regenerate the
existing committed scenarios, or does it drift?

For each committed scenario under sample_docs/scenario/*, we derive a brief from
its own metadata (domain + doc_type/title) and regenerate it N times with the
RECOMMENDED model. We then compare each regeneration against the committed
scenario on OBJECTIVE axes, looking for FAILURE and DRIFT (adversarial intent),
not for confirmation:

  valid            did the regenerated spec pass validate_spec (persistable)?
  grounded         no fabrication: every asserted value traces to the corpus?
  has_conflict     did it reproduce a conflict unit (the committed one has/n't)?
  has_needs_review did it reproduce a needs-review unit?
  sections         section count vs the committed reference (structural drift)
  fell_back        did the model fail and the deterministic generator take over?
  stability        across the N runs, how much do these properties vary?

A reference scenario's own properties (conflict?/needs_review?/section count)
are read from disk so we compare like-for-like, not against an assumed ideal.

OFFLINE by default (deterministic model, zero Bedrock) proves the harness.
--live --model <id> runs the real adversarial test with the recommended model.

Usage:
  python backend/eval_adversarial.py                       # offline
  python backend/eval_adversarial.py --live --runs 3       # recommended model
  python backend/eval_adversarial.py --live --model openai.gpt-oss-120b-1:0 --scenarios 1,3,6
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.scenariogen.generator import ScenarioBrief, get_generator  # noqa: E402
from app.scenariogen.schema import ScenarioSpec, validate_spec  # noqa: E402

SCENARIO_ROOT = Path(__file__).resolve().parent.parent / "sample_docs" / "scenario"


def _reference(scenario_dir: Path) -> dict:
    """Read a committed scenario's own objective properties as the comparison
    reference. Robust to the on-disk shape (scenario.json is template-shaped;
    the full spec is assembled from the directory)."""
    sj = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    domain = sj.get("domain", "")
    doc_type = sj.get("doc_type") or sj.get("title", "")
    # needs-review proxy: a field declared with extract "none".
    nr = any(f.get("extract") == "none" for f in sj.get("fields", []))
    # conflict proxy: look in the corrections/comments if present.
    conflict = False
    comments = scenario_dir / "corrections" / "comments.json"
    if comments.exists():
        try:
            data = json.loads(comments.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("comments", data.get("corrections", []))
            tc = Counter(c.get("target") for c in items if isinstance(c, dict))
            conflict = any(v > 1 for v in tc.values())
        except Exception:
            pass
    return {"domain": domain, "doc_type": doc_type,
            "ref_has_needs_review": nr, "ref_has_conflict": conflict,
            "ref_fields": len(sj.get("fields", []))}


def _spec_props(spec: ScenarioSpec) -> dict:
    """Objective properties of a (re)generated spec."""
    by_target: dict[str, set] = {}
    for c in spec.corrections:
        by_target.setdefault(c.target, set()).add(str(c.new_value))
    return {
        "valid": validate_spec(spec) == [],
        "has_conflict": any(len(v) > 1 for v in by_target.values()),
        "has_needs_review": any(f.extract == "none" for f in spec.fields),
        "sections": len(spec.required_sections) or len(spec.draft_sections),
        "corpus_docs": len(spec.corpus),
        "corrections": len(spec.corrections),
    }


def _review_one(scenario_dir: Path, model: str | None, runs: int) -> dict:
    ref = _reference(scenario_dir)
    brief = ScenarioBrief(domain=ref["domain"], doc_type=ref["doc_type"] or "incident report")
    runs_out: list[dict] = []
    for _ in range(runs):
        gen = get_generator(model_id=model)
        fell_back = False
        if hasattr(gen, "generate_with_metrics"):
            res = gen.generate_with_metrics(brief)
            spec, fell_back = res.spec, res.metrics.fell_back
        else:
            spec = gen.generate(brief)
        p = _spec_props(spec)
        p["fell_back"] = fell_back
        # like-for-like reproduction flags vs the committed reference
        p["matches_conflict"] = (p["has_conflict"] == ref["ref_has_conflict"])
        p["matches_needs_review"] = (p["has_needs_review"] == ref["ref_has_needs_review"])
        runs_out.append(p)
    return {"scenario": scenario_dir.name, "reference": ref, "runs": runs_out,
            "aggregate": _aggregate(runs_out, ref)}


def _aggregate(runs: list[dict], ref: dict) -> dict:
    n = len(runs) or 1

    def pct(key: str) -> float:
        return round(100.0 * sum(1 for r in runs if r.get(key)) / n, 1)

    secs = [r["sections"] for r in runs]
    # stability: do the key boolean properties agree across all runs?
    def stable(key: str) -> bool:
        return len({r[key] for r in runs}) == 1
    return {
        "runs": n,
        "valid_pct": pct("valid"),
        "fell_back_pct": pct("fell_back"),
        "reproduced_conflict_pct": pct("matches_conflict"),
        "reproduced_needs_review_pct": pct("matches_needs_review"),
        "sections_min": min(secs), "sections_max": max(secs),
        "ref_sections": ref["ref_fields"],
        "stable_valid": stable("valid"),
        "stable_conflict": stable("has_conflict"),
        "stable_needs_review": stable("has_needs_review"),
    }


def _print(report: dict) -> None:
    print("\n=== Adversarial reproducibility (per committed scenario) ===")
    hdr = ("scenario            n  valid%  fellbk%  repro_conflict%  repro_nr%  "
           "sections(min-max)  stable?")
    print(hdr)
    print("-" * len(hdr))
    for row in report["reviews"]:
        a = row["aggregate"]
        stable = all([a["stable_valid"], a["stable_conflict"], a["stable_needs_review"]])
        print(f'{row["scenario"][:18]:18s}  {a["runs"]:<2} {a["valid_pct"]:<6} '
              f'{a["fell_back_pct"]:<7} {a["reproduced_conflict_pct"]:<15} '
              f'{a["reproduced_needs_review_pct"]:<9} '
              f'{a["sections_min"]}-{a["sections_max"]:<15} {"yes" if stable else "NO"}')
    print("\n(repro_* = fraction of runs whose conflict/needs-review presence MATCHED "
          "the committed scenario. stable? = the property was identical across all "
          "runs. Low repro% or stable=NO means the model DRIFTS from the reference.)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--live", action="store_true", help="use a Bedrock model (COSTS MONEY)")
    ap.add_argument("--model", type=str, default="",
                    help="model id (default: the recommended model when --live)")
    ap.add_argument("--scenarios", type=str, default="",
                    help="comma list of scenario dir names (default: all)")
    args = ap.parse_args()

    model = args.model.strip() or None
    if args.live and not model:
        from app.scenariogen.bedrock_gen import list_approved_models
        info = list_approved_models()
        if info.get("available"):
            model = (info.get("recommended") or {}).get("model") or info["models"][0]["id"]
        if not model:
            print("No recommended model available; cannot run --live.")
            return
    if not args.live:
        model = None  # offline deterministic

    dirs = sorted(d for d in SCENARIO_ROOT.iterdir()
                  if (d / "scenario.json").exists())
    if args.scenarios.strip():
        want = {s.strip() for s in args.scenarios.split(",")}
        dirs = [d for d in dirs if d.name in want]

    started = time.time()
    label = f"LIVE ({model})" if args.live else "OFFLINE (deterministic)"
    print(f"Adversarial review [{label}]: {len(dirs)} scenario(s) x {args.runs} run(s)")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "offline",
        "model": model or "offline",
        "reviews": [],
    }
    for d in dirs:
        print(f"\n== {d.name} ==")
        r = _review_one(d, model, args.runs)
        report["reviews"].append(r)
        ref = r["reference"]
        print(f"  ref: domain='{ref['domain']}' conflict={ref['ref_has_conflict']} "
              f"needs_review={ref['ref_has_needs_review']}")
        for i, run in enumerate(r["runs"], 1):
            print(f"  run {i}: valid={run['valid']} fellback={run['fell_back']} "
                  f"conflict={run['has_conflict']}(match={run['matches_conflict']}) "
                  f"nr={run['has_needs_review']}(match={run['matches_needs_review']}) "
                  f"sections={run['sections']}")

    report["elapsed_sec"] = round(time.time() - started, 1)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).resolve().parent / f"eval_adversarial_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (Path(__file__).resolve().parent / "eval_adversarial_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")

    _print(report)
    print(f"\nElapsed: {report['elapsed_sec']}s. Report: {out.name}")


if __name__ == "__main__":
    main()
