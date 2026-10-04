"""Governor: decomposed, verifiable document generation.

The governor turns a brief into a validated ProjectSpec by DECOMPOSING the work
into bounded, individually-checked steps (plan -> fill-per-section ->
proofread-per-section -> reconcile), instead of asking a model for the whole
document in one shot (which the model evaluation proved never passes first try
and starves small models).

A pluggable ADJUDICATOR makes the governor's control decisions (accept /
needs-review / retry / downshift / reject) as bounded, typed verdicts with a
confidence -- the JEV-style decision-layer pattern (see GOVERNOR_RESEARCH.md).
The generation model is an AUTHOR only; adjudication is a separate concern.

Public surface:
  run_governed(brief, ...)         synchronous entry (tests, dry-run API)
  Governor                          the state machine
  Adjudicator / Verdict / Decision  the decision layer
  make_adjudicator(name, ...)       factory for the three implementations
  GovernorEvent / GovernorResult    structured output + progress events
"""
from __future__ import annotations

from .adjudicators import (
    ADJUDICATORS,
    Adjudicator,
    DeterministicAdjudicator,
    make_adjudicator,
)
from .core import (
    Decision,
    DecisionKind,
    GovernorEvent,
    GovernorResult,
    Verdict,
    VerdictKind,
)
from .engine import Governor, GovernorBudget, run_governed
from .section_author import (
    DeterministicSectionAuthor,
    SectionAuthor,
    make_section_author,
)

__all__ = [
    "ADJUDICATORS",
    "Adjudicator",
    "Decision",
    "DecisionKind",
    "DeterministicAdjudicator",
    "DeterministicSectionAuthor",
    "Governor",
    "GovernorBudget",
    "GovernorEvent",
    "GovernorResult",
    "SectionAuthor",
    "Verdict",
    "VerdictKind",
    "make_adjudicator",
    "make_section_author",
    "run_governed",
]
