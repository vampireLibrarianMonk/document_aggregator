"""Objective score + cost metrics for a scenario-generation run.

Every signal here is measured from the pipeline itself (the strict validator and
the Bedrock response), not a subjective quality rating. The one estimate is the
dollar cost, which multiplies measured tokens by a PINNED, DATED price table
(Bedrock does not expose live prices via API); treat $ as an estimate, tokens
and latency as ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunMetrics:
    """Per-generation score + cost. All fields are measured, not judged."""
    model: str = ""
    # Score (quality signals from the validator)
    valid_first_try: bool = False      # raw output passed validate_spec, no repair
    repair_rounds: int = 0             # model repair rounds attempted (0 or 1)
    fabrication_rejections: int = 0    # corrections dropped for ungrounded values
    salvage_dropped: int = 0           # total corrections dropped during salvage
    has_conflict: bool = False         # richness: produced a conflict unit
    has_needs_review: bool = False     # richness: produced a needs_review unit
    graphic_defects: int = 0           # richness: relabel/move/insert figure defects
    fell_back: bool = False            # model unusable -> deterministic generator
    # Cost (measured from Bedrock)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    est_usd: float = 0.0

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "valid_first_try": self.valid_first_try,
            "repair_rounds": self.repair_rounds,
            "fabrication_rejections": self.fabrication_rejections,
            "salvage_dropped": self.salvage_dropped,
            "has_conflict": self.has_conflict,
            "has_needs_review": self.has_needs_review,
            "graphic_defects": self.graphic_defects,
            "fell_back": self.fell_back,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "est_usd": round(self.est_usd, 6),
        }


# Pinned on-demand Bedrock prices, USD per 1,000 tokens (input, output).
# SOURCE: AWS Bedrock pricing page, us-east-1. PINNED 2026-05 — verify before
# relying on $; AWS changes prices and this is NOT fetched from any API.
# Matched by substring against the model id (first match wins).
_PRICE_TABLE_PER_1K: list[tuple[str, float, float]] = [
    # (model-id substring, input $/1k, output $/1k)
    ("gpt-oss-120b", 0.00016, 0.00064),
    ("gpt-oss-20b", 0.00007, 0.00030),
    ("gpt-oss", 0.00016, 0.00064),            # fallback for other gpt-oss variants
    ("nemotron-super-3-120b", 0.00060, 0.00180),
    ("nemotron-nano-3-30b", 0.00020, 0.00060),
    ("nemotron-nano-12b", 0.00010, 0.00030),
    ("nemotron-nano-9b", 0.00008, 0.00024),
    ("nemotron", 0.00060, 0.00180),           # fallback for other nemotron variants
]

PRICE_TABLE_PINNED = "2026-05 (us-east-1, on-demand)"


def price_per_1k(model_id: str) -> tuple[float, float] | None:
    mid = (model_id or "").lower()
    for sub, pin, pout in _PRICE_TABLE_PER_1K:
        if sub in mid:
            return pin, pout
    return None


def estimate_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    p = price_per_1k(model_id)
    if not p:
        return 0.0
    pin, pout = p
    return (input_tokens / 1000.0) * pin + (output_tokens / 1000.0) * pout


# Price-table fields are kept on RunMetrics for provenance in reports.
PRICE_META = {"pinned": PRICE_TABLE_PINNED, "note": "estimate; verify current AWS prices"}
