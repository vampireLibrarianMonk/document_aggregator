"""Performance bench — a decoupled measurement + A/B experimentation layer.

Three independent concerns (work any one without touching the others):

  probes.py       the INSTRUMENT — runs a fixed workload and captures timing.
                  Black-box (end-to-end repeats -> latency distribution) and
                  white-box (per-stage spans via app.json_alignment.perf_spans).
  variants.py     the THINGS YOU GAME — a registry of swappable implementations
                  behind the pipeline's existing seams (embedding backend today;
                  text-processing / model-calling strategies slot in the same
                  way). Activating a variant is a config change, not a rewrite.
  experiment.py   the A/B DRIVER — run the same workload under variant A and B,
                  compare timing, and GATE on correctness: a faster variant that
                  changes ANY mapping outcome is NOT a win (correctness first).

The goal is to game the pipeline for speed — library substitution, different
text processing, different models, different ways of calling them — while never
trading away a correct result.
"""
from __future__ import annotations

from .experiment import ABResult, compare_variants
from .probes import ProbeResult, probe_workload
from .variants import VARIANTS, Variant, get_variant, register_variant

__all__ = [
    "ABResult",
    "ProbeResult",
    "VARIANTS",
    "Variant",
    "compare_variants",
    "get_variant",
    "probe_workload",
    "register_variant",
]
