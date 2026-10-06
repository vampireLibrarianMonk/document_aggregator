"""Validation of an aligned record against the target schema + grounding.

Three independent gates, all deterministic:
  structural   required target fields present (or explicitly needs_review)
  type         each produced value matches the target's allowed type/enum/
               pattern/numeric bounds
  grounding    each produced value has provenance pointing at a real source
               path with a successful transform (no fabrication)

Returns a ValidationReport: a list of issues (empty == clean) plus a status
rollup. A missing required field is an issue ONLY when it was not already
surfaced as needs_review/conflict (those are legitimate deferrals, not errors).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .provenance import ValueProvenance
from .target_profile import TargetField, TargetSchema


@dataclass
class ValidationIssue:
    target: str
    kind: str          # missing_required|type|enum|pattern|range|ungrounded
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"target": self.target, "kind": self.kind, "detail": self.detail}


@dataclass
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


def _type_ok(value: Any, tgt: TargetField) -> bool:
    types = tgt.types
    if "array" in types:
        return isinstance(value, list)
    if "integer" in types and isinstance(value, bool):
        return False
    if value is None:
        return "null" in types
    if isinstance(value, bool):
        return "boolean" in types
    if isinstance(value, int):
        return "integer" in types or "number" in types
    if isinstance(value, float):
        return "number" in types
    if isinstance(value, str):
        return "string" in types
    if isinstance(value, dict):
        return "object" in types
    return False


def validate_record(record: dict[str, Any], provenance: list[ValueProvenance],
                    target: TargetSchema) -> ValidationReport:
    """Validate one produced record + its provenance against the target schema."""
    issues: list[ValidationIssue] = []
    tgt_by_name = target.by_name()
    prov_by_target = {p.target: p for p in provenance}
    deferred = {p.target for p in provenance
                if p.status in ("needs_review", "conflict")}

    # structural: required fields must be present OR legitimately deferred
    for tgt in target.fields:
        if tgt.required and tgt.name not in record and tgt.name not in deferred:
            issues.append(ValidationIssue(
                tgt.name, "missing_required",
                "required target field was neither produced nor deferred"))

    # type / enum / pattern / range + grounding for each produced value
    for name, value in record.items():
        tf = tgt_by_name.get(name)
        if tf is None:
            issues.append(ValidationIssue(
                name, "type", "produced a field absent from the target schema"))
            continue

        if not _type_ok(value, tf):
            issues.append(ValidationIssue(
                name, "type",
                f"value {value!r} is not of type {tf.primary_type}"))

        if tf.enum and "array" not in tf.types and value not in tf.enum:
            issues.append(ValidationIssue(
                name, "enum", f"value {value!r} not in controlled vocabulary"))
        if tf.enum and "array" in tf.types and isinstance(value, list):
            for item in value:
                if item not in tf.enum:
                    issues.append(ValidationIssue(
                        name, "enum", f"array item {item!r} not in vocabulary"))

        if tf.pattern and isinstance(value, str) and not re.match(tf.pattern, value):
            issues.append(ValidationIssue(
                name, "pattern", f"value {value!r} violates pattern {tf.pattern}"))

        if isinstance(value, int | float) and not isinstance(value, bool):
            if tf.minimum is not None and value < tf.minimum:
                issues.append(ValidationIssue(
                    name, "range", f"value {value} < minimum {tf.minimum}"))
            if tf.maximum is not None and value > tf.maximum:
                issues.append(ValidationIssue(
                    name, "range", f"value {value} > maximum {tf.maximum}"))

        # grounding: a produced value must trace to a real source path
        p = prov_by_target.get(name)
        if p is None or p.status != "filled" or not p.source_path:
            issues.append(ValidationIssue(
                name, "ungrounded",
                "produced value lacks grounded provenance (possible fabrication)"))

    return ValidationReport(ok=not issues, issues=issues)


__all__ = ["ValidationIssue", "ValidationReport", "validate_record"]
