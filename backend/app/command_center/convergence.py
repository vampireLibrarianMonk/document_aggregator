"""Convergence detection for the command center.

Convergence target: iterate until needs_review == 0, with genuine CONFLICTS
preserved (a two-humans-disagree unit cannot resolve to a value without
fabricating, so it is the one allowed non-convergeable state).

A round "makes progress" if its needs_review count drops vs the previous round.
We stop when: needs_review == 0 (converged), OR no progress for a round
(stalled — remaining needs_review items genuinely lack a grounded value), OR the
round cap is hit.
"""
from __future__ import annotations

from dataclasses import dataclass


def count_states(report: dict) -> dict[str, int]:
    """Count unit statuses across sections + furniture of a CorrectedReport dict."""
    counts = {"unchanged": 0, "filled": 0, "corrected": 0,
              "needs_review": 0, "conflict": 0}

    def tally(f: dict) -> None:
        s = f.get("status")
        if s in counts:
            counts[s] += 1

    for sec in report.get("sections", []):
        for f in sec.get("fields", []):
            tally(f)
        for g in sec.get("graphics", []):
            tally(g)
        for t in sec.get("tables", []):
            tally(t)
    fur = report.get("furniture", {})
    for f in fur.get("elements", []):
        tally(f)
    for x in fur.get("cross_references", []):
        tally(x)
    return counts


@dataclass
class RoundState:
    round: int
    needs_review: int
    conflict: int
    resolved: int                   # unchanged + filled + corrected
    converged: bool                 # needs_review == 0
    progressed: bool                # needs_review dropped vs prev


def assess(report: dict, prev_needs_review: int | None) -> RoundState:
    c = count_states(report)
    nr = c["needs_review"]
    resolved = c["unchanged"] + c["filled"] + c["corrected"]
    return RoundState(
        round=0, needs_review=nr, conflict=c["conflict"], resolved=resolved,
        converged=(nr == 0),
        progressed=(prev_needs_review is None or nr < prev_needs_review),
    )
