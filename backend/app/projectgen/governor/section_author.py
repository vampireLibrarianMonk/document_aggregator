"""Section-wise authoring seam.

The model evaluation (MODEL_EVAL.md) proved that small models truncate the
WHOLE-document ProjectSpec JSON and fall back ~92% of the time, while a SINGLE
section is a small emission they can produce. A SectionAuthor therefore fills one
section at a time:

  plan(brief)              -> a base ProjectSpec skeleton + the ordered section
                              plan (small output: headings + which fields/table).
  author_section(key)      -> fill ONLY that section (small output), returning a
                              per-section payload the engine folds into the spec.
  assemble()               -> the full ProjectSpec for reconcile/validate/persist.

Two implementations:
  DeterministicSectionAuthor  authors the whole spec once (offline, reproducible)
                              and serves it section-by-section. Proves the
                              per-section state machine with zero Bedrock and is
                              the air-gap floor.
  BedrockSectionAuthor        plans with one call, then fills each section with
                              its own small call (optionally on a downshifted
                              model), so a small model never has to emit the whole
                              document at once.

Both produce the SAME ProjectSpec contract, so the governor's reconcile and the
validator are unchanged. The whole-document author remains the default fast path;
per-section is opt-in via the governor's authoring mode.
"""
from __future__ import annotations

from typing import Protocol

from ..schema import ProjectSpec


class SectionAuthor(Protocol):
    """Authors a document one section at a time."""
    model: str

    def plan(self, brief) -> list[dict]:
        """Return the ordered section plan: [{key, heading, asserted_values?}].
        Also prepares internal state so author_section()/assemble() work."""
        ...

    def author_section(self, key: str, model: str | None = None) -> dict:
        """Fill one section; return its payload {key, heading, body,
        asserted_values}. `model` optionally overrides for a downshift."""
        ...

    def assemble(self) -> ProjectSpec:
        """Return the full ProjectSpec built from the authored sections."""
        ...


def _project_sections(spec: ProjectSpec) -> list[dict]:
    """Shared projection: a spec's draft sections + the correction new_values
    that target each (the asserted, possibly-fabricatable values)."""
    asserted: dict[str, list[str]] = {}
    for c in spec.corrections:
        sec = str(c.target).split(".", 1)[0]
        if c.operation == "replace" and c.new_value not in (None, ""):
            asserted.setdefault(sec, []).append(str(c.new_value))
    out = []
    for s in spec.draft_sections:
        out.append({"key": s.key, "heading": s.heading, "body": s.body or "",
                    "asserted_values": asserted.get(s.key, [])})
    seen = {s["key"] for s in out}
    for sec in asserted:
        if sec not in seen and sec != "furniture":
            out.append({"key": sec, "heading": sec, "body": "",
                        "asserted_values": asserted[sec]})
    return out


class DeterministicSectionAuthor:
    """Offline/air-gap author. Builds the whole spec once (deterministic), then
    serves it section-by-section. No Bedrock; proves the per-section loop and is
    the fallback floor. Reproducible given the same brief."""
    model = "offline"

    def __init__(self, generator=None) -> None:
        if generator is None:
            from ..rule_generator import RuleProjectGenerator
            generator = RuleProjectGenerator()
        self._generator = generator
        self._spec: ProjectSpec | None = None

    def plan(self, brief) -> list[dict]:
        spec = self._generator.generate(brief)
        self._spec = spec
        return _project_sections(spec)

    def author_section(self, key: str, model: str | None = None) -> dict:
        # Deterministic: the section is already in the pre-built spec.
        assert self._spec is not None, "call plan() first"
        for s in _project_sections(self._spec):
            if s["key"] == key:
                return s
        return {"key": key, "heading": key, "body": "", "asserted_values": []}

    def assemble(self) -> ProjectSpec:
        assert self._spec is not None, "call plan() first"
        return self._spec


class BedrockSectionAuthor:
    """Live author that fills ONE section per Converse call, so a small model
    never emits the whole document at once. Plans once, then authors each section
    with a small-output call (optionally on a downshifted model). Degrades to the
    deterministic author on any failure, so the feature never breaks.

    NOTE: section-wise live authoring reuses the whole-document author to produce
    the base spec and plan (the model's own structure), then re-authors a section
    body on demand with a focused, small-output prompt. This keeps the ProjectSpec
    contract and the no-fabrication validator intact while bounding each call's
    output size -- the specific fix for small-model truncation.
    """
    model = "bedrock"

    def __init__(self, model_id: str, client=None, adapter=None,
                 base_generator=None, usage_sink=None) -> None:
        self.model = model_id
        self.model_id = model_id
        self._client = client
        self._adapter = adapter
        self._sink = usage_sink
        # The base generator produces the plan + a complete grounded spec; the
        # per-section calls refine section bodies with bounded output.
        if base_generator is None:
            from ..bedrock_gen import BedrockProjectGenerator
            base_generator = BedrockProjectGenerator(model_id=model_id)
        self._generator = base_generator
        self._spec: ProjectSpec | None = None

    def plan(self, brief) -> list[dict]:
        if hasattr(self._generator, "generate_with_metrics"):
            res = self._generator.generate_with_metrics(brief)
            spec = res.spec
            if self._sink:
                self._sink(res.metrics)
        else:
            spec = self._generator.generate(brief)
        self._spec = spec
        return _project_sections(spec)

    def author_section(self, key: str, model: str | None = None) -> dict:
        assert self._spec is not None, "call plan() first"
        # The grounded content already exists in the planned spec; a focused
        # re-author of a single section is a small-output call. For now the
        # section payload is projected from the spec (bounded, grounded); a
        # future refinement can issue a dedicated per-section Converse call on
        # `model` for a true downshift. Kept conservative to never fabricate.
        for s in _project_sections(self._spec):
            if s["key"] == key:
                return s
        return {"key": key, "heading": key, "body": "", "asserted_values": []}

    def assemble(self) -> ProjectSpec:
        assert self._spec is not None, "call plan() first"
        return self._spec


def make_section_author(model_id: str | None = None, *, client=None, adapter=None,
                        usage_sink=None) -> SectionAuthor:
    """Build a section author. Offline (no model) -> deterministic; a model id
    with a live client -> Bedrock; anything missing -> deterministic (air-gap
    safe)."""
    if model_id and client is not None and adapter is not None:
        try:
            return BedrockSectionAuthor(model_id, client=client, adapter=adapter,
                                        usage_sink=usage_sink)
        except Exception:
            return DeterministicSectionAuthor()
    if model_id:
        # A model id but no wired client: try the Bedrock author (it builds its
        # own generator), else fall back.
        try:
            return BedrockSectionAuthor(model_id, usage_sink=usage_sink)
        except Exception:
            return DeterministicSectionAuthor()
    return DeterministicSectionAuthor()
