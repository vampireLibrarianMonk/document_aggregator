"""Target profiling: parse a canonical target JSON Schema into a flat, ordered
field inventory used as the mapping destination.

We read standard JSON Schema (draft-ish: type, properties, required, items,
enum, format, pattern) plus two optional semantic hints seen in benchmark
schemas (vendor-prefixed, so harmless if absent):
  x-pydi-taxonomy          an exhaustive enumeration / controlled vocabulary
  x-pydi-taxonomy-aliases  {alias: canonical} value-normalization hints

Each TargetField carries enough to drive mapping (name tokens, description
tokens) and validation (type, required, enum, pattern, numeric bounds). No
fabrication: fields come only from the schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .source_profile import name_tokens, normalize_name

_STOP = frozenset(
    {"the", "a", "an", "of", "for", "to", "by", "in", "on", "and", "or",
     "this", "that", "value", "field", "given"}
)


def _desc_tokens(description: str) -> tuple[str, ...]:
    toks = normalize_name(description).split()
    return tuple(t for t in toks if t not in _STOP and len(t) > 1)


@dataclass
class TargetField:
    """One destination attribute extracted from the target schema."""
    name: str                               # top-level property name
    types: tuple[str, ...]                  # allowed JSON types
    required: bool
    description: str = ""
    enum: tuple[Any, ...] = ()              # allowed values (controlled vocab)
    aliases: dict[str, Any] = field(default_factory=dict)  # alias -> canonical
    fmt: str = ""                           # JSON Schema "format" (e.g. date)
    pattern: str = ""                       # regex constraint
    minimum: float | None = None
    maximum: float | None = None
    item_type: str = ""                     # element type when types == array
    tokens: tuple[str, ...] = ()            # normalized name tokens
    desc_tokens: tuple[str, ...] = ()       # normalized description tokens

    @property
    def primary_type(self) -> str:
        return self.types[0] if self.types else "string"


@dataclass
class TargetSchema:
    """The parsed target schema: an ordered list of destination fields."""
    title: str
    fields: list[TargetField] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def by_name(self) -> dict[str, TargetField]:
        return {f.name: f for f in self.fields}

    def required_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


def _as_types(node: dict[str, Any]) -> tuple[str, ...]:
    t = node.get("type")
    if isinstance(t, list):
        return tuple(str(x) for x in t)
    if isinstance(t, str):
        return (t,)
    if "enum" in node:
        return ("string",)
    return ()


def extract_target(schema: dict[str, Any]) -> TargetSchema:
    """Parse a target JSON Schema object into a TargetSchema.

    Only top-level object properties become destination fields (the benchmark
    target is a flat object); nested object support can be layered later. Field
    order follows the schema's `properties` declaration order (deterministic)."""
    title = str(schema.get("title") or schema.get("$id") or "target")
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    fields: list[TargetField] = []
    for pname, node in props.items():
        if not isinstance(node, dict):
            continue
        types = _as_types(node)
        enum = tuple(node.get("enum", []) or [])
        items = node.get("items", {}) or {}
        item_type = ""
        if "array" in types and isinstance(items, dict):
            it = _as_types(items)
            item_type = it[0] if it else ""
            if not enum:
                enum = tuple(items.get("enum", []) or [])
        aliases = dict(node.get("x-pydi-taxonomy-aliases", {}) or {})
        # taxonomy may enumerate the controlled vocabulary when enum is absent
        if not enum:
            tax = node.get("x-pydi-taxonomy")
            if isinstance(tax, list):
                enum = tuple(tax)
        desc = str(node.get("description", "") or "")
        mn = node.get("minimum")
        mx = node.get("maximum")
        fields.append(
            TargetField(
                name=pname,
                types=types or ("string",),
                required=pname in required,
                description=desc,
                enum=enum,
                aliases=aliases,
                fmt=str(node.get("format", "") or ""),
                pattern=str(node.get("pattern", "") or ""),
                minimum=float(mn) if isinstance(mn, int | float) else None,
                maximum=float(mx) if isinstance(mx, int | float) else None,
                item_type=item_type,
                tokens=name_tokens(pname),
                desc_tokens=_desc_tokens(desc),
            )
        )
    return TargetSchema(title=title, fields=fields, raw=schema)


__all__ = [
    "TargetField",
    "TargetSchema",
    "extract_target",
]
