"""Zero-overhead white-box span timing for the alignment pipeline.

A span records the wall-clock time spent in a named pipeline stage (profile /
extract / infer / semantic / execute / validate). It follows the same no-op-
when-inactive pattern as the job progress channel: a `SpanCollector` is installed
into a ContextVar only while something is measuring, and `span(name)` is a plain
pass-through context manager that does NOTHING when no collector is active. So
production batches, tests, and normal API calls pay ZERO cost — the instrument
is inert unless the performance bench turns it on.

This lives in the app (not the test harness) because it must wrap the real
internal stages of `run_alignment`. It introduces no new dependency and never
changes behavior — it only observes.
"""
from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class SpanCollector:
    """Accumulates per-stage elapsed time (seconds) and call counts for one
    measured run. Nested spans are summed independently by name."""
    totals: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, name: str, elapsed: float) -> None:
        self.totals[name] += elapsed
        self.counts[name] += 1

    def as_dict(self) -> dict[str, dict[str, float]]:
        return {
            name: {"total_s": self.totals[name], "calls": self.counts[name]}
            for name in sorted(self.totals)
        }


_CURRENT: ContextVar[SpanCollector | None] = ContextVar("span_collector", default=None)


@contextmanager
def span(name: str) -> Iterator[None]:
    """Time the enclosed block into the active collector. No-op (just runs the
    block) when no collector is installed — the inert production path."""
    collector = _CURRENT.get()
    if collector is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        collector.record(name, time.perf_counter() - start)


@contextmanager
def collect() -> Iterator[SpanCollector]:
    """Install a fresh collector for the duration of the block and yield it.
    The bench wraps a `run_alignment` call with this to capture per-stage time."""
    collector = SpanCollector()
    token = _CURRENT.set(collector)
    try:
        yield collector
    finally:
        _CURRENT.reset(token)


__all__ = ["SpanCollector", "collect", "span"]
