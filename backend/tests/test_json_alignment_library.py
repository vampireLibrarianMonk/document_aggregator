"""Tests for ProfileLibrary (Part 2): the "many shapes -> one golden" catalog.

Locks the lifecycle the batch coordinator depends on: register a novel shape as
provisional, approve it once so it replays automatically, route incoming docs to
the right entry by signature, bump a version on drift re-learn (resetting to
provisional so a changed shape must be re-approved), and round-trip the whole
library through JSON unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.mapping import infer_mapping
from app.json_alignment.profile_store import (
    STATE_APPROVED,
    STATE_PROVISIONAL,
    STATE_RETIRED,
    ProfileLibrary,
    build_profile,
    load_library,
    save_library,
)
from app.json_alignment.signature import best_match, signature_of
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target
from app.json_alignment.variation import generate_variations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _vars() -> dict:
    golden = json.loads((VAR_DIR / "golden_records.json").read_text())
    return {v.name: v for v in generate_variations(golden, _schema(), seed=42)}


def _register(lib: ProfileLibrary, name: str, records: list[dict],
              state: str = STATE_PROVISIONAL):
    schema = _schema()
    src = profile_source(records)
    tgt = extract_target(schema)
    prof = build_profile(name, src, tgt, infer_mapping(src, tgt))
    return lib.register(name, signature_of(records), prof, state=state)


def test_register_defaults_to_provisional():
    lib = ProfileLibrary()
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records)
    assert lib.provisional_ids() == ["level_01"]
    assert lib.approved_ids() == []
    assert lib.get("level_01").state == STATE_PROVISIONAL


def test_approve_makes_it_replayable():
    lib = ProfileLibrary()
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records)
    lib.approve("level_01")
    assert lib.approved_ids() == ["level_01"]
    assert lib.provisional_ids() == []
    assert lib.get("level_01").is_approved


def test_route_known_shape_approved_only():
    lib = ProfileLibrary()
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records)
    _register(lib, "level_03", vars["level_03"].records)
    lib.approve("level_01")
    # only approved shapes participate in approved-only routing
    pid, match = best_match(signature_of(vars["level_01"].records),
                            lib.signatures(approved_only=True))
    assert pid == "level_01" and match.band == "high"
    # level_03 is provisional -> not offered for approved-only routing
    assert "level_03" not in lib.signatures(approved_only=True)


def test_reregister_bumps_version_and_resets_to_provisional():
    lib = ProfileLibrary()
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records)
    lib.approve("level_01")
    assert lib.get("level_01").version == 1
    # drift re-learn: re-register the same id
    entry = _register(lib, "level_01", vars["level_01"].records)
    assert entry.version == 2
    assert entry.state == STATE_PROVISIONAL  # a changed shape must be re-approved


def test_retire_excludes_from_routing():
    lib = ProfileLibrary()
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records, state=STATE_APPROVED)
    lib.retire("level_01")
    assert lib.get("level_01").state == STATE_RETIRED
    assert "level_01" not in lib.signatures()
    assert "level_01" not in lib.signatures(approved_only=True)


def test_library_roundtrips_through_json(tmp_path):
    lib = ProfileLibrary(target_title="game")
    vars = _vars()
    _register(lib, "level_01", vars["level_01"].records)
    _register(lib, "level_03", vars["level_03"].records)
    lib.approve("level_01")
    p = save_library(lib, tmp_path / "library.json")
    back = load_library(p)
    assert back.to_dict() == lib.to_dict()
    assert back.target_title == "game"
    assert back.get("level_01").is_approved
    # the replayed profile still reproduces the same mapping
    assert (back.get("level_01").profile.mapping.mapped()
            == lib.get("level_01").profile.mapping.mapped())
