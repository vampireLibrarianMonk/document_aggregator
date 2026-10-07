"""End-to-end convenience entry that ties the deterministic stages together.

`run_alignment` profiles a batch, extracts the target schema, infers (or reuses)
a mapping, executes it, and validates every produced record — returning one
AlignmentResult with the produced records, provenance, mapping, and per-record
validation. This is the function the command-center JsonAlignmentAgent drives,
and the handle a caller uses directly for a one-shot conversion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .executor import execute_mapping
from .mapping import Mapping, infer_mapping
from .profile_store import ConversionProfile
from .provenance import ValueProvenance
from .source_profile import SourceProfile, profile_source
from .target_profile import TargetSchema, extract_target
from .validator import ValidationReport, validate_record


@dataclass
class AlignmentResult:
    source_profile: SourceProfile
    target: TargetSchema
    mapping: Mapping
    records: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[list[ValueProvenance]] = field(default_factory=list)
    validation: list[ValidationReport] = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return all(v.ok for v in self.validation)

    def summary(self) -> dict[str, Any]:
        return {
            "records": len(self.records),
            "mapped": len(self.mapping.mapped()),
            "needs_review": self.mapping.needs_review(),
            "conflicts": self.mapping.conflicts(),
            "all_valid": self.all_valid,
            "invalid_records": sum(0 if v.ok else 1 for v in self.validation),
        }


def run_alignment(records: list[dict[str, Any]], target_schema: dict[str, Any],
                  *, profile: ConversionProfile | None = None,
                  source_descriptions: dict[str, str] | None = None,
                  use_semantic: bool = False) -> AlignmentResult:
    """Profile -> extract -> map (or reuse) -> [semantic tier] -> execute ->
    validate a batch.

    When `profile` is given, its frozen mapping is reused verbatim (replay);
    otherwise a mapping is inferred deterministically from the source batch.
    `source_descriptions` (path -> text) is an optional semantic signal used
    only during inference (e.g. a source metadata document).

    `use_semantic` opts into the optional LLM tier that tries to UPGRADE the
    deterministic abstentions (needs_review/conflict) with re-verified model
    picks. It is a no-op offline or when a profile is replayed (a frozen mapping
    is never re-opened), so default behavior stays fully deterministic."""
    src = profile_source(records, source_descriptions)
    target = extract_target(target_schema)
    if profile is not None:
        mapping = profile.mapping          # replay: frozen, never re-opened
    else:
        mapping = infer_mapping(src, target)
        if use_semantic:
            from .semantic import apply_semantic_tier
            mapping = apply_semantic_tier(mapping, src, target)

    produced, prov = execute_mapping(records, mapping, target)
    validation = [
        validate_record(rec, prov[i], target) for i, rec in enumerate(produced)
    ]
    return AlignmentResult(
        source_profile=src, target=target, mapping=mapping,
        records=produced, provenance=prov, validation=validation,
    )


__all__ = ["AlignmentResult", "run_alignment"]
