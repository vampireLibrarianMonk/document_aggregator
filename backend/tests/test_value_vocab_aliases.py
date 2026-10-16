"""Tests for first-class, per-field VALUE ALIASES (design Feature #2, FEAT-001).

Locks the store/dataclass CRUD, the pure merge fold (scalar + constrained array
+ free-form + purity + dedup), the read-time legacy-alias migration (idempotent,
first-wins on a same-raw collision), and v3 serialization ordering. Alias
membership and comparison are exact + case-sensitive throughout, matching the
matcher's gate.
"""
from __future__ import annotations

import copy

from app.json_alignment.value_vocab import (
    VOCAB_VERSION,
    AliasEntry,
    ValueVocabulary,
    merge_vocab_into_schema,
)

_SCALAR_SCHEMA = {
    "title": "game",
    "properties": {
        "platform": {
            "type": "string",
            "description": "the console or hardware system",
            "x-pydi-taxonomy": ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch"],
        },
        "ESRB": {
            "type": "string",
            "description": "ESRB age rating",
            "x-pydi-taxonomy": ["E", "E10+", "T", "M", "AO"],
        },
    },
}

_ARRAY_SCHEMA = {
    "title": "game",
    "properties": {
        "platform": {
            "type": "array",
            "description": "the consoles the title ships on",
            "items": {
                "type": "string",
                "enum": ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch"],
            },
        },
    },
}


# --- store / dataclass ------------------------------------------------------

def test_alias_entry_round_trip():
    a = AliasEntry(alias="PS5", value="PlayStation", added_by="rev", note="specific")
    again = AliasEntry.from_dict(a.to_dict())
    assert again == a
    # to_dict carries exactly the serialized keys (no 'example' on an alias).
    assert set(a.to_dict()) == {"alias", "value", "added_by", "added_at", "note"}


def test_add_alias_appends_and_lists():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation", added_by="r")
    aliases = v.list_field_aliases("platform")
    assert len(aliases) == 1
    assert aliases[0].alias == "PS5" and aliases[0].value == "PlayStation"


def test_add_alias_idempotent_same_pair():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation", added_by="a", note="first")
    v.add_alias("platform", "PS5", "PlayStation", added_by="b", note="second")
    aliases = v.list_field_aliases("platform")
    assert len(aliases) == 1              # no duplicate
    assert aliases[0].note == "second"    # metadata refreshed


def test_remove_alias_removes():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation")
    assert v.remove_alias("platform", "PS5") is True
    assert v.list_field_aliases("platform") == []
    assert "platform" not in v.by_field_aliases          # empty field pruned
    assert v.remove_alias("platform", "PS5") is False     # benign miss


def test_remove_aliases_for_value_cascade():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation")
    v.add_alias("platform", "PlayStation 5", "PlayStation")
    v.add_alias("platform", "Win", "Windows PC")
    dropped = v.remove_aliases_for_value("platform", "PlayStation")
    assert sorted(a.alias for a in dropped) == ["PS5", "PlayStation 5"]
    # the unrelated alias survives
    assert [a.alias for a in v.list_field_aliases("platform")] == ["Win"]


# --- merge (pure) -----------------------------------------------------------

def test_alias_folds_into_scalar_aliases_node():
    v = ValueVocabulary()
    v.add_alias("platform", "ps", "PlayStation")
    merged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    node = merged["properties"]["platform"]
    assert node["x-pydi-taxonomy-aliases"] == {"ps": "PlayStation"}


def test_alias_on_constrained_array_folds_to_top_level():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation")      # first-class
    merged = merge_vocab_into_schema(_ARRAY_SCHEMA, v)
    node = merged["properties"]["platform"]
    # alias lands on the TOP-LEVEL node, NOT on items
    assert node["x-pydi-taxonomy-aliases"] == {"PS5": "PlayStation"}
    assert "x-pydi-taxonomy-aliases" not in node["items"]
    # the value set stays in items.enum (unchanged — PlayStation already there)
    assert node["items"]["enum"] == ["Windows PC", "Xbox", "PlayStation", "Nintendo Switch"]

    # a LEGACY entry.alias on an array field now also folds top-level (§3 fix).
    v2 = ValueVocabulary()
    v2.add("platform", "Linux", alias="lnx")
    merged2 = merge_vocab_into_schema(_ARRAY_SCHEMA, v2)
    node2 = merged2["properties"]["platform"]
    assert node2["x-pydi-taxonomy-aliases"] == {"lnx": "Linux"}
    assert "x-pydi-taxonomy-aliases" not in node2["items"]
    assert node2["items"]["enum"][-1] == "Linux"       # value still folds into items.enum


def test_alias_targeting_builtin_value_folds():
    v = ValueVocabulary()
    v.add_alias("platform", "Win", "Windows PC")       # canonical is built-in only
    merged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    assert merged["properties"]["platform"]["x-pydi-taxonomy-aliases"] == {"Win": "Windows PC"}


