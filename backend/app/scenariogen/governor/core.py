"""Governor core types: decisions, verdicts, events, result.

These are deliberately small and dependency-free (no Bedrock, no FastAPI) so the
governor logic is unit-testable offline and the types can be serialized for the
live progress stream.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    """What stage asked for a verdict."""
    plan = "plan"
    fill = "fill"
    proofread = "proofread"


class VerdictKind(str, Enum):
    """The bounded, typed answer an adjudicator may return. The governor acts on
    this enum directly -- no free-form text to parse."""
    accept = "accept"              # unit is good; proceed
    needs_review = "needs_review"  # keep but flag for a human (not a failure)
    retry = "retry"                # re-author on the same model
    downshift = "downshift"        # re-author on a smaller/cheaper model
    reject = "reject"              # drop the unit; use the deterministic result


@dataclass
class Decision:
    """A single bounded question put to an adjudicator."""
    kind: DecisionKind
    unit_id: str                 # section key, field key, or "plan"
    payload: dict[str, Any]      # the unit under review (section/fields/body/...)
    corpus: str = ""             # ground-truth corpus text (for grounding checks)
    attempt: int = 0             # how many times this unit has been authored


@dataclass
class Verdict:
    """An adjudicator's typed answer + calibrated confidence + a short reason."""
    kind: VerdictKind
    confidence: float = 1.0      # [0,1]
    reason: str = ""
    source: str = ""             # which adjudicator produced it (for the log)


@dataclass
class GovernorEvent:
    """One append-only, timestamped progress event for the live log.

    Structured, not prose: the frontend renders (step, status, model, detail).
    """
    step: str                    # "plan", "fill:signal_defs", "proofread:scope", ...
    status: str                  # "start" | "verdict" | "done" | "escalate" | "fallback"
    detail: str = ""
    model: str = ""
    adjudicator: str = ""
    verdict: str = ""
    confidence: float | None = None
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        d = {
            "step": self.step, "status": self.status, "detail": self.detail,
            "model": self.model, "adjudicator": self.adjudicator,
            "verdict": self.verdict, "ts": round(self.ts, 3),
        }
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 3)
        return d


@dataclass
class GovernorResult:
    """The outcome of a governed run: the spec, the event log, and aggregate
    accounting (per-document, not per-call)."""
    spec: Any                      # ScenarioSpec (typed loosely to avoid a cycle)
    scenario_id: str | None = None
    events: list[GovernorEvent] = field(default_factory=list)
    adjudicator: str = ""
    author_model: str = ""
    authoring_mode: str = "whole_doc"   # whole_doc | per_section
    # accounting (summed across all steps of the document)
    sections_planned: int = 0
    sections_filled: int = 0
    sections_needs_review: int = 0
    retries: int = 0
    downshifts: int = 0
    rejects: int = 0
    fabrications_caught: int = 0
    fell_back: bool = False
    author_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    est_usd: float = 0.0
    # decision-quality: adjudicator verdicts vs deterministic ground truth
    decisions_total: int = 0
    decisions_agree_truth: int = 0

    def summary(self) -> dict:
        agree = (round(self.decisions_agree_truth / self.decisions_total, 3)
                 if self.decisions_total else None)
        return {
            "adjudicator": self.adjudicator,
            "author_model": self.author_model,
            "authoring_mode": self.authoring_mode,
            "sections_planned": self.sections_planned,
            "sections_filled": self.sections_filled,
            "sections_needs_review": self.sections_needs_review,
            "retries": self.retries,
            "downshifts": self.downshifts,
            "rejects": self.rejects,
            "fabrications_caught": self.fabrications_caught,
            "fell_back": self.fell_back,
            "author_calls": self.author_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "est_usd": round(self.est_usd, 6),
            "decisions_total": self.decisions_total,
            "decision_agreement": agree,
        }
