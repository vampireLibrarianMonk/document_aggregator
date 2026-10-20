"""Regression guard: array-source -> scalar-target is an ARITY MISMATCH that
must surface as needs_review, never a silent confident map.

Bug it locks: a source field like `platform[]` (array) mapping to a scalar
golden `platform` (string) used to ground as an `exact` match, because the
source profile reports the element type (string) and drops the `[]` marker. At
execution the list->string coercion is correctly refused and the value dropped,
but the card showed a clean "mapped" and the record still counted conformant —
so the shape was silently approvable with `platform` vanishing. The fix
(_arity_mismatch gate in _score_candidate) makes such a pair abstain so it
surfaces as needs_review, forcing a human decision.

The complementary cases must KEEP working:
  - array source -> array target   (genres[] -> genres)      : maps
  - scalar source -> array target  (genre -> genres, split)  : maps
  - scalar source -> scalar target (platform -> platform)    : maps
"""
from __future__ import annotations

import json
from pathlib import Path

from app.json_alignment.mapping import infer_mapping
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2]
     / "sample_docs" / "project" / "7" / "golden" / "target_schema.json").read_text())


def _infer(record: dict):
    src = profile_source([record])
    return infer_mapping(src, extract_target(SCHEMA))


def test_array_source_into_scalar_target_is_needs_review():
    """platform[] (array) -> platform (string) must NOT ground; it is an arity
    mismatch the executor would refuse, so it surfaces as needs_review."""
    rec = {"name": "Portal", "releaseYear": "2007", "developer": "Valve",
           "publisher": "Valve", "platform": ["Windows PC"], "genres": ["Puzzle"],
           "criticScore": 90, "ESRB": "T"}
    m = _infer(rec)
    assert "platform" in m.needs_review(), (
        "array->scalar platform must be needs_review, not a silent map")
    assert "platform" not in m.mapped(), "platform must not ground to platform[]"


def test_array_source_into_array_target_still_maps():
    """genres[] (array) -> genres (array) is a correct arity match and must
    keep grounding (the fix must not over-reach)."""
    rec = {"name": "Portal", "releaseYear": "2007", "developer": "Valve",
           "publisher": "Valve", "platform": "Windows PC", "genres": ["Puzzle", "Action"],
           "criticScore": 90, "ESRB": "T"}
    m = _infer(rec)
    assert m.mapped().get("genres") == "genres[]", (
        "array->array genres must still map cleanly")


def test_scalar_sources_still_map():
    """A fully-scalar record (platform a plain string) maps every field — the
    arity gate only fires for an array source into a scalar target."""
    rec = {"name": "Portal", "releaseYear": "2007", "developer": "Valve",
           "publisher": "Valve", "platform": "Windows PC", "genres": ["Puzzle"],
           "criticScore": 90, "ESRB": "T"}
    m = _infer(rec)
    assert m.mapped().get("platform") == "platform", (
        "scalar platform -> scalar platform must map")
    assert "platform" not in m.needs_review()
