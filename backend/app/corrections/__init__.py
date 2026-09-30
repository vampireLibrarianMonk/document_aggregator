"""Correction operations: the single constrained vocabulary of changes.

Both the deterministic path (hand-authored JSON corrections) and the optional
Bedrock interpreter emit operations against THIS schema and nothing else. The
schema is the safety boundary: an interpreter can only propose these operations,
never arbitrary state changes (reference spec §41 — no unrestricted modification
powers for an LLM).
"""
from .schema import (
    OPERATIONS,
    CorrectionOp,
    tool_spec,
    validate_op,
)

__all__ = ["CorrectionOp", "OPERATIONS", "validate_op", "tool_spec"]