def test_alias_fold_does_not_overwrite_schema_alias():
    schema = copy.deepcopy(_SCALAR_SCHEMA)
    schema["properties"]["platform"]["x-pydi-taxonomy-aliases"] = {"ps": "Xbox"}
    v = ValueVocabulary()
    v.add_alias("platform", "ps", "PlayStation")        # same key, different value
    merged = merge_vocab_into_schema(schema, v)
    # existing schema alias key wins (never overwritten)
    assert merged["properties"]["platform"]["x-pydi-taxonomy-aliases"]["ps"] == "Xbox"


def test_legacy_entry_alias_still_folds():
    v = ValueVocabulary()
    v.add("platform", "Linux", alias="linux")
    merged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    assert merged["properties"]["platform"]["x-pydi-taxonomy-aliases"] == {"linux": "Linux"}


def test_alias_only_vocab_merges():
    # vocab with ONLY by_field_aliases (no learned values, no free-form) must
    # still fold via the widened fast-path guard.
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation")
    merged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    assert merged is not _SCALAR_SCHEMA
    assert merged["properties"]["platform"]["x-pydi-taxonomy-aliases"] == {"PS5": "PlayStation"}


def test_merge_is_pure_with_aliases():
    schema_before = copy.deepcopy(_SCALAR_SCHEMA)
    v = ValueVocabulary()
    v.add("platform", "Linux")
    v.add_alias("platform", "PS5", "PlayStation")
    vocab_snapshot = copy.deepcopy(v.to_dict())
    merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    assert _SCALAR_SCHEMA == schema_before          # input schema unmutated
    assert v.to_dict() == vocab_snapshot            # vocab unmutated


def test_free_form_strips_aliases():
    v = ValueVocabulary()
    v.add_alias("platform", "PS5", "PlayStation")
    v.set_free_form("platform", True)
    merged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    node = merged["properties"]["platform"]
    assert "x-pydi-taxonomy-aliases" not in node    # stripped in effective schema
    assert node.get("x-pydi-free-form") is True
    # retained in the vocab
    assert [a.alias for a in v.list_field_aliases("platform")] == ["PS5"]
    # turning free-form off re-folds it
    v.set_free_form("platform", False)
    remerged = merge_vocab_into_schema(_SCALAR_SCHEMA, v)
    assert remerged["properties"]["platform"]["x-pydi-taxonomy-aliases"] == {"PS5": "PlayStation"}


# --- migration / compat -----------------------------------------------------

def test_v2_doc_migrates_entry_alias_to_first_class():
    doc = {
        "version": 2,
        "by_field": {
            "platform": [
                {"value": "Linux", "alias": "linux", "added_by": "r",
                 "added_at": "2020-01-01T00:00:00+00:00", "note": "", "example": ""},
            ]
        },
        "free_form_fields": [],
    }
    v = ValueVocabulary.from_dict(doc)
    aliases = v.list_field_aliases("platform")
    assert len(aliases) == 1
    assert aliases[0].alias == "linux" and aliases[0].value == "Linux"
    assert aliases[0].note == "(migrated from learned value alias)"
    # idempotent: re-loading the (now v3) serialized doc adds nothing new
    again = ValueVocabulary.from_dict(v.to_dict())
    assert len(again.list_field_aliases("platform")) == 1


def test_migration_same_alias_different_values_first_wins():
    doc = {
        "version": 2,
        "by_field": {
            "platform": [
                {"value": "PlayStation", "alias": "ps", "added_at": "t1"},
                {"value": "Xbox", "alias": "ps", "added_at": "t2"},   # same raw, diff value
            ]
        },
        "free_form_fields": [],
    }
    v = ValueVocabulary.from_dict(doc)
    aliases = v.list_field_aliases("platform")
    assert len(aliases) == 1                      # exactly one survives
    assert aliases[0].value == "PlayStation"       # first-wins by stored order
    # idempotent on a second load
    again = ValueVocabulary.from_dict(v.to_dict())
    aliases2 = again.list_field_aliases("platform")
    assert len(aliases2) == 1 and aliases2[0].value == "PlayStation"


def test_v1_doc_loads_with_empty_aliases():
    doc = {"version": 1, "by_field": {}, "free_form_fields": []}
    v = ValueVocabulary.from_dict(doc)
    assert v.by_field_aliases == {}
    assert v.list_field_aliases("platform") == []


def test_round_trip_version_3():
    v = ValueVocabulary()
    # add aliases on two fields, platform aliases in a deliberate insertion order
    v.add_alias("platform", "PS5", "PlayStation")
    v.add_alias("platform", "Win", "Windows PC")
    v.add_alias("ESRB", "Mature", "M")
    d = v.to_dict()
    assert d["version"] == 3 == VOCAB_VERSION
    # outer keys sorted by field name
    assert list(d["by_field_aliases"].keys()) == ["ESRB", "platform"]
    # per-field list preserves append/insertion order (never reordered)
    assert [a["alias"] for a in d["by_field_aliases"]["platform"]] == ["PS5", "Win"]
