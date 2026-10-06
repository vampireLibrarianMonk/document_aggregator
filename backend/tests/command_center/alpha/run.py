"""The scaling precision-correction alpha loop.

Sweeps the four precision-editing techniques across growing document sizes and
scores each on the growth dataset's known edits, so we can watch precision,
recall, and — the key scaling signal — the UNINTENDED-CHANGE count move as the
document gets bigger. Prints a per-size scorecard and a recommendation.

Run (deterministic techniques only, fully offline):
  cd backend
  $env:HF_HUB_OFFLINE=1; $env:TRANSFORMERS_OFFLINE=1; $env:DATA_DIR="$env:TEMP\a"
  ..\\.venv\\Scripts\\python.exe -m tests.command_center.alpha.run

Add the T3 model technique by also exporting BEDROCK_ENABLED=true + AWS creds.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.command_center.alpha.metric import aggregate, score_edits
from tests.command_center.alpha.techniques import techniques
from tests.command_center.alpha.units import (
    editable_ledger,
    flatten_draft,
    grounding_text,
    intents_from_corrections,
)
from tests.command_center.datagen import GrowthSpec, generate_project
from tests.command_center.modelcall import ModelClient, default_model

SIZES = [1, 2, 4, 8, 16]
SEEDS = [11, 23]                   # two seeds per size for a little averaging
RESULTS_PATH = Path(__file__).with_name("alpha_results.json")


def run_alpha() -> dict:
    model_client = None
    try:
        mc = ModelClient(default_model())
        if mc.available():
            model_client = mc
    except Exception:
        model_client = None
    model_on = model_client is not None

    techs = techniques(model_client)
    tech_names = ["T1-anchored", "T2-diff", "T4-micro"] + (["T3-model"] if model_on else [])

    scores = []
    per_size: dict[int, dict] = {}
    for size in SIZES:
        for seed in SEEDS:
            gp = generate_project(GrowthSpec(f"a_s{size}_{seed}", size=size, seed=seed))
            draft_units = flatten_draft(gp.draft)
            intents = intents_from_corrections(gp.corrections)
            ledger = editable_ledger([vars(e) for e in gp.edits], draft_units)
            ground = grounding_text(gp)
            for name in tech_names:
                produced = techs[name](draft_units, intents, {"grounding": ground})
                sc = score_edits(name, gp.project_id, size,
                                 draft_units=draft_units, produced_units=produced,
                                 ledger=ledger, grounding_text=ground)
                scores.append(sc)
                per_size.setdefault(size, {}).setdefault(name, []).append(sc)

    return {
        "model_on": model_on,
        "model_id": (model_client.model_id if model_client else ""),
        "sizes": SIZES, "seeds": SEEDS,
        "scores": [s.as_dict() for s in scores],
        "summary": aggregate(scores),
        "per_size": _per_size_table(per_size),
        "recommendation": _recommend(scores, model_on),
    }


def _per_size_table(per_size: dict) -> dict:
    """Average each technique's metrics at each size (the scaling curve)."""
    out: dict[str, dict] = {}
    for size, techs in sorted(per_size.items()):
        for name, lst in techs.items():
            n = len(lst)
            out.setdefault(name, {})[str(size)] = {
                "recall": round(sum(x.recall for x in lst) / n, 1),
                "precision": round(sum(x.precision for x in lst) / n, 1),
                "unintended": sum(x.unintended_changes for x in lst),
                "fabrications": sum(x.fabrications for x in lst),
                "conflict_preserved": all(x.conflict_preserved for x in lst),
            }
    return out


def _recommend(scores, model_on: bool) -> dict:
    agg = aggregate(scores)

    def safe(v) -> bool:
        return (v["total_fabrications"] == 0 and v["conflict_preserved_all"]
                and v["total_unintended"] == 0)

    ranked = sorted(
        agg.items(),
        key=lambda kv: (safe(kv[1]), kv[1]["avg_recall"], kv[1]["avg_precision"]),
        reverse=True)
    winner = ranked[0][0] if ranked else None
    return {
        "winner": winner,
        "ranking": [(k, v["avg_recall"], v["avg_precision"],
                     v["total_unintended"], v["total_fabrications"]) for k, v in ranked],
        "model_evaluated": model_on,
        "note": ("Winner maximizes recall+precision while holding the hard "
                 "invariants: 0 unintended changes, 0 fabrications, conflicts "
                 "preserved. The unintended-change column is the scaling signal "
                 "— a technique whose unintended count climbs with size is the "
                 "one that will drift on large documents."),
    }


def main() -> int:
    print("=== Precision-correction scaling alpha loop ===")
    report = run_alpha()
    print(f"model: {'ON ' + report['model_id'] if report['model_on'] else 'OFF (deterministic techniques only)'}")
    print("\n--- Scaling curve (per technique, per size) ---")
    print("  technique      size  recall  prec  unintended  fab  cpres")
    for name, bysize in sorted(report["per_size"].items()):
        for size in map(str, SIZES):
            row = bysize.get(size)
            if not row:
                continue
            print(f"  {name:<13} {size:>4}  {row['recall']:5.1f}% {row['precision']:5.1f}% "
                  f"{row['unintended']:>10}  {row['fabrications']:>3}  {int(row['conflict_preserved'])}")
    print("\n--- Summary (per technique, all sizes) ---")
    for name, s in sorted(report["summary"].items()):
        print(f"  {name:<13} recall={s['avg_recall']:5.1f}% prec={s['avg_precision']:5.1f}% "
              f"unintended={s['total_unintended']} fab={s['total_fabrications']} "
              f"cpres={int(s['conflict_preserved_all'])}")
    print("\n--- Recommendation ---")
    print(json.dumps(report["recommendation"], indent=2))
    RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
