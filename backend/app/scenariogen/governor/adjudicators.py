"""Adjudicators: the pluggable decision layer.

An adjudicator answers the governor's bounded, typed questions (accept /
needs_review / retry / downshift / reject) with a confidence. Three
implementations, all within the Nemotron/GPT-OSS allowlist (no new vendor model):

  DeterministicAdjudicator   validator / corpus-grounding checks make the call.
                             No model, zero cost, fully offline. GROUND TRUTH.
  GeneratorJudgeAdjudicator  the capable author model also decides (one extra
                             bounded call). The "no separation" baseline.
  DecisionLayerAdjudicator   a small/cheap approved model returns ONLY a typed
                             verdict + confidence (JEV-style). Accept when
                             confident; escalate to the deterministic check when
                             unsure (confidence-gated cascade).

Adding a fourth adjudicator is one new class implementing `judge`; the governor
never changes.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from .core import Decision, DecisionKind, Verdict, VerdictKind

# --------------------------------------------------------------------------
# Grounding helpers (shared; mirror schema.salvage_spec semantics)
# --------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def grounded_value(value: str, corpus: str, body: str = "") -> bool:
    """A value is grounded if it appears (normalized) in the corpus or in the
    unit's own body. This is the no-fabrication rule the validator enforces."""
    nv = _norm(str(value))
    if not nv:
        return True  # nothing asserted -> nothing to fabricate
    return nv in _norm(corpus) or nv in _norm(body)


def deterministic_verdict(decision: Decision) -> Verdict:
    """The GROUND-TRUTH verdict for a decision, from objective checks only.

    - plan: accept if there is at least one section with a key/heading.
    - fill: a section with no asserted values is accept; a section asserting a
      value not grounded in the corpus is reject (fabrication); a section whose
      required value is simply absent is needs_review.
    - proofread: same grounding rule applied to each asserted value.
    """
    p = decision.payload
    if decision.kind == DecisionKind.plan:
        sections = p.get("sections") or []
        ok = any((s.get("key") or s.get("heading")) for s in sections)
        return Verdict(VerdictKind.accept if ok else VerdictKind.retry, 1.0,
                       "has sections" if ok else "empty plan", "deterministic")

    # fill / proofread share the grounding check over asserted values.
    values = p.get("asserted_values") or []
    body = p.get("body", "")
    if not values:
        # Nothing asserted. If the template REQUIRES a value here, flag it.
        if p.get("required_but_absent"):
            return Verdict(VerdictKind.needs_review, 1.0,
                           "required value absent in corpus", "deterministic")
        return Verdict(VerdictKind.accept, 1.0, "no asserted values", "deterministic")
    ungrounded = [v for v in values if not grounded_value(v, decision.corpus, body)]
    if ungrounded:
        return Verdict(VerdictKind.reject, 1.0,
                       f"ungrounded value(s): {ungrounded[:3]}", "deterministic")
    return Verdict(VerdictKind.accept, 1.0, "all values grounded", "deterministic")


# --------------------------------------------------------------------------
# Adjudicator protocol
# --------------------------------------------------------------------------

class Adjudicator(Protocol):
    name: str

    def judge(self, decision: Decision) -> Verdict: ...


# --------------------------------------------------------------------------
# (A) Deterministic -- ground truth, zero cost, offline
# --------------------------------------------------------------------------

class DeterministicAdjudicator:
    name = "deterministic"

    def judge(self, decision: Decision) -> Verdict:
        return deterministic_verdict(decision)


# --------------------------------------------------------------------------
# (B) Generator-as-judge -- the author model also decides
# --------------------------------------------------------------------------

_VERDICT_OPTIONS = [v.value for v in VerdictKind]

_JUDGE_SYSTEM = (
    "You are a strict document reviewer. You are given a unit of a document and "
    "the ground-truth source text. Decide ONE verdict about the unit and return "
    "ONLY compact JSON: {\"verdict\": one of "
    + "|".join(_VERDICT_OPTIONS)
    + ", \"confidence\": 0..1, \"reason\": short}. "
    "Reject any value not supported by the source (that is fabrication). Use "
    "needs_review when a required value is simply missing from the source. No "
    "prose outside the JSON."
)


