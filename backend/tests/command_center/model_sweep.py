r"""Model sweep: the winning bake-off configuration across EVERY approved model.

The command-center bake-off (docs/testing/bakeoff-command-center.md) proved the
architecture on ONE model (gpt-oss-120b). It never varied the model itself, so
the `recommend_model` ranking that calls 120b the winner was an assertion, not a
measured head-to-head. This harness closes that gap.

It fixes the WINNING configuration from that study:

    draft pathway  +  S1-authored manifest  +  model agent

and runs it across all six sample projects for EACH approved model in turn,
scoring the same axes:

    value %                 emitted field value matches the gold report
    status %                emitted field status matches the gold report
    fabrications            emitted non-flagged values NOT grounded (0 ideal)
    conflict_preserved      the deliberate disagreement stays 'conflict'
    cost                    tokens / latency / estimated USD (per model)

The deterministic engine remains the authoritative floor — the model agent only
refines the natural-language correction-parse ON TOP, and every proposed value
passes the no-fabrication grounding gate before the engine accepts it (see
tests/command_center/agents.py:ModelAgent). So this sweep measures what each
model BUYS on top of that floor and, adversarially, whether any model can push a
fabrication through or collapse a conflict.

This is an ON-DEMAND study, NOT part of CI. It needs Bedrock reachable with
credentials. Every model call is cached on disk (keyed by model id), so a re-run
with a warm cache does not re-spend tokens.

Run:
  cd backend
  $env:BEDROCK_ENABLED="true"           # plus AWS creds / region in the env
  $env:HF_HUB_OFFLINE=1; $env:TRANSFORMERS_OFFLINE=1
  $env:DATA_DIR="$env:TEMP\sweep"
  ..\.venv\Scripts\python.exe -m tests.command_center.model_sweep

  # sweep a specific subset instead of every approved model:
  ..\.venv\Scripts\python.exe -m tests.command_center.model_sweep --models gpt-oss-120b,gpt-oss-20b

Results are printed as a per-model scorecard and persisted to
model_sweep_results.json next to this file.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.projectgen.bedrock_gen import list_approved_models
from app.projectgen.metrics import estimate_usd

from tests.bakeoff.harness import PROJECT_IDS
from tests.command_center.modelcall import ModelClient
from tests.command_center.run import _run_cell

# The winning configuration from the command-center bake-off. Fixed on purpose:
# the one axis we are varying is the MODEL.
WIN_PATHWAY = "draft"
WIN_STRATEGY = "S1-authored"
WIN_AGENT = "model"

RESULTS_PATH = Path(__file__).with_name("model_sweep_results.json")


@dataclass
class ModelRun:
    """Aggregate score + cost for one model across all six projects."""
    model_id: str
    available: bool = False
    projects_run: int = 0
    # correctness (averaged across projects that ran)
    avg_value_pct: float = 0.0
    avg_status_pct: float = 0.0
    # hard safety invariants (the ones that must never break)
    total_fabrications: int = 0
    conflicts_preserved: int = 0          # count of projects that kept the conflict
    conflicts_expected: int = 0           # projects with a gold conflict to keep
    invariants_held: bool = False
    # cost (summed across projects)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    est_usd: float = 0.0
    # per-project detail + any errors
    per_project: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_models(requested: list[str] | None) -> tuple[list[str], dict]:
    """Resolve the model ids to sweep. With --models, use that explicit list
    (validated against the live catalog where possible). Otherwise sweep every
    APPROVED + AVAILABLE model the account/region exposes."""
    info = list_approved_models()
    catalog_ids = [m["id"] for m in info.get("models", [])]
    if requested:
        # Match each requested substring to a live catalog id when available, so
        # a short name like "gpt-oss-120b" resolves to the real on-demand id.
        resolved: list[str] = []
        for want in requested:
            w = want.strip().lower()
            if not w:
                continue
            hit = next((cid for cid in catalog_ids if w in cid.lower()), None)
            resolved.append(hit or want.strip())
        return resolved, info
    return catalog_ids, info


def _sweep_one(model_id: str) -> ModelRun:
    """Run the winning config across all six projects for one model."""
    run = ModelRun(model_id=model_id)
    client = ModelClient(model_id=model_id)
    run.available = client.available()
    if not run.available:
        run.errors.append("model unavailable (bedrock unreachable or not this id)")
        return run

    values: list[float] = []
    statuses: list[float] = []
    for pid in PROJECT_IDS:
        cell = _run_cell(pid, WIN_PATHWAY, WIN_AGENT, WIN_STRATEGY, client)
        detail = {
            "project_id": pid,
            "ran": cell.ran,
            "value_pct": cell.value_pct,
            "status_pct": cell.status_pct,
            "fabrications": cell.fabrications,
            "conflict_preserved": cell.conflict_preserved,
            "needs_review": cell.needs_review,
            "conflict": cell.conflict,
            "error": cell.error,
        }
        run.per_project.append(detail)
        if cell.error:
            run.errors.append(f"p{pid}: {cell.error}")
        if not cell.ran:
            continue
        run.projects_run += 1
        values.append(cell.value_pct)
        statuses.append(cell.status_pct)
        run.total_fabrications += cell.fabrications
        # Every sample project carries exactly one deliberate conflict.
        run.conflicts_expected += 1
        if cell.conflict_preserved:
            run.conflicts_preserved += 1

    if values:
        run.avg_value_pct = round(sum(values) / len(values), 1)
        run.avg_status_pct = round(sum(statuses) / len(statuses), 1)

    # Cost: pull the accumulated usage off the shared client's stats.
    run.input_tokens = client.stats.input_tokens
    run.output_tokens = client.stats.output_tokens
    run.latency_ms = client.stats.latency_ms
    run.est_usd = round(estimate_usd(model_id, run.input_tokens, run.output_tokens), 6)

    # The hard invariants: zero fabrications AND every expected conflict kept.
    run.invariants_held = (
        run.projects_run > 0
        and run.total_fabrications == 0
        and run.conflicts_preserved == run.conflicts_expected
    )
    return run


def sweep(model_ids: list[str] | None = None) -> dict:
    """Sweep the winning config across the given models (or all approved ones).
    Returns the full report dict (also what gets written to disk).

    NOTE on live calls: ModelClient.available() returns True whenever a boto3
    bedrock-runtime client can be CONSTRUCTED (creds present), independent of
    BEDROCK_ENABLED. That means this sweep will issue live model calls on a box
    with mounted credentials even if the app's BEDROCK_ENABLED flag is off. Every
    call is cached on disk by (model_id, system, user, max_tokens), so a warm
    cache re-runs for free; a cold cache spends tokens. The pytest wrapper
    (test_model_sweep.py) gates on BEDROCK_ENABLED=true per the on-demand
    convention; this function is the raw engine and does not self-gate so a
    cache-only re-run works without the flag."""
    ids, info = _resolve_models(model_ids)
    runs: list[ModelRun] = []
    for mid in ids:
        runs.append(_sweep_one(mid))

    report = {
        "config": {
            "pathway": WIN_PATHWAY,
            "strategy": WIN_STRATEGY,
            "agent": WIN_AGENT,
            "projects": PROJECT_IDS,
            "note": (
                "Winning bake-off config held fixed; the model is the only axis "
                "varied. Deterministic engine is the floor; the model only "
                "refines the NL correction-parse and every value is "
                "grounding-gated before the engine accepts it."
            ),
        },
        "bedrock_available": bool(info.get("available")),
        "region": info.get("region"),
        "allowlist": info.get("allowlist"),
        "recommended_before_sweep": info.get("recommended"),
        "models": [asdict(r) for r in runs],
    }
    return report


def _print(report: dict) -> None:
    print("=== Model sweep (winning config: draft + S1-authored + model agent) ===")
    print(f"bedrock_available={report['bedrock_available']} region={report['region']}")
    print(f"allowlist={report['allowlist']}")
    rec = report.get("recommended_before_sweep") or {}
    if rec:
        print(f"recommended-before-sweep: {rec.get('model')} ({rec.get('reason')})")
    print("-" * 100)
    print(f"{'model':<48}{'ran':>4}{'val%':>7}{'stat%':>7}{'fab':>5}"
          f"{'confs':>7}{'inv':>5}{'$':>9}")
    print("-" * 100)
    for m in report["models"]:
        inv = "OK" if m["invariants_held"] else ("—" if not m["available"] else "FAIL")
        confs = f"{m['conflicts_preserved']}/{m['conflicts_expected']}"
        name = m["model_id"]
        if len(name) > 46:
            name = name[:45] + "…"
        print(f"{name:<48}{m['projects_run']:>4}{m['avg_value_pct']:>7.1f}"
              f"{m['avg_status_pct']:>7.1f}{m['total_fabrications']:>5}{confs:>7}"
              f"{inv:>5}{m['est_usd']:>9.4f}")
    print("-" * 100)
    print("val%/stat% = value/status match vs gold (avg over projects that ran)")
    print("fab        = ungrounded emitted values (MUST be 0 — a model that")
    print("             fabricates breaks the core guarantee)")
    print("confs      = projects where the deliberate conflict stayed 'conflict'")
    print("inv        = hard invariants held (0 fab AND all conflicts preserved)")
    print("$          = estimated USD for the model's calls (pinned price table)")
    print("=" * 100)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    model_ids: list[str] | None = None
    if "--models" in argv:
        i = argv.index("--models")
        if i + 1 < len(argv):
            model_ids = [s for s in argv[i + 1].split(",") if s.strip()]

    report = sweep(model_ids)
    _print(report)
    RESULTS_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")

    # A model that ran but broke an invariant is a hard failure signal.
    broke = [m["model_id"] for m in report["models"]
             if m["available"] and m["projects_run"] > 0 and not m["invariants_held"]]
    if broke:
        print(f"\nWARNING: invariants broken by: {', '.join(broke)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
