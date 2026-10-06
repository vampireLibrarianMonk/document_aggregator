r"""Task 5 — the full bake-off matrix + scorecard + Phase-3 recommendation.

Runs the command center across the full cross-product and scores every run on
the five axes the user cares about:

  MATRIX   2 pathways {draft, template}
         x 2 agent types {deterministic, model}
         x 3 manifest strategies {S1-authored, S2-model, S3-body}
         x 6 projects {1..6}
         = up to 72 cells (the model-agent cells are skipped cleanly, and
           recorded as such, when Bedrock is unavailable, so the harness runs
           fully offline and simply scales up when credentials are present).

  AXES     convergence           needs_review -> 0 (conflicts preserved)
           correctness           value % + status % vs the gold report
           no-fabrication        emitted values are all grounded (0 invented)
           conflict-preservation the deliberate disagreement stays 'conflict'
           determinism           re-run (generated_at stripped) is identical

Results are printed as a compact scorecard and persisted to results.json.
A recommendation for the Phase-3 engine architecture is derived from the data
and printed last.

Run (offline deterministic cells only):
  cd backend
  $env:HF_HUB_OFFLINE=1; $env:TRANSFORMERS_OFFLINE=1; $env:DATA_DIR="$env:TEMP\cc"
  ..\.venv\Scripts\python.exe -m tests.command_center.run

Add the model cells by also exporting BEDROCK_ENABLED=true + AWS creds env.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tests.bakeoff.harness import PROJECT_IDS, load_raw_inputs, score_against_gold
from tests.command_center.agents import DeterministicAgent, ModelAgent
from tests.command_center.coordinator import Coordinator
from tests.command_center.modelcall import ModelClient, default_model
from tests.command_center.review import full_review
from tests.command_center.strategies import strategies

PATHWAYS = ["draft", "template"]
AGENT_TYPES = ["deterministic", "model"]
STRATEGY_NAMES = ["S1-authored", "S2-model", "S3-body"]
MAX_ROUNDS = 3
RESULTS_PATH = Path(__file__).with_name("results.json")


def _strip_ts(report: dict | None) -> dict:
    if not report:
        return {}
    r = dict(report)
    r.pop("generated_at", None)
    return r


@dataclass
class CellResult:
    project_id: str
    pathway: str
    agent_type: str
    strategy: str
    ran: bool = False
    skipped_reason: str = ""
    # convergence
    rounds_run: int = 0
    needs_review: int = 0
    conflict: int = 0
    converged: bool = False
    # correctness / safety
    value_pct: float = 0.0
    status_pct: float = 0.0
    gold_units: int = 0
    fabrications: int = 0
    conflict_preserved: bool = False
    # engineering
    deterministic: bool = False
    render_ok: bool = False
    render_formats_ok: int = 0
    error: str = ""
    model_stats: dict | None = None


def _run_cell(project_id: str, pathway: str, agent_type: str, strategy_name: str,
              model_client: ModelClient | None) -> CellResult:
    cell = CellResult(project_id=project_id, pathway=pathway,
                      agent_type=agent_type, strategy=strategy_name)
    # Model-agent cells require Bedrock; skip cleanly when unavailable.
    if agent_type == "model" and (model_client is None or not model_client.available()):
        cell.skipped_reason = "bedrock unavailable"
        return cell
    try:
        raw = load_raw_inputs(project_id)
        strat = strategies(model_client)[strategy_name]
        agents = ([ModelAgent(model_client)] if agent_type == "model"
                  else [DeterministicAgent()])
        coord = Coordinator(agents, parallel=False, max_rounds=MAX_ROUNDS,
                            manifest_strategy=strat)
        rec = coord.run(raw, pathway=pathway, agent_type=agent_type,
                        strategy=strategy_name)
        cell.ran = True
        cell.error = rec.error
        cell.rounds_run = len(rec.rounds)
        if rec.rounds:
            last = rec.rounds[-1]
            cell.needs_review = last.needs_review
            cell.conflict = last.conflict
            cell.converged = last.converged
        cell.model_stats = rec.model_stats
        # Score vs gold.
        offline = agent_type == "deterministic"
        sc = score_against_gold(project_id, f"{pathway}/{agent_type}/{strategy_name}",
                                rec.final_report or {}, raw,
                                deterministic=True, offline_capable=offline)
        cell.value_pct = round(sc.value_pct, 1)
        cell.status_pct = round(sc.status_pct, 1)
        cell.gold_units = sc.gold_units
        cell.fabrications = sc.fabrications
        cell.conflict_preserved = sc.conflict_preserved
        # Determinism: re-run and compare (timestamp stripped). Only meaningful
        # for the deterministic agent; model calls are cached so a cached re-run
        # is also comparable, but we flag determinism=True only when identical.
        rec2 = coord.run(raw, pathway=pathway, agent_type=agent_type,
                         strategy=strategy_name)
        cell.deterministic = _strip_ts(rec.final_report) == _strip_ts(rec2.final_report)
        # Render review of the final product.
        rev = full_review(rec, probe_soffice=True)
        cell.render_ok = rev["render"]["ok"]
        cell.render_formats_ok = sum(
            1 for v in rev["render"]["formats"].values() if v.get("ok"))
    except Exception as exc:  # noqa: BLE001 - record, never abort the matrix
        cell.error = f"{type(exc).__name__}: {exc}"[:200]
    return cell


@dataclass
class MatrixReport:
    cells: list[dict] = field(default_factory=list)
    model_available: bool = False
    model_id: str = ""
    summary: dict = field(default_factory=dict)
    recommendation: dict = field(default_factory=dict)


def run_matrix() -> MatrixReport:
    model_client: ModelClient | None = None
    try:
        model_client = ModelClient(default_model())
    except Exception:
        model_client = None
    model_available = bool(model_client and model_client.available())

    report = MatrixReport(model_available=model_available,
                          model_id=(model_client.model_id if model_client else ""))
    cells: list[CellResult] = []
    for pid in PROJECT_IDS:
        for pathway in PATHWAYS:
            for agent_type in AGENT_TYPES:
                for strat in STRATEGY_NAMES:
                    cell = _run_cell(pid, pathway, agent_type, strat, model_client)
                    cells.append(cell)
                    _print_cell(cell)
    report.cells = [asdict(c) for c in cells]
    report.summary = _summarize(cells)
    report.recommendation = _recommend(cells)
    return report


def _print_cell(c: CellResult) -> None:
    if c.skipped_reason:
        print(f"  [skip] p{c.project_id} {c.pathway:<8} {c.agent_type:<13} "
              f"{c.strategy:<12} ({c.skipped_reason})")
        return
    flag = "ok " if c.ran and not c.error else "ERR"
    print(f"  [{flag}] p{c.project_id} {c.pathway:<8} {c.agent_type:<13} "
          f"{c.strategy:<12} val={c.value_pct:5.1f}% stat={c.status_pct:5.1f}% "
          f"nr={c.needs_review} conf={c.conflict} "
          f"fab={c.fabrications} cpres={int(c.conflict_preserved)} "
          f"det={int(c.deterministic)} render={c.render_formats_ok}/5"
          + (f" ERR:{c.error}" if c.error else ""))


def _summarize(cells: list[CellResult]) -> dict:
    """Aggregate by (agent_type, strategy) across pathways + projects."""
    groups: dict[tuple[str, str], list[CellResult]] = {}
    for c in cells:
        if not c.ran:
            continue
        groups.setdefault((c.agent_type, c.strategy), []).append(c)
    out = {}
    for (agent, strat), gcells in sorted(groups.items()):
        n = len(gcells)
        out[f"{agent}/{strat}"] = {
            "runs": n,
            "avg_value_pct": round(sum(c.value_pct for c in gcells) / n, 1),
            "avg_status_pct": round(sum(c.status_pct for c in gcells) / n, 1),
            "total_fabrications": sum(c.fabrications for c in gcells),
            "conflict_preserved_all": all(c.conflict_preserved for c in gcells),
            "deterministic_all": all(c.deterministic for c in gcells),
            "render_ok_all": all(c.render_ok for c in gcells),
            "errors": sum(1 for c in gcells if c.error),
        }
    return out


def _recommend(cells: list[CellResult]) -> dict:
    """Derive the Phase-3 recommendation from the data. The winner is the
    configuration that maximizes correctness while holding the hard safety
    invariants (0 fabrications, conflicts preserved, deterministic)."""
    ran = [c for c in cells if c.ran and not c.error]
    if not ran:
        return {"winner": None, "reason": "no successful runs"}

    def safe(c: CellResult) -> bool:
        return (c.fabrications == 0 and c.conflict_preserved and c.deterministic)

    # Best manifest strategy by avg value% among safe deterministic cells.
    det_safe = [c for c in ran if c.agent_type == "deterministic" and safe(c)]
    by_strat: dict[str, list[CellResult]] = {}
    for c in det_safe:
        by_strat.setdefault(c.strategy, []).append(c)
    strat_rank = sorted(
        ((s, round(sum(x.value_pct for x in v) / len(v), 1)) for s, v in by_strat.items()),
        key=lambda kv: kv[1], reverse=True)

    best_strategy = strat_rank[0][0] if strat_rank else None
    model_ran = any(c.agent_type == "model" for c in ran)
    return {
        "winner_strategy": best_strategy,
        "strategy_ranking": strat_rank,
        "agent_recommendation": (
            "deterministic reconcile is the always-on baseline (0 fabrications, "
            "deterministic, offline). The model agent refines the NL correction "
            "parse ON TOP when Bedrock is available (Option B), never replacing "
            "the grounded engine."),
        "model_cells_ran": model_ran,
        "hard_invariants_held": {
            "no_fabrication": all(c.fabrications == 0 for c in ran),
            "conflict_preserved": all(c.conflict_preserved for c in ran),
            "deterministic_det_agent": all(
                c.deterministic for c in ran if c.agent_type == "deterministic"),
        },
    }


def main() -> int:
    print("=== Command-center bake-off matrix ===")
    report = run_matrix()
    print("\n--- Summary (by agent/strategy) ---")
    for key, s in report.summary.items():
        print(f"  {key:<28} runs={s['runs']:>2} "
              f"val={s['avg_value_pct']:5.1f}% stat={s['avg_status_pct']:5.1f}% "
              f"fab={s['total_fabrications']} cpres={int(s['conflict_preserved_all'])} "
              f"det={int(s['deterministic_all'])} render={int(s['render_ok_all'])} "
              f"err={s['errors']}")
    print("\n--- Recommendation ---")
    print(json.dumps(report.recommendation, indent=2))
    RESULTS_PATH.write_text(
        json.dumps({"model_available": report.model_available,
                    "model_id": report.model_id,
                    "cells": report.cells,
                    "summary": report.summary,
                    "recommendation": report.recommendation}, indent=2),
        encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
