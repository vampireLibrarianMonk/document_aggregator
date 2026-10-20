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

VOCAB_VERSION = 3

# Schema keys the merge writes into (the same vendor-prefixed keys extract_target
# already reads, so no extractor change is needed).
_TAXONOMY_KEY = "x-pydi-taxonomy"
_ALIASES_KEY = "x-pydi-taxonomy-aliases"
# Audit marker written into the EFFECTIVE schema for a free-form field (never
# persisted to the stored schema; the merge is pure).
_FREE_FORM_KEY = "x-pydi-free-form"


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
class AliasEntry:
    """One human-taught alias for a target field: raw_spelling -> canonical value.

    alias     the raw/source spelling that should normalize TO `value` (stored
              verbatim, trimmed; case-sensitive match, mirroring how the
              matcher's apply_alias does an exact `value in tgt.aliases`).
    value     the CANONICAL accepted value this alias maps to; must be an
              accepted value of the field at add time (built-in OR learned).
    added_by  provenance (free text / user id).
    added_at  ISO-8601 UTC timestamp.
    note      optional human justification.
    """
    alias: str
    value: str
    added_by: str = ""
    added_at: str = field(default_factory=_now_iso)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "value": self.value,
            "added_by": self.added_by,
            "added_at": self.added_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AliasEntry:
        return cls(
            alias=str(d.get("alias", "")),
            value=str(d.get("value", "")),
            added_by=str(d.get("added_by", "")),
            added_at=str(d.get("added_at", "")) or _now_iso(),
            note=str(d.get("note", "")),
        )


