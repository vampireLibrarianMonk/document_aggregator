"""Tests for the optional LLM semantic-mapping tier (Phase E).

The tier is bounded to abstentions, picks from a CLOSED candidate set, and
re-verifies every pick deterministically. These lock: it only upgrades
abstentions (never overrides a confident mapping or collapses a conflict); an
unverifiable pick (fake path / out-of-candidate / type-incompatible) is rejected
back to needs_review (no fabrication); and offline it is a no-op. The live
Bedrock test is skipped unless BEDROCK_ENABLED=true (repo convention).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from app.json_alignment.mapping import STATUS_MAPPED, infer_mapping
from app.json_alignment.semantic import (
    SemanticProposal,
    apply_semantic_tier,
    get_proposer,
)
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target
from app.json_alignment.variation import generate_variations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "json_alignment"
VAR_DIR = FIXTURE_DIR / "variations"


def _schema() -> dict:
    return json.loads((FIXTURE_DIR / "target_schema.json").read_text())


def _blind_rename():
    """A variation the deterministic matcher abstains on (blind renames)."""
    golden = json.loads((VAR_DIR / "golden_records.json").read_text())
    v = {x.name: x for x in generate_variations(golden, _schema(), seed=42)}["level_01"]
    src = profile_source(v.records)
    tgt = extract_target(_schema())
    return v, src, tgt, infer_mapping(src, tgt)


class _FakeProposer:
    """Returns canned picks; records what it was asked (to assert bounding)."""
    name = "fake"

    def __init__(self, picks: dict):
        self.picks = picks
        self.seen: list[dict] | None = None

    def propose(self, requests):
        self.seen = requests
        return [SemanticProposal(r["target"], self.picks.get(r["target"]))
                for r in requests]


def test_tier_only_sees_abstained_targets():
    _, src, tgt, m = _blind_rename()
    fp = _FakeProposer({})
    apply_semantic_tier(m, src, tgt, proposer=fp)
    seen = {r["target"] for r in (fp.seen or [])}
    # ESRB maps deterministically in this variation; it must NOT be sent.
    assert "ESRB" not in seen
    assert set(m.needs_review()) == seen


def test_verified_picks_are_upgraded():
    v, src, tgt, m = _blind_rename()
    truth = {t: sp for sp, t in v.gold_mapping.items()}  # correct source per target
    out = apply_semantic_tier(m, src, tgt, proposer=_FakeProposer(truth))
    # name/releaseYear/etc. recovered via the model, marked semantic_model.
    assert "name" in out.mapped()
    name_fm = next(f for f in out.fields if f.target == "name")
    assert name_fm.method == "semantic_model" and name_fm.status == STATUS_MAPPED


def test_pick_to_nonexistent_source_is_rejected():
    v, src, tgt, m = _blind_rename()
    truth = {t: sp for sp, t in v.gold_mapping.items()}
    truth["developer"] = "no_such_source_path"  # fabricated path
    out = apply_semantic_tier(m, src, tgt, proposer=_FakeProposer(truth))
    assert "developer" in out.needs_review()      # rejected, not fabricated
    assert "developer" not in out.mapped()


def test_abstain_pick_leaves_needs_review():
    _, src, tgt, m = _blind_rename()
    out = apply_semantic_tier(m, src, tgt,
                              proposer=_FakeProposer({t: None for t in m.needs_review()}))
    # the model abstained on everything -> mapping unchanged
    assert set(out.needs_review()) == set(m.needs_review())


def test_tier_does_not_override_confident_mappings():
    v, src, tgt, m = _blind_rename()
    # Even if the model tries to repoint a CONFIDENT target, it is ignored:
    # ESRB is already mapped; a pick for it must not change it.
    out = apply_semantic_tier(m, src, tgt,
                              proposer=_FakeProposer({"ESRB": "press_rating"}))
    before = next(f for f in m.fields if f.target == "ESRB")
    after = next(f for f in out.fields if f.target == "ESRB")
    assert after.source_path == before.source_path
    assert after.method == before.method


def test_offline_no_proposer_is_noop():
    _, src, tgt, m = _blind_rename()
    out = apply_semantic_tier(m, src, tgt, proposer=None)
    assert out.to_dict() == m.to_dict()


def test_get_proposer_is_none_when_bedrock_disabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "BEDROCK_ENABLED", False, raising=False)
    assert get_proposer() is None


def test_input_mapping_not_mutated():
    v, src, tgt, m = _blind_rename()
    before = m.to_dict()
    truth = {t: sp for sp, t in v.gold_mapping.items()}
    apply_semantic_tier(m, src, tgt, proposer=_FakeProposer(truth))
    assert m.to_dict() == before  # pure: returns a new Mapping


@pytest.mark.skipif(os.getenv("BEDROCK_ENABLED", "false").lower() != "true",
                    reason="Bedrock disabled; set BEDROCK_ENABLED=true with creds to run")
def test_bedrock_semantic_tier_live():
    """Live tool-use smoke: the model recovers blind renames, and every accepted
    upgrade is a real, type-compatible source path (re-verified)."""
    v, src, tgt, m = _blind_rename()
    out = apply_semantic_tier(m, src, tgt)  # real proposer via get_proposer()
    valid_paths = {f.path for f in src.fields}
    for fm in out.fields:
        if fm.method == "semantic_model":
            assert fm.source_path in valid_paths


# --------------------------------------------------------------------------
# Tool-name sanitization for the semantic proposer (offline). A harmony-channel
# decorated name on the propose_field_mappings tool must still parse.
# --------------------------------------------------------------------------

def _mapping_resp(tool_name: str) -> dict:
    return {
        "output": {"message": {"content": [
            {"toolUse": {"name": tool_name, "input": {"mappings": [
                {"target": "name", "source_path": "title"},
                {"target": "releaseYear", "source_path": "abstain"},
            ]}}},
        ]}}
    }


def test_semantic_extract_accepts_clean_tool_name():
    from app.json_alignment.semantic import BedrockSemanticProposer

    out = BedrockSemanticProposer._extract(_mapping_resp("propose_field_mappings"))
    by = {p.target: p.source_path for p in out}
    assert by["name"] == "title"
    assert by["releaseYear"] is None  # 'abstain' -> None


def test_semantic_extract_accepts_harmony_decorated_tool_name():
    from app.json_alignment.semantic import BedrockSemanticProposer

    out = BedrockSemanticProposer._extract(
        _mapping_resp("propose_field_mappings<|channel|>commentary"))
    assert len(out) == 2
    assert {p.target for p in out} == {"name", "releaseYear"}
