"""Experiment — the A/B driver with a CORRECTNESS-FIRST gate.

Runs the same workload under variant A and variant B, each inside its scoped
activation, and compares. The cardinal rule (per the agreed goal): correctness
first, speed second. A variant that is faster but changes ANY mapping outcome is
reported as a correctness DIVERGENCE, not a win — and by default that fails the
comparison. Only when both variants produce the IDENTICAL mapping outcome is the
speed delta reported as a real, bankable win.

`allow_divergence=True` surfaces the tradeoff instead of failing, so you can
deliberately inspect a speed/quality exchange (e.g. hashing drops a hard synonym
that MiniLM recovers) — but it is never silently called a win.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .probes import ProbeResult, probe_workload
from .variants import get_variant


@dataclass
class ABResult:
    workload: str
    variant_a: str
    variant_b: str
    probe_a: ProbeResult
    probe_b: ProbeResult
    correct: bool                 # identical mapping outcome?
    divergence: dict[str, Any]    # what differs, when not correct

    @property
    def speedup(self) -> float:
        """How many times faster B's median is vs A (｜>1 means B faster)."""
        mb = self.probe_b.median_s
        if mb <= 0:
            return 0.0
        return self.probe_a.median_s / mb

    def render(self) -> str:
        a, b = self.probe_a, self.probe_b
        lines = [
            f"A/B · workload '{self.workload}'",
            f"  A = {self.variant_a}",
            f"  B = {self.variant_b}",
            "",
            f"  correctness: {'IDENTICAL ✓' if self.correct else 'DIVERGENT ✗'}",
        ]
        if not self.correct:
            d = self.divergence
            if d.get("mapped_only_a"):
                lines.append(f"    mapped only in A: {d['mapped_only_a']}")
            if d.get("mapped_only_b"):
                lines.append(f"    mapped only in B: {d['mapped_only_b']}")
            if d.get("remapped"):
                lines.append(f"    mapped differently: {d['remapped']}")
        lines += [
            "",
            f"  latency (median)   A={a.median_s*1000:.3f}ms   B={b.median_s*1000:.3f}ms",
            f"  latency (p90)      A={a.p90_s*1000:.3f}ms   B={b.p90_s*1000:.3f}ms",
        ]
        if self.correct:
            faster = "B" if self.speedup > 1 else "A"
            lines.append(f"  -> {faster} faster by {max(self.speedup, 1/self.speedup):.2f}x "
                         f"(SAFE: outcomes identical)")
        else:
            lines.append("  -> NOT a win: outcomes differ; speed is moot until "
                         "correctness matches (correctness-first gate)")
        # White-box: show where B spends its time (the optimization target).
        lines.append("")
        lines.append("  per-stage (B, first run):")
        for name, s in b.spans.items():
            lines.append(f"    {name:22} {s['total_s']*1000:8.3f} ms")
        return "\n".join(lines)


def _diff_outcomes(a: ProbeResult, b: ProbeResult) -> dict[str, Any]:
    ma, mb = a.mapped, b.mapped
    remapped = {k: (ma.get(k), mb.get(k)) for k in set(ma) & set(mb)
                if ma[k] != mb[k]}
    return {
        "mapped_only_a": sorted(set(ma) - set(mb)),
        "mapped_only_b": sorted(set(mb) - set(ma)),
        "remapped": remapped,
        "needs_review_delta": sorted(set(a.needs_review) ^ set(b.needs_review)),
        "conflicts_delta": sorted(set(a.conflicts) ^ set(b.conflicts)),
    }


def compare_variants(records: list[dict[str, Any]], schema: dict[str, Any], *,
                     workload: str, variant_a: str, variant_b: str,
                     repeats: int = 5,
                     source_descriptions: dict[str, str] | None = None,
                     use_semantic: bool = False) -> ABResult:
    """Run the same workload under A then B (each scoped) and compare, gating on
    correctness. `use_semantic=True` is needed for the embedding A/B to exercise
    the MiniLM semantic tier (otherwise both backends skip it)."""
    va, vb = get_variant(variant_a), get_variant(variant_b)

    with va.activate():
        probe_a = probe_workload(records, schema, label=variant_a,
                                 repeats=repeats,
                                 source_descriptions=source_descriptions,
                                 use_semantic=use_semantic)
    with vb.activate():
        probe_b = probe_workload(records, schema, label=variant_b,
                                 repeats=repeats,
                                 source_descriptions=source_descriptions,
                                 use_semantic=use_semantic)

    correct = probe_a.outcome_key() == probe_b.outcome_key()
    divergence = {} if correct else _diff_outcomes(probe_a, probe_b)
    return ABResult(workload=workload, variant_a=variant_a, variant_b=variant_b,
                    probe_a=probe_a, probe_b=probe_b, correct=correct,
                    divergence=divergence)
