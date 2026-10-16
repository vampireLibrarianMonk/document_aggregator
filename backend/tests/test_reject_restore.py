"""Unit tests for the REJECT lifecycle on ProfileLibrary + its pure helpers.

Covers: validate_rejection rules, compose_rejection_text canonical strings,
reject/restore state transitions with prev_state round-trip, rejected_ids /
rejected_signatures, signatures() hardening, and to_dict/from_dict persistence
of the rejection meta (including a v1 library that predates it).
"""
from __future__ import annotations

import pytest
from app.json_alignment.mapping import Mapping
from app.json_alignment.profile_store import (
    STATE_APPROVED,
    STATE_PROVISIONAL,
    STATE_REJECTED,
    ConversionProfile,
    ProfileLibrary,
    compose_rejection_text,
    validate_rejection,
)
from app.json_alignment.signature import Signature


def _sig(h: str) -> Signature:
    return Signature(paths=(f"path_{h}",), types={f"path_{h}": "string"}, hash=h)


def _profile(name: str = "p") -> ConversionProfile:
    return ConversionProfile(name=name, target_title="game",
                             mapping=Mapping(fields=[]))


def _lib_with(state: str = STATE_PROVISIONAL) -> ProfileLibrary:
    lib = ProfileLibrary(target_title="game")
    lib.register("game__shape_abc", _sig("abc"), _profile(), state=state)
    return lib


# -- validate_rejection ------------------------------------------------------

KNOWN = {"name", "platform", "genres", "ESRB"}


@pytest.mark.parametrize("category,ok", [
    ("not_our_data", True),
    ("duplicate_shape", True),
    ("bad_source_data", True),
    ("mystery", False),
])
def test_validate_simple_categories(category, ok):
    got, _ = validate_rejection(category, None, "", known_fields=KNOWN)
    assert got is ok


def test_validate_wrong_mapping_requires_a_field():
    ok, err = validate_rejection("wrong_mapping", [], "", known_fields=KNOWN)
    assert not ok and "at least one golden field" in err


def test_validate_wrong_mapping_rejects_unknown_field():
    ok, err = validate_rejection("wrong_mapping", ["bogus"], "", known_fields=KNOWN)
    assert not ok and "not a golden field" in err


def test_validate_wrong_mapping_accepts_real_fields():
    ok, _ = validate_rejection("wrong_mapping", ["platform", "genres"], "",
                               known_fields=KNOWN)
    assert ok


@pytest.mark.parametrize("note,ok", [
    ("", False),
    ("   ", False),
    ("no", False),
    ("bad", False),
    ("nononono", False),       # 8 non-space -> reject
    ("nononono!", False),      # 9 non-space -> reject
    ("this is fine", True),    # 10 non-space -> accept
    ("  this is fine  ", True),  # whitespace collapsed -> 10 -> accept
])
def test_validate_other_note_length_bar(note, ok):
    got, _ = validate_rejection("other", None, note, known_fields=KNOWN)
    assert got is ok


# -- compose_rejection_text --------------------------------------------------

def test_compose_canonical_strings():
    assert compose_rejection_text("not_our_data", None, "") == "Rejected — not our data."
    assert compose_rejection_text("duplicate_shape", None, "") == \
        "Rejected — duplicate of an existing shape."
    assert compose_rejection_text("bad_source_data", None, "") == \
        "Rejected — bad source data."
    assert compose_rejection_text("wrong_mapping", ["platform", "genres"], "") == \
        "Rejected — wrong field mapping (platform, genres)."
    assert compose_rejection_text("other", None, "mislabeled export") == \
        "Rejected — mislabeled export."


def test_compose_appends_note_for_non_other():
    txt = compose_rejection_text("not_our_data", None, "weather feed")
    assert txt == "Rejected — not our data. Note: weather feed"


# -- reject / restore --------------------------------------------------------

