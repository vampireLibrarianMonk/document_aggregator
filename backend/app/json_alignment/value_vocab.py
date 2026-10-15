"""Human-taught, per-project VALUE VOCABULARY — accepted values a reviewer adds
to a golden field's allowed set, remembered and replayed deterministically.

Motivation
----------
The value-evidence gate rejects a value that is not in a target field's declared
taxonomy (e.g. platform allows {Windows PC, Xbox, PlayStation, Nintendo Switch};
'Linux' is vetoed). That is correct precision behavior for an UNKNOWN value — the
matcher must not guess that an unlisted value is genuine (a 'Linux' platform is
indistinguishable, by value alone, from a 'Steam' storefront junk value). The
safe resolution is NOT to weaken the gate but to let a HUMAN teach the system,
once, that a specific value is allowed for a specific field — then remember it.

This module is that memory. It is deliberately governed to never become a
backdoor that silently re-admits junk:

  - CRUD, not write-only: every learned value can be listed, edited, and
    DELETED. A deletion takes effect on the next load (the value is excluded
    from the merged taxonomy, so it is vetoed again immediately).
  - Provenance per entry: who/when/why + the example that justified it, so the
    whole vocabulary is an auditable artifact.
  - Built-in vs learned stays SEPARATE: the schema taxonomy is authoritative and
    untouched on disk; learned values are an explicit overlay merged in only at
    load time. A reader can always tell which values came from the schema and
    which a human added.
  - No bulk / no auto-accept: entries are added one deliberate decision at a
    time (a model may PROPOSE, a human COMMITS).

Integration
-----------
`merge_vocab_into_schema(schema, vocab)` is a PURE function that folds the
learned values of each field into that field's `x-pydi-taxonomy` (and optional
alias into `x-pydi-taxonomy-aliases`). It is applied wherever a project's golden
schema is loaded for alignment, so the matcher / `coerce_enum` / region veto see
a larger allowed set with ZERO changes to the gate itself. Determinism holds:
the merged schema is a pure function of (schema, stored vocab).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

VOCAB_VERSION = 1

# Schema keys the merge writes into (the same vendor-prefixed keys extract_target
# already reads, so no extractor change is needed).
_TAXONOMY_KEY = "x-pydi-taxonomy"
_ALIASES_KEY = "x-pydi-taxonomy-aliases"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VocabEntry:
    """One human-accepted value for a target field.

    value      the accepted value, as it should appear in the golden output
               (the canonical form).
    alias      an optional raw/source spelling that should normalize TO `value`
               (e.g. alias 'linux' -> value 'Linux'); None when the accepted
               value needs no alias.
    added_by   provenance: who accepted it (free text / user id).
    added_at   ISO-8601 UTC timestamp (set on create, preserved on edit unless
               the value itself changes materially).
    note       optional human justification.
    example    optional source example that motivated the acceptance (audit).
    """
    value: str
    alias: str | None = None
    added_by: str = ""
    added_at: str = field(default_factory=_now_iso)
    note: str = ""
    example: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "alias": self.alias,
            "added_by": self.added_by,
            "added_at": self.added_at,
            "note": self.note,
            "example": self.example,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VocabEntry:
        return cls(
            value=str(d.get("value", "")),
            alias=(str(d["alias"]) if d.get("alias") not in (None, "") else None),
            added_by=str(d.get("added_by", "")),
            added_at=str(d.get("added_at", "")) or _now_iso(),
            note=str(d.get("note", "")),
            example=str(d.get("example", "")),
        )


@dataclass
class ValueVocabulary:
    """Per-project learned values, keyed by target FIELD name.

    `by_field[target]` is the ordered list of VocabEntry a human accepted for
    that golden field. Only fields with at least one learned value appear."""
    by_field: dict[str, list[VocabEntry]] = field(default_factory=dict)
    version: int = VOCAB_VERSION
    meta: dict[str, Any] = field(default_factory=dict)

    # -- CRUD ---------------------------------------------------------------

    def list_field(self, target: str) -> list[VocabEntry]:
        """Read: the learned entries for one field (empty list if none)."""
        return list(self.by_field.get(target, []))

    def add(self, target: str, value: str, *, alias: str | None = None,
            added_by: str = "", note: str = "", example: str = "") -> VocabEntry:
        """Create (or idempotently refresh) a learned value for a field.

        Adding a value that already exists for the field UPDATES that entry's
        metadata rather than duplicating it (so repeated accepts are safe)."""
        value = value.strip()
        if not value:
            raise ValueError("cannot accept an empty value")
        entries = self.by_field.setdefault(target, [])
        existing = next((e for e in entries if e.value == value), None)
        if existing is not None:
            if alias is not None:
                existing.alias = alias.strip() or None
            if added_by:
                existing.added_by = added_by
            if note:
                existing.note = note
            if example:
                existing.example = example
            return existing
        entry = VocabEntry(
            value=value, alias=(alias.strip() or None) if alias else None,
            added_by=added_by, note=note, example=example)
        entries.append(entry)
        return entry

    def update(self, target: str, value: str, *, new_value: str | None = None,
               alias: str | None = ...,   # type: ignore[assignment]
               note: str | None = ...,    # type: ignore[assignment]
               example: str | None = ...,  # type: ignore[assignment]
               ) -> VocabEntry:
        """Update an existing learned value (rename, re-alias, re-note).

        Each of `alias`, `note`, `example` uses the Ellipsis sentinel: OMIT
        (leave as `...`) to keep the stored value UNCHANGED, pass a str to set it
        (empty string clears). This prevents a partial update from silently
        wiping provenance a reviewer did not intend to touch (adversarial review:
        a default note="" was overwriting the stored note). Raises KeyError if
        the value isn't learned for the field."""
        entries = self.by_field.get(target, [])
        entry = next((e for e in entries if e.value == value), None)
        if entry is None:
            raise KeyError(f"{target!r} has no learned value {value!r}")
        if new_value is not None:
            nv = new_value.strip()
            if not nv:
                raise ValueError("new_value cannot be empty")
            entry.value = nv
            entry.added_at = _now_iso()  # material change -> refresh timestamp
        if alias is not Ellipsis:
            entry.alias = (alias.strip() or None) if isinstance(alias, str) else None
        if note is not Ellipsis:
            entry.note = note or ""
        if example is not Ellipsis:
            entry.example = example or ""
        return entry

    def remove(self, target: str, value: str) -> bool:
        """Delete a learned value. Returns True if something was removed. The
        deletion takes effect at the next merge (the value is no longer folded
        into the taxonomy, so the gate vetoes it again)."""
        entries = self.by_field.get(target)
        if not entries:
            return False
        kept = [e for e in entries if e.value != value]
        removed = len(kept) != len(entries)
        if kept:
            self.by_field[target] = kept
        else:
            del self.by_field[target]
        return removed

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "meta": self.meta,
            "by_field": {
                tgt: [e.to_dict() for e in entries]
                for tgt, entries in sorted(self.by_field.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValueVocabulary:
        by_field = {
            str(tgt): [VocabEntry.from_dict(e) for e in entries]
            for tgt, entries in (data.get("by_field", {}) or {}).items()
        }
        return cls(
            by_field=by_field,
            version=int(data.get("version", VOCAB_VERSION)),
            meta=dict(data.get("meta", {})),
        )


def merge_vocab_into_schema(schema: dict[str, Any],
                            vocab: ValueVocabulary) -> dict[str, Any]:
    """Return a NEW schema with each field's learned values folded into its
    taxonomy + aliases. PURE: does not mutate `schema` or `vocab`; the result is
    a deterministic function of both.

    For a target field with learned entries:
      - each entry's `value` is appended to the field's `x-pydi-taxonomy`
        (deduped, schema values first, then learned in stored order);
      - each entry's `alias` (if any) is added to `x-pydi-taxonomy-aliases`
        mapping alias -> value, WITHOUT overwriting an existing schema alias.
    A field with no learned values is left exactly as the schema declares it.
    Only fields that exist in the schema's `properties` are touched (a learned
    value for an unknown field is ignored by the merge — the CRUD layer should
    prevent that, but the merge stays safe either way)."""
    if not vocab.by_field:
        return schema
    merged = copy.deepcopy(schema)
    props = merged.get("properties")
    if not isinstance(props, dict):
        return merged
    for tgt, entries in vocab.by_field.items():
        node = props.get(tgt)
        if not isinstance(node, dict) or not entries:
            continue
        tax = list(node.get(_TAXONOMY_KEY, []) or [])
        seen = {str(v) for v in tax}
        for e in entries:
            if e.value not in seen:
                tax.append(e.value)
                seen.add(e.value)
        node[_TAXONOMY_KEY] = tax
        aliases = dict(node.get(_ALIASES_KEY, {}) or {})
        for e in entries:
            if e.alias and e.alias not in aliases:
                aliases[e.alias] = e.value
        if aliases:
            node[_ALIASES_KEY] = aliases
    return merged


__all__ = [
    "VOCAB_VERSION",
    "ValueVocabulary",
    "VocabEntry",
    "merge_vocab_into_schema",
]
