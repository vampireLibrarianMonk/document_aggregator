"""LLM-backed (and offline-deterministic) project generation.

A generator proposes a complete ProjectSpec; it is validated against the engine
contract and only then persisted as fixed JSON. The LLM is a project AUTHOR, not
part of the correction path: once a project is written, the deterministic engine
reconciles it exactly as a hand-authored one, so reproducibility and the
no-fabrication guarantees are preserved.
"""
from .schema import ProjectSpec, validate_spec

__all__ = ["ProjectSpec", "validate_spec"]
