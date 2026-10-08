"""The constrained correction operation schema.

A correction is one of a small, fixed set of operations against a resolvable
target. This is the ONLY vocabulary the pipeline accepts — the deterministic
JSON path and the Bedrock interpreter both produce these. The Bedrock tool spec
is generated from this same definition so the model is structurally unable to
propose anything outside it.

Operations:
  replace_field       set a section field's value            target "section.field"
  correct_table_cell  set one table cell                     target "section.table[row][col]"
  relabel_graphic     rename a graphic reference in a section target "section.graphic"
  relocate_graphic    move a graphic to another section      target "section.graphic"
  flag_needs_review   mark a unit as needing human review     target "section.field" | "furniture.x"
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

OPERATIONS = (
    "replace_field",
    "correct_table_cell",
    "relabel_graphic",
    "relocate_graphic",
    "flag_needs_review",
)


class CorrectionOp(BaseModel):
    """A single proposed correction. `new_value` is required for value-setting
    ops; `flag_needs_review` needs none. `round` supports multi-round convergence.
    """
    id: str
    operation: str
    target: str
    old_value: Any = None
    new_value: Any = None
    author: str = "unknown"
    round: int = 0
    reason: str = ""

    @field_validator("operation")
    @classmethod
    def _known_op(cls, v: str) -> str:
        if v not in OPERATIONS:
            raise ValueError(f"unknown operation: {v}")
        return v


def validate_op(op: dict) -> tuple[bool, str]:
    """Structural + semantic validation for one proposed operation.
    Returns (ok, reason). Never fabricates: value-setting ops must carry a value.
    """
    try:
        parsed = CorrectionOp(**op)
    except Exception as exc:  # pydantic validation error
        return False, f"invalid op shape: {exc}"

    o = parsed.operation
    if o == "flag_needs_review":
        return True, "ok"
    if o in ("replace_field", "correct_table_cell", "relabel_graphic", "relocate_graphic"):
        if parsed.new_value in (None, ""):
            return False, f"{o} requires a non-empty new_value (no fabrication)"
        return True, "ok"
    return False, f"unhandled operation: {o}"


def to_engine_correction(op: dict) -> dict:
    """Normalize a validated op into the correction dict the engine consumes.

    The engine's resolver keys on target + new_value + round. relocate/relabel
    both act on a "section.graphic" target; the engine already handles graphic
    relabel/move by comparing the corpus-true placement, so we pass the intended
    new section/name through new_value and mark the operation.
    """
    return {
        "id": op["id"],
        "round": op.get("round", 0),
        "kind": "operation",
        "author": op.get("author", "unknown"),
        "subject": op.get("reason", op["operation"]),
        "target": op["target"],
        "operation": _engine_operation(op["operation"]),
        "old_value": op.get("old_value"),
        "new_value": op.get("new_value"),
        "body": op.get("reason", ""),
    }


def _engine_operation(schema_op: str) -> str:
    # The engine's existing vocabulary: "replace" for value ops, "relabel_graphic"
    # for graphic renames. relocate maps to relabel_graphic semantics (the engine
    # resolves final placement from the corpus-true section).
    if schema_op in ("relabel_graphic", "relocate_graphic"):
        return "relabel_graphic"
    if schema_op == "flag_needs_review":
        return "flag"
    return "replace"


def sanitize_tool_name(name: str | None) -> str:
    """Normalize a Bedrock Converse `toolUse` name for matching.

    Some models (notably the GPT-OSS / harmony family) decorate the tool name
    with a channel suffix over Converse, e.g.
    `propose_corrections<|channel|>commentary`, and may pad it with whitespace.
    A strict equality check then rejects a VALID tool call, silently yielding
    zero operations and masking the model's real capability. Strip any
    `<|...|>` channel decoration (take the part before the first `<|`) and
    surrounding whitespace; a clean name passes through unchanged.
    """
    if not name:
        return ""
    return name.split("<|", 1)[0].strip()


def tool_spec() -> dict:
    """Bedrock (Converse API) tool specification generated from this schema.
    The model may ONLY call `propose_corrections` with ops from this vocabulary.
    """
    return {
        "toolSpec": {
            "name": "propose_corrections",
            "description": (
                "Propose document corrections as a list of structured operations. "
                "Only use values explicitly stated in the feedback or supported by "
                "the source corpus. Never invent values; if the correct value is "
                "unknown, use flag_needs_review instead."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "operation": {"type": "string", "enum": list(OPERATIONS)},
                                    "target": {
                                        "type": "string",
                                        "description": "e.g. 'contributing_factors.firmware', "
                                                       "'timeline.graphic', 'identifiers.severity'",
                                    },
                                    "old_value": {"type": "string"},
                                    "new_value": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["operation", "target"],
                            },
                        }
                    },
                    "required": ["operations"],
                }
            },
        }
    }
