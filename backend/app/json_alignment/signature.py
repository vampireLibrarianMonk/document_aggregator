"""Source-shape signatures + matching against a profile library.

The thousands-across-teams workload has few DISTINCT shapes and many instances
each. To route an incoming document to the right learned mapping cheaply, we
fingerprint its SHAPE (independent of values) and compare that fingerprint to
the shapes we have already learned.

A Signature is derived from a SourceProfile:
  - the set of normalized field paths (case/delimiter-insensitive leaf keys at
    their structural position) — the dominant signal
  - the per-path primary JSON type — a secondary signal that catches a shape
    whose keys are stable but whose types drifted

Matching (brief s30) returns a band, not just a boolean, so the coordinator can
decide a PATHWAY:
  HIGH      near-identical shape        -> replay the profile deterministically
  MODERATE  mostly the same, some drift -> replay + extra validation
  LOW/NONE  unfamiliar shape            -> needs inference (new provisional profile)

Everything is deterministic and value-independent: two documents from the same
team produce the same signature regardless of their contents.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .source_profile import SourceProfile, normalize_name

# Match-band thresholds on the path-set Jaccard similarity.
HIGH_BAND = 0.95
MODERATE_BAND = 0.6

BAND_HIGH = "high"
BAND_MODERATE = "moderate"
BAND_LOW = "low"
BAND_NONE = "none"


def _normalize_path(path: str) -> str:
    """Normalize a field path so cosmetic key styling doesn't change the shape.

    Each dotted segment runs through the same name normalizer the matcher uses
    (splits camelCase / snake / kebab, lower-cases) so 'gameTitle', 'game_title'
    and 'game-title' collapse to the same normalized segment. The '[]' array
    marker is preserved (array-ness is structural, not cosmetic)."""
    segs_out: list[str] = []
    for seg in path.split("."):
        is_arr = seg.endswith("[]")
        core = seg[:-2] if is_arr else seg
        segs_out.append(normalize_name(core) + ("[]" if is_arr else ""))
    return ".".join(segs_out)


@dataclass
class Signature:
    """A value-independent fingerprint of a source document shape."""
    paths: tuple[str, ...]                      # sorted normalized path set
    types: dict[str, str] = field(default_factory=dict)  # norm path -> primary type
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"paths": list(self.paths), "types": self.types, "hash": self.hash}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Signature:
        return cls(
            paths=tuple(data.get("paths", [])),
            types=dict(data.get("types", {})),
            hash=str(data.get("hash", "")),
        )


def _hash_paths(paths: tuple[str, ...]) -> str:
    payload = json.dumps(sorted(paths), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def source_signature(profile: SourceProfile, *,
                     ignore: tuple[str, ...] = ("internal_id", "_scraped_at"),
                     ) -> Signature:
    """Compute a Signature from a SourceProfile.

    `ignore` drops obviously-irrelevant bookkeeping fields so their presence or
    absence doesn't fracture a shape into near-duplicate signatures."""
    ignore_norm = {_normalize_path(p) for p in ignore}
    norm_types: dict[str, str] = {}
    for f in profile.fields:
        np = _normalize_path(f.path)
        if np in ignore_norm:
            continue
        # first-seen wins; profile.fields is already sorted by path (stable)
        norm_types.setdefault(np, f.primary_type)
    paths = tuple(sorted(norm_types))
    return Signature(paths=paths, types=norm_types, hash=_hash_paths(paths))


def signature_of(records: list[dict[str, Any]]) -> Signature:
    """Convenience: profile a batch and compute its signature in one call."""
    from .source_profile import profile_source
    return source_signature(profile_source(records))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class SignatureMatch:
    band: str
    score: float                    # path-set Jaccard similarity
    type_mismatches: tuple[str, ...] = ()   # shared paths whose primary type differs
    added_paths: tuple[str, ...] = ()       # in candidate, not in reference
    removed_paths: tuple[str, ...] = ()     # in reference, not in candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "score": round(self.score, 4),
            "type_mismatches": list(self.type_mismatches),
            "added_paths": list(self.added_paths),
            "removed_paths": list(self.removed_paths),
        }


def compare(candidate: Signature, reference: Signature) -> SignatureMatch:
    """Compare an incoming signature against a known (reference) signature.

    The band is driven by path-set similarity; a HIGH path match is DOWNGRADED
    to MODERATE when shared paths disagree on primary type (a classic drift:
    same keys, a field's type changed), so a type change never silently rides
    the fast replay path."""
    cand_set, ref_set = set(candidate.paths), set(reference.paths)
    score = _jaccard(cand_set, ref_set)
    added = tuple(sorted(cand_set - ref_set))
    removed = tuple(sorted(ref_set - cand_set))

    shared = cand_set & ref_set
    type_mismatches = tuple(sorted(
        p for p in shared
        if p in candidate.types and p in reference.types
        and candidate.types[p] != reference.types[p]
    ))

    if score >= HIGH_BAND:
        band = BAND_HIGH if not type_mismatches else BAND_MODERATE
    elif score >= MODERATE_BAND:
        band = BAND_MODERATE
    elif score > 0.0:
        band = BAND_LOW
    else:
        band = BAND_NONE
    return SignatureMatch(band=band, score=score, type_mismatches=type_mismatches,
                          added_paths=added, removed_paths=removed)


def best_match(candidate: Signature,
               references: dict[str, Signature]) -> tuple[str | None, SignatureMatch]:
    """Pick the best-matching reference signature from a library.

    `references` maps a profile id -> its learned Signature. Returns
    (profile_id | None, SignatureMatch). Ties break on profile id (deterministic);
    a NONE band yields (None, ...) so the caller treats the shape as novel."""
    best_id: str | None = None
    best = SignatureMatch(band=BAND_NONE, score=-1.0)
    for pid in sorted(references):
        m = compare(candidate, references[pid])
        if (m.score, pid) > (best.score, best_id or ""):
            best, best_id = m, pid
    if best.band == BAND_NONE or best_id is None:
        return None, SignatureMatch(band=BAND_NONE, score=max(best.score, 0.0))
    return best_id, best


__all__ = [
    "BAND_HIGH",
    "BAND_LOW",
    "BAND_MODERATE",
    "BAND_NONE",
    "HIGH_BAND",
    "MODERATE_BAND",
    "Signature",
    "SignatureMatch",
    "best_match",
    "compare",
    "signature_of",
    "source_signature",
]
