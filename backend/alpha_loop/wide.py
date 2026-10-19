"""Wide/deep synthetic generator — the JSON SIZE stress dimension.

Our game schema has 8 flat fields, so it cannot reveal the real "file length
factor": the matcher's inference is O(source_fields x target_fields), and the
source profiler flattens nested objects. This module synthesizes:

  - a GOLDEN target schema with `width` top-level fields (the destination). The
    matcher reads ONLY top-level target properties (extract_target), so width is
    the target-side cost driver.
  - SOURCE records with the same `width` logical fields, renamed to synonyms
    (so matching is non-trivial) and optionally NESTED `depth` levels deep under
    wrappers (so profile_source must flatten deep paths and the matcher sees
    long dotted source paths).

Carries ground truth (source_path -> golden_field) like the stage generator, so
the correctness adversary scores it the same way. Deterministic from a seed.

This is offline + synthetic by design (no dataset download). If a real large
JSON becomes available, point a probe at it instead; this fills the gap now.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .generator import FiredDoc

# A pool of field-name stems + a synonym for each, so source keys differ from
# golden keys (forcing the synonym/description tiers, not a trivial exact match).
_STEMS = [
    "name", "title", "author", "editor", "year", "month", "day", "region",
    "country", "city", "status", "category", "priority", "owner", "team",
    "cost", "price", "quantity", "weight", "height", "width", "depth", "color",
    "material", "vendor", "sku", "rating", "score", "level", "phase", "stage",
    "code", "label", "tag", "kind", "type", "format", "version", "revision",
    "source", "target", "channel", "medium", "segment", "cohort", "tier",
]
_SYN = {
    "name": "full_name", "title": "headline", "author": "written_by",
    "editor": "edited_by", "year": "yr", "month": "mo", "day": "dy",
    "region": "geo_region", "country": "nation", "city": "locality",
    "status": "state", "category": "cat", "priority": "prio", "owner": "held_by",
    "team": "group", "cost": "expense", "price": "amount", "quantity": "qty",
    "weight": "mass", "height": "ht", "width": "wd", "depth": "dp",
    "color": "colour", "material": "substance", "vendor": "supplier",
    "sku": "stock_code", "rating": "grade", "score": "points", "level": "lvl",
    "phase": "ph", "stage": "stg", "code": "identifier", "label": "caption",
    "tag": "marker", "kind": "variety", "type": "classification",
    "format": "layout", "version": "ver", "revision": "rev", "source": "origin",
    "target": "destination", "channel": "pathway", "medium": "carrier",
    "segment": "slice", "cohort": "band", "tier": "rank",
}


@dataclass
class WideSpec:
    width: int = 8          # number of golden fields
    depth: int = 0          # nesting depth of SOURCE fields (0 = flat)
    rename: bool = True      # use synonyms for source keys (non-trivial match)

    @property
    def label(self) -> str:
        return f"w{self.width}_d{self.depth}{'_syn' if self.rename else ''}"


def _field_names(width: int) -> list[str]:
    """Deterministic, MUTUALLY-UNAMBIGUOUS golden field names up to `width`.

    Unique stems are used first; beyond the pool, names get a distinct ordinal
    suffix. Critically, to keep the width sweep a clean PERFORMANCE probe (not a
    semantic-ambiguity trap), every name is made lexically distinct: once the
    stem pool is exhausted we switch to a fully synthetic, collision-free scheme
    (`field_<i>`) so no two golden fields are near-duplicates and the fuzzy/
    description tiers have no legitimate reason to cross-wire."""
    out: list[str] = []
    pool = _STEMS
    for i in range(width):
        if i < len(pool):
            out.append(pool[i])
        else:
            out.append(f"field_{i:04d}")
    return out


def build_schema(spec: WideSpec) -> dict[str, Any]:
    """A flat golden JSON Schema with `width` string fields. First two are
    required (so the break criterion — required-field recovery — applies)."""
    names = _field_names(spec.width)
    props: dict[str, Any] = {}
    for n in names:
        props[n] = {"type": "string", "description": f"the {n.replace('_', ' ')}"}
    return {
        "$id": f"https://example.com/schemas/wide_{spec.width}.json",
        "title": f"wide{spec.width}",
        "type": "object",
        "description": f"Synthetic {spec.width}-field golden schema.",
        "properties": props,
        "required": names[:2],
    }


def _nest(key: str, value: Any, depth: int) -> tuple[str, Any, str]:
    """Wrap (key, value) under `depth` wrapper objects. Returns
    (top_wrapper_key, nested_object, dotted_source_path). depth 0 = flat."""
    if depth <= 0:
        return key, value, key
    # Build wrappers w0.w1...w(depth-1).key
    path_parts = [f"w{i}" for i in range(depth)] + [key]
    dotted = ".".join(path_parts)
    # Construct nested dict from the inside out.
    obj: Any = value
    for part in reversed(path_parts[1:]):  # wrap key..w1 under w0
        obj = {part: obj}
    # obj is now {w1: {...: {key: value}}}; wrap once more under w0 handled below
    # Simpler: rebuild cleanly.
    node: Any = value
    for part in reversed(path_parts[1:]):
        node = {part: node}
    return path_parts[0], node, dotted


def build_record(spec: WideSpec, rng: random.Random
                 ) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """One source record for the spec, plus its truth map (source_path ->
    golden) and a descriptions sidecar (source_path -> honest description).

    Each golden field is emitted under a (possibly renamed, possibly nested)
    source key with a plausible string value. For pool-exhausted synthetic
    fields the source KEY shares no token with the golden name, so an honest
    description sidecar (the metadata a real source ships) carries the signal —
    this mirrors the opaque-key-with-description case and keeps matching
    recoverable without name collisions."""
    names = _field_names(spec.width)
    record: dict[str, Any] = {}
    truth: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for gi, gname in enumerate(names):
        synthetic = gname not in _SYN
        if spec.rename:
            src_key = _SYN.get(gname) or f"src_{gi:04d}"
        else:
            src_key = gname
        value = f"{gname}_val_{rng.randint(1000, 9999)}"
        top_key, node, dotted = _nest(src_key, value, spec.depth)
        if spec.depth <= 0:
            record[src_key] = value
            truth[src_key] = gname
            path = src_key
        else:
            _deep_merge(record, {top_key: node})
            truth[dotted] = gname
            path = dotted
        # Honest description signal for synthetic (token-disjoint) keys so the
        # description tier can recover them without a name collision.
        if spec.rename and synthetic:
            descriptions[path] = f"the {gname.replace('_', ' ')}"
    return record, truth, descriptions


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def make_wide_doc(spec: WideSpec, *, n_records: int = 3, seed: int = 1337,
                  doc_id: str | None = None) -> FiredDoc:
    """Build a FiredDoc for a wide/deep spec (records + carried truth)."""
    records: list[dict[str, Any]] = []
    truth: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for i in range(n_records):
        rng = random.Random(f"{seed}:{spec.label}:{i}")
        rec, t, desc = build_record(spec, rng)
        records.append(rec)
        for k, g in t.items():
            truth.setdefault(k, g)
        for k, d in desc.items():
            descriptions.setdefault(k, d)
    return FiredDoc(
        doc_id=doc_id or f"wide_{spec.label}_{n_records}recs.json",
        records=records, truth=truth, descriptions=descriptions,
        shape_label=spec.label)
