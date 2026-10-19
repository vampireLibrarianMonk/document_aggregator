"""Probes — the measurement instrument.

Runs a fixed workload through the REAL alignment pipeline (`run_alignment`,
in-process) and captures:

  white-box   per-stage wall time via app.json_alignment.perf_spans
              (profile / extract / infer / semantic / execute / validate)
  black-box   end-to-end latency over N repeats -> min / median / p90 / max
  outcome     the mapping produced (so the experiment can gate on correctness)

In-process is the right tool for WHERE time goes: it needs direct call access to
time each internal stage and to swap variants. The live-API loop remains the
tool for the real client experience; these two are complementary.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from app.json_alignment import perf_spans
from app.json_alignment.pipeline import run_alignment


@dataclass
class ProbeResult:
    label: str
    repeats: int
    # black-box end-to-end latency (seconds)
    latencies: list[float] = field(default_factory=list)
    # white-box per-stage totals from the FIRST timed run (seconds + calls)
    spans: dict[str, dict[str, float]] = field(default_factory=dict)
    # the mapping outcome used for the correctness gate: golden -> source_path
    mapped: dict[str, str] = field(default_factory=dict)
    needs_review: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def median_s(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def p90_s(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(0.9 * len(s)))]

    @property
    def min_s(self) -> float:
        return min(self.latencies) if self.latencies else 0.0

    @property
    def max_s(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    def outcome_key(self) -> tuple:
        """A hashable fingerprint of the mapping outcome for correctness A/B."""
        return (tuple(sorted(self.mapped.items())),
                tuple(sorted(self.needs_review)),
                tuple(sorted(self.conflicts)))


def probe_workload(records: list[dict[str, Any]], schema: dict[str, Any], *,
                   label: str, repeats: int = 5,
                   source_descriptions: dict[str, str] | None = None,
                   use_semantic: bool = False) -> ProbeResult:
    """Measure one workload. The first run is captured WITH white-box spans; the
    remaining repeats are timed black-box (spans off, so the instrument overhead
    never contaminates the latency distribution)."""
    res = ProbeResult(label=label, repeats=repeats)

    # First run: capture spans + the outcome.
    with perf_spans.collect() as collector:
        t0 = time.perf_counter()
        r = run_alignment(records, schema,
                          source_descriptions=source_descriptions,
                          use_semantic=use_semantic)
        res.latencies.append(time.perf_counter() - t0)
    res.spans = collector.as_dict()
    res.mapped = dict(r.mapping.mapped())
    res.needs_review = list(r.mapping.needs_review())
    res.conflicts = list(r.mapping.conflicts())

    # Remaining runs: black-box latency only (no span collector installed).
    for _ in range(max(0, repeats - 1)):
        t0 = time.perf_counter()
        run_alignment(records, schema, source_descriptions=source_descriptions,
                      use_semantic=use_semantic)
        res.latencies.append(time.perf_counter() - t0)

    return res
