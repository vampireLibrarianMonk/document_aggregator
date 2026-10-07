"""Drift detection: decide when a cluster that USED to replay cleanly against a
learned profile has changed enough that we must leave the deterministic replay
path and re-emerge into research (re-inference / human approval / LLM tier).

A profile is approved once per source shape and then replays across thousands of
instances. Teams change their exports over time, so we watch for three kinds of
drift, each of which taints only the fields it touches (so the re-emerge step
re-researches ONLY the broken fields, never the whole mapping):

  structural   the incoming signature no longer HIGH-matches the profile's
               learned signature: fields appeared or disappeared, or the overall
               shape similarity dropped a band.
  type         a field kept its name but its primary JSON type changed
               (signature.compare already surfaces this as type_mismatches).
  behavioral   a field that reliably FILLED under the approved profile now
               ABSTAINS across the cluster above a tolerance. The keys and types
               can be unchanged yet the values stopped grounding (e.g. a team
               started sending opaque codes). A pure shape hash misses this;
               the fill-rate baseline catches it.

Behavioral drift is measured against a FillBaseline captured when the profile
was approved (expected per-target fill rate). Drift is always a `needs_review`
signal, never a silent wrong conversion: a drifted field is flagged, not forced.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .provenance import ValueProvenance
from .signature import (
    BAND_HIGH,
    Signature,
    SignatureMatch,
    compare,
)

# A field whose fill rate falls by more than this (absolute) vs its baseline is
# flagged as behavioral drift. 0.25 = "a quarter of previously-filled instances
# now abstain" is enough to warrant re-research of that field.
DEFAULT_COVERAGE_DROP = 0.25

# Reasons a cluster is considered drifted.
DRIFT_STRUCTURAL = "structural"
DRIFT_TYPE = "type"
DRIFT_BEHAVIORAL = "behavioral"


def fill_rates(provenance: Sequence[Sequence[ValueProvenance]]) -> dict[str, float]:
    """Per-target fraction of records whose value FILLED (grounded), from the
    per-record provenance an alignment run produces.

    A target counts toward its denominator only in records where it appears in
    the provenance (always, in practice, since every target gets an entry), so
    this is simply filled / total."""
    total = len(provenance)
    if not total:
        return {}
    filled: dict[str, int] = {}
    seen: dict[str, int] = {}
    for rec in provenance:
        for p in rec:
            seen[p.target] = seen.get(p.target, 0) + 1
            if p.status == "filled":
                filled[p.target] = filled.get(p.target, 0) + 1
    return {t: filled.get(t, 0) / seen[t] for t in seen}


@dataclass
class FillBaseline:
    """The expected per-target fill rate captured when a profile was approved.

    Stored alongside a ConversionProfile (e.g. in its meta) so later batches can
    be compared against the behavior the human signed off on."""
    rates: dict[str, float] = field(default_factory=dict)
    sample_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"rates": {k: round(v, 4) for k, v in self.rates.items()},
                "sample_size": self.sample_size}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FillBaseline:
        return cls(rates=dict(data.get("rates", {})),
                   sample_size=int(data.get("sample_size", 0)))

    @classmethod
    def from_provenance(cls, provenance: Sequence[Sequence[ValueProvenance]],
                        ) -> FillBaseline:
        return cls(rates=fill_rates(provenance), sample_size=len(provenance))


@dataclass
class FieldDrift:
    """Drift localized to one field, so re-research can target only it."""
    field: str
    kinds: tuple[str, ...]              # which drift kinds implicate this field
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "kinds": list(self.kinds), "detail": self.detail}


@dataclass
class DriftReport:
    """The verdict for one cluster vs its approved profile."""
    drifted: bool
    kinds: tuple[str, ...] = ()                     # structural|type|behavioral
    signature_match: SignatureMatch | None = None
    broken_fields: tuple[FieldDrift, ...] = ()      # fields to re-research
    reasons: tuple[str, ...] = ()

    @property
    def broken_field_names(self) -> tuple[str, ...]:
        return tuple(fd.field for fd in self.broken_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "drifted": self.drifted,
            "kinds": list(self.kinds),
            "signature_match": self.signature_match.to_dict() if self.signature_match else None,
            "broken_fields": [fd.to_dict() for fd in self.broken_fields],
            "reasons": list(self.reasons),
        }


def assess_drift(current_signature: Signature, profile_signature: Signature, *,
                 current_fill: dict[str, float] | None = None,
                 baseline: FillBaseline | None = None,
                 coverage_drop: float = DEFAULT_COVERAGE_DROP,
                 mapping_targets: dict[str, str] | None = None,
                 ) -> DriftReport:
    """Compare a cluster's current shape + behavior against its approved profile.

    `current_signature` / `profile_signature` drive structural + type drift via
    signature.compare. `current_fill` (per-target fill rate for THIS batch) vs
    `baseline` drives behavioral drift. `mapping_targets` optionally maps a
    source path -> target field so a structural change on a MAPPED source path
    can be reported against the target field it feeds (clearer for re-research)."""
    match = compare(current_signature, profile_signature)
    kinds: list[str] = []
    reasons: list[str] = []
    # Accumulate per-field drift, merging kinds when a field drifts multiple ways.
    field_kinds: dict[str, set[str]] = {}
    field_detail: dict[str, str] = {}
    path_to_target = mapping_targets or {}

    def _flag(name: str, kind: str, detail: str) -> None:
        field_kinds.setdefault(name, set()).add(kind)
        if detail:
            field_detail[name] = detail

    # -- structural: band dropped below HIGH, or paths added/removed ----------
    structural = match.band != BAND_HIGH or match.added_paths or match.removed_paths
    if structural:
        kinds.append(DRIFT_STRUCTURAL)
        if match.band != BAND_HIGH:
            reasons.append(f"shape similarity dropped to '{match.band}' "
                           f"(score {match.score:.2f})")
        for p in match.added_paths:
            reasons.append(f"new source field appeared: {p}")
            _flag(path_to_target.get(p, p), DRIFT_STRUCTURAL, f"new field {p}")
        for p in match.removed_paths:
            tgt = path_to_target.get(p, p)
            reasons.append(f"expected source field vanished: {p}")
            _flag(tgt, DRIFT_STRUCTURAL, f"missing field {p}")

    # -- type: a shared path changed primary type -----------------------------
    if match.type_mismatches:
        kinds.append(DRIFT_TYPE)
        for p in match.type_mismatches:
            tgt = path_to_target.get(p, p)
            reasons.append(f"source field changed type: {p}")
            _flag(tgt, DRIFT_TYPE, f"type change at {p}")

    # -- behavioral: per-target fill rate fell vs the approved baseline --------
    if current_fill is not None and baseline is not None and baseline.rates:
        behavioral_hit = False
        for tgt, prev in baseline.rates.items():
            now = current_fill.get(tgt, 0.0)
            if prev - now > coverage_drop:
                behavioral_hit = True
                reasons.append(
                    f"field '{tgt}' fill rate fell {prev:.0%} -> {now:.0%} "
                    f"(> {coverage_drop:.0%} drop)")
                _flag(tgt, DRIFT_BEHAVIORAL,
                      f"fill {prev:.0%}->{now:.0%}")
        if behavioral_hit:
            kinds.append(DRIFT_BEHAVIORAL)

    broken = tuple(
        FieldDrift(field=name, kinds=tuple(sorted(field_kinds[name])),
                   detail=field_detail.get(name, ""))
        for name in sorted(field_kinds)
    )
    drifted = bool(kinds)
    if not drifted:
        reasons.append("no drift: shape, types, and fill rates match the profile")
    return DriftReport(
        drifted=drifted, kinds=tuple(dict.fromkeys(kinds)),  # dedup, keep order
        signature_match=match, broken_fields=broken, reasons=tuple(reasons),
    )


__all__ = [
    "DEFAULT_COVERAGE_DROP",
    "DRIFT_BEHAVIORAL",
    "DRIFT_STRUCTURAL",
    "DRIFT_TYPE",
    "DriftReport",
    "FieldDrift",
    "FillBaseline",
    "assess_drift",
    "fill_rates",
]