def _build_judge_user(decision: Decision) -> str:
    p = decision.payload
    return (
        f"UNIT KIND: {decision.kind.value}\n"
        f"UNIT: {json.dumps(p)[:2000]}\n\n"
        f"SOURCE (ground truth):\n{decision.corpus[:4000]}\n\n"
        "Return the verdict JSON only."
    )


def _parse_verdict(raw: str, source: str) -> Verdict:
    """Parse a model's typed-verdict JSON; forgiving, defaults to needs_review
    (never silently 'accept' on a parse failure)."""
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception:
        data = {}
    v = str(data.get("verdict", "")).strip().lower()
    kind = VerdictKind(v) if v in _VERDICT_OPTIONS else VerdictKind.needs_review
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return Verdict(kind, conf, str(data.get("reason", ""))[:160], source)


class GeneratorJudgeAdjudicator:
    """Uses the capable author model to decide via one bounded Converse call.

    Requires a live model client + adapter; if anything fails it degrades to the
    deterministic verdict so the governor never breaks.
    """
    name = "generator_judge"

    def __init__(self, model_id: str, client, adapter, metrics_sink=None) -> None:
        self.model_id = model_id
        self._client = client
        self._adapter = adapter
        self._sink = metrics_sink  # optional callable(Usage) for accounting

    def judge(self, decision: Decision) -> Verdict:
        try:
            raw, usage = self._adapter.complete_with_usage(
                self._client, _JUDGE_SYSTEM, _build_judge_user(decision),
                max_tokens=512)
            if self._sink:
                self._sink(usage)
            return _parse_verdict(raw, self.name)
        except Exception:
            return deterministic_verdict(decision)


# --------------------------------------------------------------------------
# (C) JEV-style decision layer -- small cheap model, confidence-gated cascade
# --------------------------------------------------------------------------

class DecisionLayerAdjudicator:
    """A small/cheap approved model returns ONLY a typed verdict + confidence.

    Accept when confident; when confidence < threshold, ESCALATE to the
    deterministic check (free, authoritative), bounding the blast radius of a
    miscalibrated cheap judge. This is the research-backed cost shape.
    """
    name = "decision_layer"

    def __init__(self, model_id: str, client, adapter, confidence_threshold: float = 0.6,
                 metrics_sink=None) -> None:
        self.model_id = model_id
        self._client = client
        self._adapter = adapter
        self.threshold = confidence_threshold
        self._sink = metrics_sink

    def judge(self, decision: Decision) -> Verdict:
        try:
            raw, usage = self._adapter.complete_with_usage(
                self._client, _JUDGE_SYSTEM, _build_judge_user(decision),
                max_tokens=256)
            if self._sink:
                self._sink(usage)
            v = _parse_verdict(raw, self.name)
        except Exception:
            return deterministic_verdict(decision)
        if v.confidence < self.threshold:
            # Not confident enough: escalate to ground truth (free).
            truth = deterministic_verdict(decision)
            truth.source = f"{self.name}->escalated"
            return truth
        return v


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

ADJUDICATORS = ["deterministic", "generator_judge", "decision_layer"]


def make_adjudicator(name: str, *, model_id: str | None = None, client=None,
                     adapter=None, metrics_sink=None,
                     confidence_threshold: float = 0.6) -> Adjudicator:
    """Build an adjudicator by name. The two model-based ones need a live client
    + adapter; if those are missing they fall back to the deterministic one so
    offline/air-gap always works."""
    if name == "generator_judge" and client is not None and adapter is not None:
        return GeneratorJudgeAdjudicator(model_id or "", client, adapter, metrics_sink)
    if name == "decision_layer" and client is not None and adapter is not None:
        return DecisionLayerAdjudicator(model_id or "", client, adapter,
                                        confidence_threshold, metrics_sink)
    return DeterministicAdjudicator()