@dataclass
class ValueVocabulary:
    """Per-project learned values, keyed by target FIELD name.

    `by_field[target]` is the ordered list of VocabEntry a human accepted for
    that golden field. Only fields with at least one learned value appear.

    `free_form_fields` is the set of golden fields a human marked FREE-FORM:
    the field is treated as unconstrained (the value gate vetoes nothing for it)
    and its built-in/learned values are overridden — retained in the vocab but
    skipped at merge time, so turning free-form off re-folds them automatically.
    Free-form is a property of the FIELD, not of any single learned value, and a
    field can be free-form with zero learned values."""
    by_field: dict[str, list[VocabEntry]] = field(default_factory=dict)
    free_form_fields: set[str] = field(default_factory=set)
    by_field_aliases: dict[str, list[AliasEntry]] = field(default_factory=dict)
    version: int = VOCAB_VERSION
    meta: dict[str, Any] = field(default_factory=dict)

    # -- free-form ----------------------------------------------------------

    def set_free_form(self, target: str, on: bool) -> None:
        """Mark (or unmark) a field free-form."""
        if on:
            self.free_form_fields.add(target)
        else:
            self.free_form_fields.discard(target)

    def is_free_form(self, target: str) -> bool:
        return target in self.free_form_fields

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

    # -- alias CRUD (store-level; conflict validation lives in project_store) --

    def list_field_aliases(self, target: str) -> list[AliasEntry]:
        """Read: the first-class aliases for one field (empty list if none)."""
        return list(self.by_field_aliases.get(target, []))

    def add_alias(self, target: str, alias: str, value: str, *,
                  added_by: str = "", note: str = "") -> AliasEntry:
        """Store-level append (or idempotent refresh) of a first-class alias.

        No conflict validation here — that is the server layer's job
        (project_store.apply_vocab_change). Adding an alias whose raw spelling
        already exists on the field refreshes that entry's metadata rather than
        duplicating it."""
        alias = alias.strip()
        value = value.strip()
        entries = self.by_field_aliases.setdefault(target, [])
        existing = next((a for a in entries if a.alias == alias), None)
        if existing is not None:
            existing.value = value
            if added_by:
                existing.added_by = added_by
            if note:
                existing.note = note
            return existing
        entry = AliasEntry(alias=alias, value=value, added_by=added_by, note=note)
        entries.append(entry)
        return entry

    def remove_alias(self, target: str, alias: str) -> bool:
        """Delete a first-class alias by its raw spelling. Returns True if
        something was removed. Prunes the field key when the list empties."""
        entries = self.by_field_aliases.get(target)
        if not entries:
            return False
        kept = [a for a in entries if a.alias != alias]
        removed = len(kept) != len(entries)
        if kept:
            self.by_field_aliases[target] = kept
        else:
            del self.by_field_aliases[target]
        return removed

    def remove_aliases_for_value(self, target: str, value: str) -> list[AliasEntry]:
        """Drop every alias on `target` whose canonical value == `value` (the
        cascade when a learned value is removed). Returns the dropped list and
        prunes the field key when the list empties."""
        entries = self.by_field_aliases.get(target)
        if not entries:
            return []
        dropped = [a for a in entries if a.value == value]
        if not dropped:
            return []
        kept = [a for a in entries if a.value != value]
        if kept:
            self.by_field_aliases[target] = kept
        else:
            del self.by_field_aliases[target]
        return dropped

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "meta": self.meta,
            "by_field": {
                tgt: [e.to_dict() for e in entries]
                for tgt, entries in sorted(self.by_field.items())
            },
            "free_form_fields": sorted(self.free_form_fields),
            # Outer keys sorted by field name (matching by_field); each field's
            # alias list kept in stored append/insertion order (never reordered)
            # so serialization is fully deterministic at both levels.
            "by_field_aliases": {
                tgt: [a.to_dict() for a in aliases]
                for tgt, aliases in sorted(self.by_field_aliases.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValueVocabulary:
        by_field = {
            str(tgt): [VocabEntry.from_dict(e) for e in entries]
            for tgt, entries in (data.get("by_field", {}) or {}).items()
        }
        # Absent key (a v1/v2 doc) -> empty dict -> today's behavior.
        by_field_aliases = {
            str(tgt): [AliasEntry.from_dict(a) for a in aliases]
            for tgt, aliases in (data.get("by_field_aliases", {}) or {}).items()
        }
        # Read-time idempotent migration (design §2): promote any legacy
        # per-entry VocabEntry.alias to a first-class AliasEntry. Iterate learned
        # entries in stored order; append only when no AliasEntry on that field
        # already carries the same raw-spelling key (first-wins by stored order
        # on a same-raw collision). entry.alias is left as-is (not nulled) so a
        # rolled-back / older build still folds it. Idempotent: a doc saved after
        # migration already has the AliasEntry, so the guard skips it.
        for tgt, entries in by_field.items():
            promoted = by_field_aliases.setdefault(tgt, [])
            existing_keys = {a.alias for a in promoted}
            for e in entries:
                if e.alias and e.alias not in existing_keys:
                    promoted.append(AliasEntry(
                        alias=e.alias, value=e.value, added_by=e.added_by,
                        added_at=e.added_at,
                        note="(migrated from learned value alias)"))
                    existing_keys.add(e.alias)
            if not promoted:
                del by_field_aliases[tgt]
        return cls(
            by_field=by_field,
            # Absent key (a v1 doc) -> empty set -> today's behavior.
            free_form_fields=set(data.get("free_form_fields", []) or []),
            by_field_aliases=by_field_aliases,
            version=int(data.get("version", VOCAB_VERSION)),
            meta=dict(data.get("meta", {})),
        )


def _as_type_set(node: dict[str, Any]) -> set[str]:
    """The node's declared JSON Schema types as a set (mirrors
    target_profile._as_types; do NOT import the private helper across modules)."""
    t = node.get("type")
    if isinstance(t, str):
        return {t}
    return set(t or [])


def _strip_field_constraints(node: dict[str, Any]) -> None:
    """Remove every value constraint extract_target would read for this field so
    the effective field is genuinely free-text. Covers scalar fields (top-level
    enum/taxonomy) AND array fields whose constraint lives under `items` (e.g.
    genres[] with items.enum). Also pops the never-read items.x-pydi-taxonomy as
    a defensive over-strip (harmless; keeps the strip robust)."""
    node.pop(_TAXONOMY_KEY, None)
    node.pop("enum", None)
    node.pop(_ALIASES_KEY, None)
    items = node.get("items")
    if isinstance(items, dict):
        items.pop("enum", None)
        items.pop(_TAXONOMY_KEY, None)
        items.pop(_ALIASES_KEY, None)


def _fold_values(node: dict[str, Any], entries: list[VocabEntry],
                 field_aliases: list[AliasEntry]) -> None:
    """Fold learned values into the SAME constraint slot extract_target reads
    for this field, so the value gate actually widens. A constrained array
    (items.enum — the only array constraint extract_target reads) folds VALUES
    into its `items.enum`. Everything else — a scalar free-text field, OR an
    array whose only constraint was the never-read items.x-pydi-taxonomy — folds
    VALUES into the TOP-LEVEL x-pydi-taxonomy.

    ALIASES, by contrast, ALWAYS fold into the TOP-LEVEL node's
    x-pydi-taxonomy-aliases (design §3, Option A). extract_target reads aliases
    only from the top-level node (never from `items`), so writing the alias map
    to the item node on a constrained array would land in a slot the matcher
    never reads — silently dropping the alias. Splitting value_node (item-aware)
    from alias_node (always top-level) makes the alias write slot coincide with
    the extractor's read slot for every field type, and repairs the identical
    latent legacy-alias-on-array bug as a bonus."""
    types = _as_type_set(node)
    items = node.get("items")
    is_constrained_array = (
        "array" in types
        and isinstance(items, dict)
        and "enum" in items          # ONLY items.enum is an array constraint extract_target reads
    )
    # value_node is a dict in both branches: `items` is dict-typed exactly when
    # is_constrained_array (the isinstance check above), else it is `node` (a
    # dict param). Narrow explicitly so mypy tracks the indexed writes below.
    value_node: dict[str, Any]
    if is_constrained_array:
        assert isinstance(items, dict)
        value_node = items   # item-aware value slot
    else:
        value_node = node
    alias_node = node                                      # aliases ALWAYS top-level

    if is_constrained_array:
        base = list(value_node.get("enum", []) or [])
        seen = {str(v) for v in base}
        for e in entries:
            if e.value not in seen:
                base.append(e.value)
                seen.add(e.value)
        value_node["enum"] = base
    else:
        tax = list(value_node.get(_TAXONOMY_KEY, []) or [])
        seen = {str(v) for v in tax}
        for e in entries:
            if e.value not in seen:
                tax.append(e.value)
                seen.add(e.value)
        value_node[_TAXONOMY_KEY] = tax

    # Alias write order/precedence on the TOP-LEVEL node:
    #   1. existing schema aliases already on the node (authoritative, kept);
    #   2. legacy entry.alias from learned values (dedup on key);
    #   3. first-class AliasEntry from by_field_aliases (dedup on key).
    # Later sources never overwrite an earlier key.
    aliases = dict(alias_node.get(_ALIASES_KEY, {}) or {})
    for e in entries:
        if e.alias and e.alias not in aliases:
            aliases[e.alias] = e.value
    for a in field_aliases:
        if a.alias and a.alias not in aliases:
            aliases[a.alias] = a.value
    if aliases:
        alias_node[_ALIASES_KEY] = aliases


def merge_vocab_into_schema(schema: dict[str, Any],
                            vocab: ValueVocabulary) -> dict[str, Any]:
    """Return a NEW schema with each field's learned values folded into the slot
    extract_target reads for it (items-aware), and each FREE-FORM field stripped
    of its constraints so it is unconstrained in the effective schema. PURE:
    does not mutate `schema` or `vocab`; a deterministic function of both.

    For a non-free-form target field with learned entries:
      - each entry's `value` is folded into the field's constraint slot
        (`items.enum` for a constrained array, else top-level `x-pydi-taxonomy`),
        deduped, schema values first, then learned in stored order;
      - each entry's `alias` is added to that same node's aliases, WITHOUT
        overwriting an existing schema alias.
    For a FREE-FORM field: all value constraints (top-level enum/taxonomy and
    items.enum) are stripped and an audit marker `x-pydi-free-form` is set, so
    the matcher's value gate vetoes nothing — free-form WINS over learned values
    (which are retained in the vocab and re-fold when free-form is turned off).
    Only fields in the schema's `properties` are touched; the stored schema on
    disk is never mutated (reversible)."""
    if not vocab.by_field and not vocab.free_form_fields and not vocab.by_field_aliases:
        return schema
    merged = copy.deepcopy(schema)
    props = merged.get("properties")
    if not isinstance(props, dict):
        return merged
    for tgt, node in props.items():
        if not isinstance(node, dict):
            continue
        if tgt in vocab.free_form_fields:
            # Free-form wins: unconstrain this field in the effective schema.
            # First-class aliases are retained in the vocab but skipped here
            # (the strip + skip), so turning free-form off re-folds them.
            _strip_field_constraints(node)
            node[_FREE_FORM_KEY] = True
            continue
        entries = vocab.by_field.get(tgt, [])
        field_aliases = vocab.by_field_aliases.get(tgt, [])
        if not entries and not field_aliases:
            continue
        _fold_values(node, entries, field_aliases)
    return merged


__all__ = [
    "VOCAB_VERSION",
    "AliasEntry",
    "ValueVocabulary",
    "VocabEntry",
    "merge_vocab_into_schema",
]
