"""LLM-backed (and offline-deterministic) scenario generation.

A generator proposes a complete ScenarioSpec; it is validated against the engine
contract and only then persisted as fixed JSON. The LLM is a scenario AUTHOR, not
part of the correction path: once a scenario is written, the deterministic engine
reconciles it exactly as a hand-authored one, so reproducibility and the
no-fabrication guarantees are preserved.
"""
from .schema import ScenarioSpec, validate_spec

__all__ = ["ScenarioSpec", "validate_spec"]
