"""Execution: apply a Mapping + bounded transforms to source records,
producing target records plus per-value provenance.

Deterministic and non-fabricating:
  - a target field is filled ONLY from its mapped source path, through a
    transform that reports success; a failed transform leaves the field absent
    and records status 'needs_review'
  - unmapped targets (abstained / conflicted during inference) are never
    invented — they surface as needs_review / conflict provenance
  - every produced value gets a ValueProvenance trace (grounding evidence)

Source values are read by the dotted path produced during profiling. Array
paths ('[]') collapse repeated structure; reading a '[]' leaf gathers the list
of values at that position (used for array-typed targets).
"""
from __future__ import annotations

from typing import Any

from .mapping import (
    STATUS_CONFLICT,
    STATUS_NEEDS_REVIEW,
    Mapping,
)
from .provenance import ValueProvenance
from .target_profile import TargetSchema
from .transforms import plan_and_apply


def _read_path(record: dict[str, Any], path: str) -> tuple[Any, bool]:
    """Read a value from a record by a profiling path. Returns (value, found).

    Supports nested object keys ('a.b') and array segments ('a[]' / 'a[].b'):
    an array segment gathers values across elements into a list."""

    def descend(node: Any, remaining: list[str]) -> tuple[Any, bool]:
        if not remaining:
            return node, True
        seg = remaining[0]
        if seg.endswith("[]"):
            key = seg[:-2]
            container = node if key == "" else (
                node.get(key) if isinstance(node, dict) else None)
            if not isinstance(container, list):
                return None, False
            vals: list[Any] = []
            for item in container:
                v, ok = descend(item, remaining[1:])
                if ok:
                    vals.append(v)
            return vals, bool(vals)
        if isinstance(node, dict) and seg in node:
            return descend(node[seg], remaining[1:])
        return None, False

    return descend(record, path.split("."))


def execute_mapping(records: list[dict[str, Any]], mapping: Mapping,
                    target: TargetSchema) -> tuple[list[dict[str, Any]],
                                                    list[list[ValueProvenance]]]:
    """Apply the mapping to each record.

    Returns (target_records, provenance_per_record), index-aligned with
    `records`. Target records contain only successfully-filled fields; the
    provenance list is the complete story (filled + needs_review + conflict)."""
    tgt_by_name = target.by_name()
    out_records: list[dict[str, Any]] = []
    out_prov: list[list[ValueProvenance]] = []

    for rec in records:
        produced: dict[str, Any] = {}
        prov: list[ValueProvenance] = []

        for fm in mapping.fields:
            tgt = tgt_by_name[fm.target]

            if fm.status == STATUS_CONFLICT:
                prov.append(ValueProvenance(
                    target=fm.target, status="conflict", method=fm.method,
                    confidence=fm.confidence, note=fm.note))
                continue
            if fm.status == STATUS_NEEDS_REVIEW or fm.source_path is None:
                prov.append(ValueProvenance(
                    target=fm.target, status="needs_review", method=fm.method,
                    confidence=fm.confidence, note=fm.note or "unmapped"))
                continue

            # STATUS_MAPPED: read -> transform -> fill or defer
            raw, found = _read_path(rec, fm.source_path)
            if not found or raw is None:
                prov.append(ValueProvenance(
                    target=fm.target, status="needs_review",
                    source_path=fm.source_path, method=fm.method,
                    confidence=fm.confidence, raw_value=None,
                    note="source value absent in this record"))
                continue

            value, op, ok = plan_and_apply(raw, tgt)
            if not ok:
                prov.append(ValueProvenance(
                    target=fm.target, status="needs_review",
                    source_path=fm.source_path, method=fm.method,
                    confidence=fm.confidence, operation=op, raw_value=raw,
                    note=f"transform '{op}' could not produce a {tgt.primary_type}"))
                continue

            produced[fm.target] = value
            prov.append(ValueProvenance(
                target=fm.target, status="filled", source_path=fm.source_path,
                operation=op, method=fm.method, confidence=fm.confidence,
                raw_value=raw))

        out_records.append(produced)
        out_prov.append(prov)

    return out_records, out_prov


__all__ = ["execute_mapping"]
