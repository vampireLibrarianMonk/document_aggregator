"""Mapping inference: decide which source field (if any) feeds each target
field, deterministically and with explicit abstention.

Matching cascade (highest-confidence tier wins), per target field:
  1. exact           normalized source leaf name == normalized target name
  2. normalized key  token-set equality after name normalization
  3. alias           source name tokens match a known alias of the target
  4. description      Jaccard token overlap between source name and target
                     name+description, above a floor
  5. abstain         no tier clears the floor -> status 'needs_review'
                     (NO fabrication: we never invent a correspondence)

Conflict: if two source fields tie for the top score on a required target with
no deterministic tie-break, the field is marked 'conflict' and left unresolved
(mirrors the correction engine's two-humans-disagree rule).

Type compatibility is scored as a secondary signal, never used to fabricate a
match. Everything here is pure and reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .source_profile import FieldStat, SourceProfile, name_tokens
from .target_profile import TargetField, TargetSchema

# Confidence floors per tier. A match below EXACT/NORMALIZED is "proposed".
_EXACT = 1.0
_NORMALIZED = 0.95
_ALIAS = 0.9
_DESC_FLOOR = 0.34        # Jaccard overlap required to propose a description match

STATUS_MAPPED = "mapped"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_CONFLICT = "conflict"


@dataclass
class FieldMapping:
    """One target field's resolution against the source."""
    target: str
    source_path: str | None          # None when abstained
    method: str                      # exact|normalized|alias|description|abstain
    confidence: float
    status: str                      # mapped|needs_review|conflict
    candidates: tuple[tuple[str, float], ...] = ()  # (source_path, score) ranked
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "source_path": self.source_path,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "status": self.status,
            "candidates": [[p, round(s, 4)] for p, s in self.candidates],
            "note": self.note,
        }


@dataclass
class Mapping:
    """The full source->target mapping with per-field provenance of the choice."""
    fields: list[FieldMapping] = field(default_factory=list)

    def mapped(self) -> dict[str, str]:
        """target -> source_path for confidently-mapped fields only."""
        return {m.target: m.source_path for m in self.fields
                if m.status == STATUS_MAPPED and m.source_path is not None}

    def needs_review(self) -> list[str]:
        return [m.target for m in self.fields if m.status == STATUS_NEEDS_REVIEW]

    def conflicts(self) -> list[str]:
        return [m.target for m in self.fields if m.status == STATUS_CONFLICT]

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [m.to_dict() for m in self.fields]}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _type_compatible(src_type: str, tgt: TargetField) -> bool:
    """Soft compatibility: strings cast to many targets, so this is permissive;
    used only to break near-ties, never to create a match."""
    if not tgt.types:
        return True
    if src_type in tgt.types:
        return True
    # strings are castable to numeric/date targets; numerics interchange
    if src_type == "string":
        return True
    if src_type in ("integer", "number") and (
        "integer" in tgt.types or "number" in tgt.types
    ):
        return True
    return False


def _score_candidate(sf: FieldStat, tgt: TargetField) -> tuple[float, str]:
    """Return (score, method) for one source field against one target field.

    Tiers, strongest first: exact name, normalized token-set equality, declared
    alias, then token overlap. The overlap tier compares BOTH sides' name+
    description tokens, so a source metadata description (when available) is an
    honest extra signal — never fabrication."""
    s_name = set(sf.tokens)
    s_desc = set(sf.desc_tokens)
    t_name = set(tgt.tokens)
    t_desc = set(tgt.desc_tokens)
    leaf_norm = " ".join(sf.tokens)
    tgt_norm = " ".join(tgt.tokens)

    # 1. exact normalized name
    if leaf_norm and leaf_norm == tgt_norm:
        return _EXACT, "exact"
    # 2. normalized token-set equality (names only)
    if s_name and s_name == t_name:
        return _NORMALIZED, "normalized"
    # 3. alias: source name tokens match a declared alias of the target
    for alias in tgt.aliases:
        if name_tokens(str(alias)) and set(name_tokens(str(alias))) == s_name:
            return _ALIAS, "alias"
    # 4. token overlap. Compare the richest available bag of tokens on each side
    #    (name + description), so metadata descriptions help when names are opaque.
    s_bag = s_name | s_desc
    t_bag = t_name | t_desc
    overlap = _jaccard(s_bag, t_bag)
    # Also consider name-vs-(name+desc) in case only one side has a description.
    overlap = max(overlap, _jaccard(s_name, t_bag), _jaccard(s_bag, t_name))
    if overlap >= _DESC_FLOOR and _type_compatible(sf.primary_type, tgt):
        score = min(0.85, 0.5 + overlap / 2)
        return score, "description"
    return 0.0, "abstain"


def infer_mapping(source: SourceProfile, target: TargetSchema) -> Mapping:
    """Infer a deterministic source->target mapping, abstaining when unsure.

    One source field may legitimately be the best candidate for multiple targets
    (e.g. a single 'genre' column), so we do NOT enforce 1:1; each target is
    resolved independently from its ranked candidates."""
    src_fields = source.fields
    out: list[FieldMapping] = []

    for tgt in target.fields:
        scored: list[tuple[float, str, str]] = []  # (score, method, source_path)
        for sf in src_fields:
            score, method = _score_candidate(sf, tgt)
            if score > 0.0:
                scored.append((score, method, sf.path))
        # Rank: score desc, then source path asc for a stable deterministic order.
        scored.sort(key=lambda x: (-x[0], x[2]))
        candidates = tuple((p, s) for s, _m, p in scored)

        if not scored:
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method="abstain",
                confidence=0.0, status=STATUS_NEEDS_REVIEW,
                candidates=candidates,
                note="no source field cleared the match floor",
            ))
            continue

        top_score, top_method, top_path = scored[0]
        # Conflict only when two DISTINCT source paths tie at the very top with a
        # non-authoritative method (exact/normalized are self-evidently unique).
        tied = [c for c in scored if c[0] == top_score and c[2] != top_path]
        if tied and top_method not in ("exact", "normalized"):
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method=top_method,
                confidence=top_score, status=STATUS_CONFLICT,
                candidates=candidates,
                note=f"ambiguous: {1 + len(tied)} source fields tie at {top_score:.2f}",
            ))
            continue

        out.append(FieldMapping(
            target=tgt.name, source_path=top_path, method=top_method,
            confidence=top_score, status=STATUS_MAPPED,
            candidates=candidates,
        ))
    return Mapping(fields=out)


__all__ = [
    "FieldMapping",
    "Mapping",
    "STATUS_CONFLICT",
    "STATUS_MAPPED",
    "STATUS_NEEDS_REVIEW",
    "infer_mapping",
]