def test_reject_sets_state_and_meta():
    lib = _lib_with(STATE_PROVISIONAL)
    entry = lib.reject("game__shape_abc", category="not_our_data", fields=None,
                       note="", composed_text="Rejected — not our data.",
                       rejected_by="reviewer")
    assert entry.state == STATE_REJECTED
    rec = entry.meta["rejection"]
    assert rec["category"] == "not_our_data"
    assert rec["prev_state"] == STATE_PROVISIONAL
    assert rec["rejected_by"] == "reviewer"
    assert "fields" not in rec and "note" not in rec  # empties omitted
    assert rec["rejected_at"]


def test_reject_stores_fields_and_note_when_present():
    lib = _lib_with()
    entry = lib.reject("game__shape_abc", category="wrong_mapping",
                       fields=["platform"], note="see ticket",
                       composed_text="x")
    rec = entry.meta["rejection"]
    assert rec["fields"] == ["platform"]
    assert rec["note"] == "see ticket"


def test_restore_round_trips_provisional():
    lib = _lib_with(STATE_PROVISIONAL)
    lib.reject("game__shape_abc", category="other", fields=None,
               note="x" * 10, composed_text="x")
    entry = lib.restore("game__shape_abc")
    assert entry.state == STATE_PROVISIONAL
    assert "rejection" not in entry.meta


def test_restore_round_trips_approved():
    lib = _lib_with(STATE_APPROVED)
    lib.reject("game__shape_abc", category="not_our_data", fields=None,
               note="", composed_text="x")
    entry = lib.restore("game__shape_abc")
    assert entry.state == STATE_APPROVED


def test_restore_falls_back_to_provisional_without_prev_state():
    lib = _lib_with(STATE_APPROVED)
    entry = lib.get("game__shape_abc")
    # Simulate an old reason record written before prev_state existed.
    entry.state = STATE_REJECTED
    entry.meta["rejection"] = {"category": "other", "composed_text": "x"}
    restored = lib.restore("game__shape_abc")
    assert restored.state == STATE_PROVISIONAL


def test_reject_unknown_id_raises_keyerror():
    lib = _lib_with()
    with pytest.raises(KeyError):
        lib.reject("nope", category="not_our_data", fields=None, note="",
                   composed_text="x")


def test_restore_non_rejected_raises_valueerror():
    lib = _lib_with(STATE_PROVISIONAL)
    with pytest.raises(ValueError):
        lib.restore("game__shape_abc")


# -- id/signature listings + signatures() hardening -------------------------

def test_rejected_ids_and_signatures():
    lib = _lib_with()
    lib.reject("game__shape_abc", category="not_our_data", fields=None,
               note="", composed_text="x")
    assert lib.rejected_ids() == ["game__shape_abc"]
    assert "game__shape_abc" in lib.rejected_signatures()


def test_signatures_default_excludes_rejected():
    lib = _lib_with(STATE_PROVISIONAL)
    lib.register("game__shape_xyz", _sig("xyz"), _profile("q"),
                 state=STATE_APPROVED)
    lib.reject("game__shape_abc", category="not_our_data", fields=None,
               note="", composed_text="x")
    default_sigs = lib.signatures()
    assert "game__shape_abc" not in default_sigs
    assert "game__shape_xyz" in default_sigs
    assert lib.signatures(approved_only=True) == {"game__shape_xyz": _sig("xyz")}
    assert set(lib.rejected_signatures()) == {"game__shape_abc"}


# -- persistence -------------------------------------------------------------

def test_to_from_dict_preserves_rejection_meta():
    lib = _lib_with(STATE_APPROVED)
    lib.reject("game__shape_abc", category="wrong_mapping", fields=["platform"],
               note="n", composed_text="x")
    round_tripped = ProfileLibrary.from_dict(lib.to_dict())
    entry = round_tripped.get("game__shape_abc")
    assert entry.state == STATE_REJECTED
    assert entry.meta["rejection"]["prev_state"] == STATE_APPROVED
    assert entry.meta["rejection"]["fields"] == ["platform"]


def test_v1_library_without_rejection_loads_unchanged():
    lib = _lib_with(STATE_PROVISIONAL)
    data = lib.to_dict()
    # No entry carries a rejection meta; load must succeed and keep state.
    loaded = ProfileLibrary.from_dict(data)
    e = loaded.get("game__shape_abc")
    assert e.state == STATE_PROVISIONAL
    assert "rejection" not in e.meta
