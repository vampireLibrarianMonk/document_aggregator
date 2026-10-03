"""Scenario generator interface + selection.

Mirrors corrections/interpreter.py: one Protocol, an offline deterministic
implementation (the default + air-gap fallback + alpha-loop target), and an
optional Bedrock implementation selected by config. A generator turns a BRIEF
into a validated ScenarioSpec; it never persists.
"""
from __future__ import annotations

from typing import Protocol

from ..config import settings
from .schema import ScenarioSpec


class ScenarioBrief:
    """What the caller asks for. Either a structured brief (domain + doc_type +
    optional section plan) or a freeform description the generator interprets."""

    def __init__(self, domain: str = "", doc_type: str = "incident report",
                 title: str = "", freeform: str = "", seed: int = 0) -> None:
        self.domain = domain
        self.doc_type = doc_type
        self.title = title
        self.freeform = freeform
        self.seed = seed


class ScenarioGenerator(Protocol):
    name: str

    def generate(self, brief: ScenarioBrief) -> ScenarioSpec: ...


def get_generator(model_id: str | None = None,
                  prompt_strategy: str | None = None) -> ScenarioGenerator:
    """Select the generator by config, with graceful fallback to the offline
    deterministic generator (which is also the air-gap default). An optional
    model_id overrides the configured scenario model (must be on the allowlist),
    and an optional prompt_strategy selects a named prompt from prompts.py.
    If Bedrock is disabled and no model is requested, returns the offline one."""
    from .rule_generator import RuleScenarioGenerator

    if settings.BEDROCK_ENABLED or model_id:
        try:
            from .bedrock_gen import BedrockScenarioGenerator
            return BedrockScenarioGenerator(model_id=model_id,
                                            prompt_strategy=prompt_strategy)
        except Exception:
            return RuleScenarioGenerator()
    return RuleScenarioGenerator()
