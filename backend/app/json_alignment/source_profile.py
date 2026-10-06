"""Source profiling: inventory a batch of incoming JSON records into a stable,
deterministic description of their field structure.

A SourceProfile is the evidence base for mapping. It records, per field path:
  - the dotted path (nested objects flattened with '.', arrays marked '[]')
  - observed JSON types (ranked by frequency, stable tie-break)
  - a few example values (first-seen, de-duplicated, capped)
  - how often the field was present (coverage)
  - normalized name tokens (for alias / token-overlap matching downstream)

No inference, no fabrication: this only reports what is literally present.
Determinism: field order is sorted by path; examples preserve first-seen order.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_MAX_EXAMPLES = 5
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def normalize_name(name: str) -> str:
    """Lower-case, split camelCase / snake / kebab, collapse to single spaces."""
    # camelCase -> camel Case
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    tokens = _TOKEN_RE.findall(spaced.lower())
    return " ".join(tokens)


def name_tokens(name: str) -> tuple[str, ...]:
    return tuple(normalize_name(name).split())


@dataclass
class FieldStat:
    """What we observed for one source field path across the batch."""
    path: str
    types: tuple[str, ...]              # observed types, most-common first
    examples: tuple[Any, ...]
    present: int                        # records in which this path appeared
    total: int                          # records scanned
    tokens: tuple[str, ...]             # normalized name tokens of the leaf key
    desc_tokens: tuple[str, ...] = ()   # tokens from an optional source description

    @property
    def coverage(self) -> float:
        return self.present / self.total if self.total else 0.0

    @property
    def primary_type(self) -> str:
        return self.types[0] if self.types else "null"


@dataclass
class SourceProfile:
    """Deterministic inventory of a batch of source records."""
    record_count: int
    fields: list[FieldStat] = field(default_factory=list)

    def by_path(self) -> dict[str, FieldStat]:
        return {f.path: f for f in self.fields}

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_count": self.record_count,
            "fields": [
                {
                    "path": f.path,
                    "types": list(f.types),
                    "examples": list(f.examples),
                    "present": f.present,
                    "total": f.total,
                    "coverage": round(f.coverage, 4),
                    "tokens": list(f.tokens),
                    "desc_tokens": list(f.desc_tokens),
                }
                for f in self.fields
            ],
        }


def _walk(obj: Any, prefix: str, out: dict[str, list[Any]]) -> None:
    """Flatten a record into {path: [leaf values]} with '[]' for array levels.

    Objects recurse by key; arrays recurse into each element under a '[]' path
    segment so repeated structures collapse onto one path (deterministic)."""
    if isinstance(obj, dict):
        for k in obj:
            child = f"{prefix}.{k}" if prefix else str(k)
            _walk(obj[k], child, out)
    elif isinstance(obj, list):
        child = f"{prefix}[]" if prefix else "[]"
        if not obj:
            out.setdefault(child, [])  # record the path even if empty
        for item in obj:
            _walk(item, child, out)
    else:
        out.setdefault(prefix, []).append(obj)


_DESC_STOP = frozenset(
    {"the", "a", "an", "of", "for", "to", "by", "in", "on", "and", "or",
     "this", "that", "value", "field", "given", "with"}
)


def description_tokens(description: str) -> tuple[str, ...]:
    """Normalized, stop-word-filtered tokens from a free-text field description.

    Used as an optional semantic signal (e.g. a source metadata document that
    describes each column) — symmetric to the target's description tokens."""
    toks = normalize_name(description).split()
    return tuple(t for t in toks if t not in _DESC_STOP and len(t) > 1)


def profile_source(records: list[dict[str, Any]],
                   descriptions: dict[str, str] | None = None) -> SourceProfile:
    """Inventory a batch of JSON records into a SourceProfile.

    `records` is a list of dict-shaped source objects (one entity each). The
    result is fully determined by the input and insensitive to Python dict
    ordering for the field list (sorted by path).

    `descriptions` optionally maps a field path to a human description (e.g. from
    a source metadata document); its tokens become `FieldStat.desc_tokens`, an
    extra honest matching signal — never fabricated, only used when provided."""
    descriptions = descriptions or {}
    total = len(records)
    types: dict[str, Counter] = {}
    examples: dict[str, list[Any]] = {}
    present: Counter = Counter()

    for rec in records:
        flat: dict[str, list[Any]] = {}
        _walk(rec, "", flat)
        for path, values in flat.items():
            present[path] += 1
            tc = types.setdefault(path, Counter())
            ex = examples.setdefault(path, [])
            if not values:  # empty array path
                tc["array"] += 1
            for v in values:
                tc[_json_type(v)] += 1
                if len(ex) < _MAX_EXAMPLES and v not in ex and v is not None:
                    ex.append(v)

    fields: list[FieldStat] = []
    for path in sorted(types):
        tc = types[path]
        # Most-common first; tie-break alphabetically for determinism.
        ordered = tuple(t for t, _ in sorted(tc.items(), key=lambda kv: (-kv[1], kv[0])))
        leaf = path.split(".")[-1].replace("[]", "")
        fields.append(
            FieldStat(
                path=path,
                types=ordered,
                examples=tuple(examples.get(path, [])),
                present=present[path],
                total=total,
                tokens=name_tokens(leaf),
                desc_tokens=description_tokens(descriptions.get(path, "")),
            )
        )
    return SourceProfile(record_count=total, fields=fields)


__all__ = [
    "FieldStat",
    "SourceProfile",
    "description_tokens",
    "name_tokens",
    "normalize_name",
    "profile_source",
]
