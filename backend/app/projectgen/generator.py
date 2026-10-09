"""Project generator interface + selection.

Mirrors corrections/interpreter.py: one Protocol, an offline deterministic
implementation (the default + air-gap fallback + alpha-loop target), and an
optional Bedrock implementation selected by config. A generator turns a BRIEF
into a validated ProjectSpec; it never persists.
"""
from __future__ import annotations

from typing import Protocol

from ..config import settings
from .schema import ProjectSpec


class ProjectBrief:
    """What the caller asks for. Either a structured brief (domain + doc_type +
    optional section plan) or a freeform description the generator interprets."""

    def __init__(self, domain: str = "", doc_type: str = "incident report",
                 title: str = "", freeform: str = "", seed: int = 0,
                 corpus: list | None = None,
                 figure_hints: list | None = None) -> None:
        self.domain = domain
        self.doc_type = doc_type
        self.title = title
        self.freeform = freeform
        self.seed = seed
        # Optional user-provided ground-truth corpus (list of CorpusDoc | dict |
        # (name, text)). When present, the project is built FROM this real
        # source material deterministically, instead of a model inventing it.
        self.corpus = corpus
        # Optional figures extracted from an uploaded deliverable (docx/pptx):
        # [{name, caption, source_doc, anchor_text}]. These augment figures found
        # in the corpus '## Figures' blocks so a draft's embedded images are
        # placed next to the text they were anchored to.
        self.figure_hints = figure_hints or []


class ProjectGenerator(Protocol):
    name: str

    def generate(self, brief: ProjectBrief) -> ProjectSpec: ...


def get_generator(model_id: str | None = None,
                  prompt_strategy: str | None = None) -> ProjectGenerator:
    """Select the generator by config, with graceful fallback to the offline
    deterministic generator (which is also the air-gap default). An optional
    model_id overrides the configured project model (must be on the allowlist),
    and an optional prompt_strategy selects a named prompt from prompts.py.
    If Bedrock is disabled and no model is requested, returns the offline one."""
    from .rule_generator import RuleProjectGenerator

    if settings.BEDROCK_ENABLED or model_id:
        try:
            from .bedrock_gen import BedrockProjectGenerator
            return BedrockProjectGenerator(model_id=model_id,
                                            prompt_strategy=prompt_strategy)
        except Exception:
            return RuleProjectGenerator()
    return RuleProjectGenerator()


def get_generator_for_brief(brief: ProjectBrief, model_id: str | None = None,
                            prompt_strategy: str | None = None) -> ProjectGenerator:
    """Select the generator for a specific brief. When the brief carries a
    user-provided corpus, use the deterministic corpus-grounded generator (the
    real source material is the ground truth; no model invents facts, and the
    result is reproducible). Otherwise fall back to the normal selection."""
    if getattr(brief, "corpus", None):
        from .corpus_generator import CorpusProjectGenerator
        return CorpusProjectGenerator()
    return get_generator(model_id=model_id, prompt_strategy=prompt_strategy)
