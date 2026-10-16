"""Free-form marker + items-aware fold tests for merge_vocab_into_schema.

Free-form WINS: a marked field is stripped of every constraint extract_target
reads (scalar top-level enum/taxonomy AND array items.enum) so the value gate
vetoes nothing. A learned value folds into the SAME slot extract_target reads
for the field (items.enum for a constrained array, else top-level taxonomy), so
adding an accepted value actually widens the gate.
"""
from __future__ import annotations

import copy

from app.json_alignment.target_profile import extract_target
from app.json_alignment.value_vocab import (
    ValueVocabulary,
    merge_vocab_into_schema,
)


def _scalar_schema() -> dict:
    return {
        "title": "t", "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "x-pydi-taxonomy": ["Windows PC", "Xbox"],
                "x-pydi-taxonomy-aliases": {"PC": "Windows PC"},
            },
            "note": {"type": "string"},  # free-text scalar, no constraint
        },
    }


def _array_enum_schema() -> dict:
    return {
        "title": "t", "type": "object",
        "properties": {
            "genres": {
                "type": "array",
                "items": {"type": "string", "enum": ["Action", "RPG"]},
            },
        },
    }


def _target_field(schema: dict, name: str):
    return {f.name: f for f in extract_target(schema).fields}[name]


# -- free-form strips constraints -------------------------------------------

def test_free_form_scalar_strips_taxonomy_and_adds_marker():
    schema = _scalar_schema()
    vocab = ValueVocabulary(free_form_fields={"platform"})
    merged = merge_vocab_into_schema(schema, vocab)
    node = merged["properties"]["platform"]
    assert "x-pydi-taxonomy" not in node
    assert "x-pydi-taxonomy-aliases" not in node
    assert node["x-pydi-free-form"] is True
    # effective field is unconstrained (free-text)
    assert _target_field(merged, "platform").enum == ()


def test_free_form_array_strips_items_enum():
    schema = _array_enum_schema()
    vocab = ValueVocabulary(free_form_fields={"genres"})
    merged = merge_vocab_into_schema(schema, vocab)
    assert "enum" not in merged["properties"]["genres"]["items"]
    # extract_target now reports genres as unconstrained
    assert _target_field(merged, "genres").enum == ()


def test_free_form_overrides_learned_values_but_retains_them():
    schema = _scalar_schema()
    vocab = ValueVocabulary(free_form_fields={"platform"})
    vocab.add("platform", "Linux")
    merged = merge_vocab_into_schema(schema, vocab)
    # Linux is NOT folded while free-form is on (field unconstrained)
    assert "x-pydi-taxonomy" not in merged["properties"]["platform"]
    # but the learned value is retained in the vocab, re-folds when off
    vocab.set_free_form("platform", False)
    merged2 = merge_vocab_into_schema(schema, vocab)
    assert "Linux" in merged2["properties"]["platform"]["x-pydi-taxonomy"]
    assert _target_field(merged2, "platform").enum  # constrained again


# -- items-aware fold --------------------------------------------------------

def test_fold_into_constrained_array_lands_in_items_enum():
    schema = _array_enum_schema()
    vocab = ValueVocabulary()
    vocab.add("genres", "Platformer")
    merged = merge_vocab_into_schema(schema, vocab)
    assert "Platformer" in merged["properties"]["genres"]["items"]["enum"]
    # the gate (via extract_target) now admits it
    assert "Platformer" in _target_field(merged, "genres").enum


def test_fold_into_scalar_lands_in_top_level_taxonomy():
    schema = _scalar_schema()
    vocab = ValueVocabulary()
    vocab.add("platform", "Linux")
    merged = merge_vocab_into_schema(schema, vocab)
    assert "Linux" in merged["properties"]["platform"]["x-pydi-taxonomy"]
    assert "Linux" in _target_field(merged, "platform").enum


def test_fold_into_array_with_only_items_taxonomy_lands_top_level():
    # An array whose only "constraint" is the never-read items.x-pydi-taxonomy:
    # the fold must land in the TOP-LEVEL taxonomy where extract_target falls
    # through for arrays with no enum/items.enum.
    schema = {
        "title": "t", "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string", "x-pydi-taxonomy": ["a", "b"]},
            },
        },
    }
    vocab = ValueVocabulary()
    vocab.add("tags", "c")
    merged = merge_vocab_into_schema(schema, vocab)
    assert "c" in merged["properties"]["tags"]["x-pydi-taxonomy"]
    assert "c" in _target_field(merged, "tags").enum


def test_non_free_form_fold_matches_prior_behavior_with_alias():
    schema = _scalar_schema()
    vocab = ValueVocabulary()
    vocab.add("platform", "Linux", alias="linux")
    merged = merge_vocab_into_schema(schema, vocab)
    node = merged["properties"]["platform"]
    assert node["x-pydi-taxonomy"] == ["Windows PC", "Xbox", "Linux"]
    assert node["x-pydi-taxonomy-aliases"]["linux"] == "Linux"
    assert node["x-pydi-taxonomy-aliases"]["PC"] == "Windows PC"  # schema alias kept


# -- purity / persistence / backward compat ----------------------------------

def test_merge_is_pure():
    schema = _scalar_schema()
    before = copy.deepcopy(schema)
    vocab = ValueVocabulary(free_form_fields={"platform"})
    vocab.add("note", "freeform-value")
    merge_vocab_into_schema(schema, vocab)
    assert schema == before  # input unmutated


def test_empty_vocab_merge_is_identical():
    schema = _scalar_schema()
    merged = merge_vocab_into_schema(schema, ValueVocabulary())
    assert merged is schema  # fast-path returns the same object


def test_round_trip_free_form_fields():
    vocab = ValueVocabulary(free_form_fields={"platform", "note"})
    vocab.add("platform", "Linux")
    data = vocab.to_dict()
    assert data["free_form_fields"] == ["note", "platform"]
    loaded = ValueVocabulary.from_dict(data)
    assert loaded.free_form_fields == {"platform", "note"}
    assert loaded.is_free_form("platform")


def test_v1_doc_loads_with_empty_free_form():
    v1 = {"version": 1, "by_field": {"platform": [{"value": "Linux"}]}}
    loaded = ValueVocabulary.from_dict(v1)
    assert loaded.free_form_fields == set()
    assert loaded.list_field("platform")[0].value == "Linux"
