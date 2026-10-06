"""Bounded, deterministic value transforms applied during execution.

Every transform is a pure function (value, target_field) -> (new_value, op, ok).
`ok=False` means the value could not be transformed safely; the executor then
leaves the target unfilled and marks it needs_review (NO fabrication, NO lossy
guessing). Transforms chosen per target by `plan_transform`:

  copy          type already matches -> pass through
  cast_number   string -> int/float when it parses cleanly
  parse_date    recognizable date -> ISO 'YYYY-MM-DD' (or year -> 'YYYY')
  alias         enum/taxonomy alias -> canonical value
  coerce_enum   validate against a controlled vocabulary (pass or fail)
  split_list    delimited string -> array of trimmed items

No locale guessing, no fuzzy parsing: ambiguous input fails closed.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .target_profile import TargetField

TransformResult = tuple[Any, str, bool]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR = re.compile(r"^\d{4}$")
_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d",
    "%d-%m-%Y", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%Y",
)


def cast_number(value: Any, want_int: bool) -> TransformResult:
    if isinstance(value, bool):
        return value, "copy", False  # don't silently treat bool as number
    if isinstance(value, int | float):
        n: Any = int(value) if want_int else float(value)
        return n, "copy", True
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        try:
            if want_int:
                # allow "90" and "90.0" -> 90 but not "90.5" -> int (lossy)
                f = float(s)
                if f.is_integer():
                    return int(f), "cast_number", True
                return value, "cast_number", False
            return float(s), "cast_number", True
        except ValueError:
            return value, "cast_number", False
    return value, "cast_number", False


def parse_date(value: Any, year_only: bool) -> TransformResult:
    if isinstance(value, int) and not isinstance(value, bool):
        if year_only and 1000 <= value <= 9999:
            return str(value), "parse_date", True
    if not isinstance(value, str):
        return value, "parse_date", False
    s = value.strip()
    if year_only and _YEAR.match(s):
        return s, "parse_date", True
    if _ISO_DATE.match(s):
        if year_only:
            return s[:4], "parse_date", True
        return s, "copy", True
    for fmt in _DATE_FORMATS:
        try:
            dt: date = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        return (str(dt.year) if year_only else dt.isoformat()), "parse_date", True
    return value, "parse_date", False


def apply_alias(value: Any, tgt: TargetField) -> TransformResult:
    """Map a known alias to its canonical value; pass through if already canonical."""
    if isinstance(value, str) and value in tgt.aliases:
        return tgt.aliases[value], "alias", True
    return value, "copy", True


def coerce_enum(value: Any, tgt: TargetField) -> TransformResult:
    """Validate against the controlled vocabulary. Fail closed if not a member."""
    canon, _op, _ok = apply_alias(value, tgt)
    if not tgt.enum:
        return canon, "copy", True
    if canon in tgt.enum:
        return canon, "coerce_enum", True
    return value, "coerce_enum", False


def split_list(value: Any, tgt: TargetField) -> TransformResult:
    if isinstance(value, list):
        return value, "copy", True
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[;,/|]", value) if p.strip()]
        if parts:
            # normalize each item against aliases if the element is enumerated
            if tgt.aliases:
                parts = [tgt.aliases.get(p, p) for p in parts]
            return parts, "split_list", True
    return value, "split_list", False


def plan_and_apply(value: Any, tgt: TargetField) -> TransformResult:
    """Choose and apply the right transform for a target field + value.

    Returns (new_value, op, ok). ok=False -> executor leaves it needs_review."""
    if value is None:
        return None, "copy", False

    want = tgt.primary_type

    if want == "array":
        v, op, ok = split_list(value, tgt)
        return v, op, ok

    if tgt.enum:
        return coerce_enum(value, tgt)

    if want in ("integer", "number"):
        return cast_number(value, want_int=(want == "integer"))

    if want == "string":
        year_only = _looks_year_target(tgt)
        if tgt.fmt == "date" or year_only or _looks_date_target(tgt):
            return parse_date(value, year_only=year_only)
        if tgt.aliases:
            return apply_alias(value, tgt)
        # plain string: stringify scalars, reject containers
        if isinstance(value, dict | list):
            return value, "copy", False
        return (value if isinstance(value, str) else str(value)), "copy", True

    # unknown/other target type: only pass through on exact type agreement
    return value, "copy", False


def _looks_year_target(tgt: TargetField) -> bool:
    """A target that wants a 4-digit year: 'year' in the name or a ^\\d{4}$
    style pattern. Checked before the full-date path so we emit 'YYYY'."""
    pat = tgt.pattern or ""
    if r"\d{4}" in pat and r"\d{2}" not in pat:
        return True
    return "year" in tgt.tokens


def _looks_date_target(tgt: TargetField) -> bool:
    pat = tgt.pattern or ""
    return r"\d{4}-\d{2}-\d{2}" in pat or "date" in tgt.tokens


__all__ = [
    "TransformResult",
    "apply_alias",
    "cast_number",
    "coerce_enum",
    "parse_date",
    "plan_and_apply",
    "split_list",
]
