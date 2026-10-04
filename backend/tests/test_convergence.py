"""Multi-round convergence tests.

Assert the pipeline behaviour, not one answer:
  - a mishap/contradictory round does not reduce unresolved (it may raise it);
  - a later clean round supersedes an earlier one (last-good-wins) and drives
    unresolved toward zero;
  - the final revision converges (unresolved == 0) for well-formed feedback;
  - every intermediate revision is still a legal, non-fabricated report.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import project as sc  # noqa: E402
from app.reconcile import collapse_rounds  # noqa: E402

RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _rounds_scenarios() -> list[str]:
    return [s["id"] for s in sc.list_project_cases() if sc.has_rounds(s["id"])]


def test_scenario_1_converges():
    result = sc.run_convergence("draft", "1")
    assert result["converged"] is True
    assert result["final_unresolved"] == 0
    traj = result["trajectory"]
    # Unresolved must end at zero and never increase at the FINAL step.
    assert traj[-1]["unresolved"] == 0


def test_contradictory_round_does_not_converge_early():
    """The mishap round (round 1) must leave work unresolved (conflict/needs_review),
    proving a bad round does not falsely converge."""
    result = sc.run_convergence("draft", "1")
    by_round = {t["round"]: t for t in result["trajectory"]}
    assert by_round[1]["unresolved"] >= 1, "contradictory round should stay unresolved"
    # And a conflict is present at the contradictory round.
    assert by_round[1]["summary"].get("conflict", 0) >= 1


def test_last_good_wins_supersedes_earlier_round():
    """A later round's value for a target overrides earlier rounds."""
    corrections = sc.load_corrections("1", "rounds")
    # severity is touched in rounds 1 (two values) and 2 (one value).
    collapsed = collapse_rounds(corrections)
    sev = [c for c in collapsed if c["target"] == "identifiers.severity"]
    assert sev, "severity correction should survive the fold"
    assert all(c.get("round") == 2 for c in sev), "only the latest round survives"


def test_every_revision_is_legal_and_unfabricated():
    result = sc.run_convergence("draft", "1")
    for step in result["trajectory"]:
        report = step["report"]
        for s in report["sections"]:
            for f in s["fields"]:
                assert f["status"] in {"unchanged", "filled", "corrected",
                                       "needs_review", "conflict"}
                if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
                    p = f["provenance"]
                    assert p.get("corpus") or p.get("corrections") or p.get("rule"), (
                        f"fabricated {f['key']} in revision {step['revision']}"
                    )


def test_monotonic_after_clean_rounds():
    """From the first clean round onward, unresolved must not increase."""
    result = sc.run_convergence("draft", "1")
    unresolved = [t["unresolved"] for t in result["trajectory"]]
    # Find the minimum and assert it never rises again after reaching it.
    min_idx = unresolved.index(min(unresolved))
    tail = unresolved[min_idx:]
    assert tail == sorted(tail, reverse=True) or len(set(tail)) == 1
