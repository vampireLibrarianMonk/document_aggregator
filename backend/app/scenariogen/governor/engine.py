"""Governor engine: the decomposed state machine.

plan -> fill[i] -> proofread[i] -> reconcile, with a pluggable adjudicator making
the bounded control decisions, per-document budgets, a retry/downshift policy,
and an append-only event log for the live progress view.

Separation of concerns:
  - AUTHORING is delegated to a ScenarioGenerator (deterministic offline, or a
    Bedrock model). The governor does not know how a section is produced.
  - ADJUDICATION is delegated to an Adjudicator (this file never hardcodes a
    decision rule; it asks the adjudicator and acts on the typed verdict).
  - RECONCILE reuses the existing validate_spec + salvage_spec + persist.

The offline path proves the whole state machine with zero Bedrock: the
deterministic generator authors a complete spec, and the governor decomposes it
into per-section decisions, adjudicates each against the corpus, and reconciles.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schema import ScenarioSpec, salvage_spec, validate_spec
from .adjudicators import (
    Adjudicator,
    DeterministicAdjudicator,
    deterministic_verdict,
)
from .core import (
    Decision,
    DecisionKind,
    GovernorEvent,
    GovernorResult,
    VerdictKind,
)


@dataclass
class GovernorBudget:
    """Per-document limits. Decomposition multiplies calls, so cost is a governed
    resource (see GOVERNOR_RESEARCH.md, the Jevons warning)."""
    max_retries_per_section: int = 1
    max_total_author_calls: int = 40     # hard ceiling across the whole document
    allow_downshift: bool = True


def _spec_sections(spec: ScenarioSpec) -> list[dict]:
    """Project a spec's draft sections into decision payloads: the asserted
    values are the correction new_values that target this section (what could be
    fabricated), plus the body text."""
    # Map section -> asserted values from corrections.
    asserted: dict[str, list[str]] = {}
    for c in spec.corrections:
        sec = str(c.target).split(".", 1)[0]
        if c.operation == "replace" and c.new_value not in (None, ""):
            asserted.setdefault(sec, []).append(str(c.new_value))
    out = []
    for s in spec.draft_sections:
        out.append({
            "key": s.key,
            "heading": s.heading,
            "body": s.body or "",
            "asserted_values": asserted.get(s.key, []),
        })
    # Sections that only appear as correction targets (not in draft) still count.
    seen = {s["key"] for s in out}
    for sec in asserted:
        if sec not in seen and sec not in ("furniture",):
            out.append({"key": sec, "heading": sec, "body": "",
                        "asserted_values": asserted[sec]})
    return out


class Governor:
    """Runs the decomposed state machine for one document."""

    def __init__(self, author=None, adjudicator: Adjudicator | None = None,
                 budget: GovernorBudget | None = None,
                 author_model: str = "offline", on_event=None,
                 section_author=None) -> None:
        self.author = author                       # a whole-document ScenarioGenerator
        self.section_author = section_author       # a SectionAuthor (per_section mode)
        self.adjudicator = adjudicator or DeterministicAdjudicator()
        self.budget = budget or GovernorBudget()
        self.author_model = author_model
        self.on_event = on_event                   # optional callback(GovernorEvent)
        self.mode = "per_section" if section_author is not None else "whole_doc"
        self.result = GovernorResult(
            spec=None, adjudicator=self.adjudicator.name,
            author_model=author_model)
        self.result.authoring_mode = self.mode

    def account_usage(self, usage) -> None:
        """Fold a model call's measured usage into the per-document totals, and
        estimate its dollar cost. Adjudicators call this via their metrics_sink;
        the engine also calls it for the author's own usage. Safe for any object
        exposing input_tokens/output_tokens/latency_ms (zero on the offline
        path)."""
        from ..metrics import estimate_usd
        it = int(getattr(usage, "input_tokens", 0) or 0)
        ot = int(getattr(usage, "output_tokens", 0) or 0)
        self.result.input_tokens += it
        self.result.output_tokens += ot
        self.result.latency_ms += int(getattr(usage, "latency_ms", 0) or 0)
        model = self.author_model if self.author_model != "offline" else ""
        if model:
            self.result.est_usd += estimate_usd(model, it, ot)
        self.result.author_calls += 1

    def _wire_sink(self) -> None:
        """Point a model-based adjudicator's metrics sink at this governor so its
        decision-call tokens are accounted. No-op for the deterministic one."""
        if hasattr(self.adjudicator, "_sink"):
            self.adjudicator._sink = self.account_usage

    # -- event helper -------------------------------------------------------
    def _emit(self, **kw) -> None:
        ev = GovernorEvent(adjudicator=self.adjudicator.name, **kw)
        self.result.events.append(ev)
        if self.on_event is not None:
            # Streaming consumers get each event as it happens; a failing sink
            # must never break the governor.
            try:
                self.on_event(ev)
            except Exception:
                pass

    # -- decision helper: ask adjudicator, record decision-quality ----------
    def _decide(self, decision: Decision):
        verdict = self.adjudicator.judge(decision)
        truth = deterministic_verdict(decision)
        self.result.decisions_total += 1
        if verdict.kind == truth.kind:
            self.result.decisions_agree_truth += 1
        self._emit(step=f"{decision.kind.value}:{decision.unit_id}", status="verdict",
                   verdict=verdict.kind.value, confidence=verdict.confidence,
                   detail=verdict.reason, model=self.author_model)
        return verdict

    # -- the state machine --------------------------------------------------
    def run(self, brief) -> GovernorResult:
        self._wire_sink()
        self._emit(step="plan", status="start",
                   detail=f"authoring candidate ({self.mode})", model=self.author_model)

        if self.mode == "per_section":
            spec, sections = self._plan_per_section(brief)
        else:
            spec, sections = self._plan_whole_doc(brief)

        self.result.sections_planned = len(sections)
        plan_decision = Decision(
            kind=DecisionKind.plan, unit_id="plan",
            payload={"sections": sections}, corpus=self._corpus(spec))
        plan_verdict = self._decide(plan_decision)
        self._emit(step="plan", status="done",
                   detail=f"{len(sections)} sections", model=self.author_model)
        if plan_verdict.kind == VerdictKind.retry:
            self.result.fell_back = True

        # FILL + PROOFREAD per section.
        corpus = self._corpus(spec)
        keep_sections: set[str] = set()
        for sec in sections:
            dec = Decision(kind=DecisionKind.fill, unit_id=sec["key"],
                           payload=sec, corpus=corpus)
            verdict = self._run_section(dec, spec)
            if verdict.kind in (VerdictKind.accept, VerdictKind.needs_review):
                keep_sections.add(sec["key"])
                self.result.sections_filled += 1
                if verdict.kind == VerdictKind.needs_review:
                    self.result.sections_needs_review += 1
            elif verdict.kind == VerdictKind.reject:
                self.result.rejects += 1

        # RECONCILE: drop corrections for rejected sections, then validate +
        # salvage + hand back. The deterministic engine stays authoritative.
        self._reconcile(spec, keep_sections)
        self.result.spec = spec
        self._emit(step="reconcile", status="done",
                   detail=f"kept {len(keep_sections)}/{len(sections)} sections",
                   model=self.author_model)
        return self.result

    def _plan_whole_doc(self, brief):
        """Whole-document authoring: one call produces the full candidate spec."""
        if hasattr(self.author, "generate_with_metrics"):
            author_res = self.author.generate_with_metrics(brief)
            spec = author_res.spec
            am = author_res.metrics
            self.result.input_tokens += am.input_tokens
            self.result.output_tokens += am.output_tokens
            self.result.latency_ms += am.latency_ms
            self.result.est_usd += am.est_usd
            if am.fell_back:
                self.result.fell_back = True
        else:
            spec = self.author.generate(brief)
        return spec, _spec_sections(spec)

    def _plan_per_section(self, brief):
        """Per-section authoring: plan the skeleton, then the spec is assembled
        from section fills. The SectionAuthor's own usage is accounted via the
        wired sink (plan() calls it)."""
        self.section_author._sink = self.account_usage if hasattr(
            self.section_author, "_sink") else None
        sections = self.section_author.plan(brief)
        spec = self.section_author.assemble()
        return spec, sections

    def _author_one_section(self, key: str, model: str | None = None) -> None:
        """Re-author a single section (per_section mode) on retry/downshift. The
        deterministic author is stable; a live author would refill the section
        here (optionally on a downshifted `model`)."""
        if self.mode != "per_section":
            return
        self.section_author.author_section(key, model=model)

    def _run_section(self, decision: Decision, spec: ScenarioSpec):
        """Fill -> adjudicate -> retry/downshift/accept for one section.

        In per_section mode a retry/downshift RE-AUTHORS the section (small call)
        rather than only re-judging; in whole_doc mode the content is already
        authored, so a retry re-adjudicates the stable content."""
        self._emit(step=f"fill:{decision.unit_id}", status="start",
                   detail=decision.payload.get("heading", ""), model=self.author_model)
        if self.mode == "per_section":
            self._author_one_section(decision.unit_id)
        attempts = 0
        verdict = self._decide(decision)
        while verdict.kind in (VerdictKind.retry, VerdictKind.downshift):
            if attempts >= self.budget.max_retries_per_section:
                break
            attempts += 1
            decision.attempt = attempts
            if verdict.kind == VerdictKind.retry:
                self.result.retries += 1
                self._emit(step=f"fill:{decision.unit_id}", status="retry",
                           detail=verdict.reason, model=self.author_model)
                self._author_one_section(decision.unit_id)        # re-author same model
            else:
                self.result.downshifts += 1
                self._emit(step=f"fill:{decision.unit_id}", status="downshift",
                           detail=verdict.reason, model=self.author_model)
                # Re-author this one section on a smaller/cheaper model.
                self._author_one_section(decision.unit_id, model="downshift")
            # Re-adjudicate the (possibly re-authored) section.
            verdict = self._decide(decision)

        # PROOFREAD: re-check grounding of the section's asserted values.
        pf = Decision(kind=DecisionKind.proofread, unit_id=decision.unit_id,
                      payload=decision.payload, corpus=decision.corpus)
        pf_verdict = self._decide(pf)
        if pf_verdict.kind == VerdictKind.reject:
            self.result.fabrications_caught += 1
            verdict = pf_verdict  # a proofread rejection overrides
        elif pf_verdict.kind == VerdictKind.needs_review and verdict.kind == VerdictKind.accept:
            verdict = pf_verdict
        self._emit(step=f"proofread:{decision.unit_id}", status="done",
                   verdict=verdict.kind.value, model=self.author_model)
        return verdict

    def _reconcile(self, spec: ScenarioSpec, keep_sections: set[str]) -> None:
        """Drop corrections whose section was rejected, then salvage + validate."""
        if keep_sections:
            spec.corrections = [
                c for c in spec.corrections
                if str(c.target).split(".", 1)[0] in keep_sections
                or str(c.target).split(".", 1)[0] == "furniture"
            ]
        salvage_spec(spec)
        self.result.fabrications_caught += getattr(spec, "_salvage_fabrication", 0)
        problems = validate_spec(spec)
        if problems:
            # Reconcile failed -> the governed result is not persistable; the
            # caller treats this as a fallback to the deterministic generator.
            self.result.fell_back = True
            self._emit(step="reconcile", status="fallback",
                       detail="; ".join(problems)[:160], model=self.author_model)

    @staticmethod
    def _corpus(spec: ScenarioSpec) -> str:
        return " \n ".join(d.text for d in spec.corpus)


def run_governed(brief, *, author=None, adjudicator: Adjudicator | None = None,
                 budget: GovernorBudget | None = None,
                 author_model: str = "offline", on_event=None,
                 section_author=None) -> GovernorResult:
    """Synchronous entry point. Defaults to the deterministic author +
    deterministic adjudicator, so this runs fully offline with no Bedrock.
    `on_event` is an optional callback invoked with each GovernorEvent as it is
    emitted (used by the streaming API). Pass `section_author` to run in
    per-section authoring mode (one section per call) instead of whole-document;
    `author` is then only the fallback and may be omitted."""
    if section_author is not None:
        gov = Governor(section_author=section_author, adjudicator=adjudicator,
                       budget=budget, author_model=author_model, on_event=on_event)
        return gov.run(brief)
    if author is None:
        from ..rule_generator import RuleScenarioGenerator
        author = RuleScenarioGenerator()
    gov = Governor(author=author, adjudicator=adjudicator, budget=budget,
                   author_model=author_model, on_event=on_event)
    return gov.run(brief)
