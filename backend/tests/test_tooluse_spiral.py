"""Tool-use spiral review — offline structure tests + on-demand live wrapper.

The parsing rungs (rung1/rung2) are pure functions of a Converse response dict,
so they are fully testable offline with synthetic responses — no Bedrock. The
live ladder is gated behind BEDROCK_ENABLED like the other model tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from tests.command_center.tooluse_spiral import (  # noqa: E402
    rung1_from_response,
    rung2_from_response,
)

_LIVE = os.getenv("BEDROCK_ENABLED", "false").lower() == "true"


def _resp(tool_name, operations):
    return {"output": {"message": {"content": [
        {"toolUse": {"name": tool_name, "input": {"operations": operations}}},
    ]}}}


_GOOD_OP = {"operation": "replace_field",
            "target": "contributing_factors.firmware",
            "new_value": "4.2.1", "reason": "stated"}


# ---- Rung 1: forced tool-use returns a usable toolUse block ----------------

def test_rung1_clean_name_passes():
    r = rung1_from_response(_resp("propose_corrections", [_GOOD_OP]))
    assert r.passed and r.detail["decorated"] is False


def test_rung1_harmony_decorated_name_still_passes():
    r = rung1_from_response(_resp("propose_corrections<|channel|>commentary", [_GOOD_OP]))
    assert r.passed and r.detail["decorated"] is True


def test_rung1_forced_choice_error_is_captured_with_fix_hint():
    r = rung1_from_response({"_error": "ValidationException: toolChoice not supported"})
    assert not r.passed
    assert "toolChoice" in r.fix_hint or "auto" in r.fix_hint


def test_rung1_prose_only_response_fails():
    resp = {"output": {"message": {"content": [{"text": "Sure, here is my answer"}]}}}
    r = rung1_from_response(resp)
    assert not r.passed and "no toolUse" in r.failure_reason


# ---- Rung 2: tool input parses + validates + uncorrupted -------------------

def test_rung2_clean_ops_pass():
    r = rung2_from_response(_resp("propose_corrections", [_GOOD_OP]))
    assert r.passed and r.detail["valid_ops"] == 1


def test_rung2_detects_parameter_argument_corruption():
    # The sibling-project Nemotron failure: framing leaked into a value.
    bad = {**_GOOD_OP, "new_value": "4.2.1<parameter=query>firmware"}
    r = rung2_from_response(_resp("propose_corrections", [bad]))
    assert not r.passed
    assert "corruption" in r.failure_reason
    assert "sanitize" in r.fix_hint


def test_rung2_detects_channel_token_corruption():
    bad = {**_GOOD_OP, "reason": "ok<|channel|>leak"}
    r = rung2_from_response(_resp("propose_corrections", [bad]))
    assert not r.passed and "corruption" in r.failure_reason


def test_rung2_string_payload_non_json_fails_cleanly():
    resp = {"output": {"message": {"content": [
        {"toolUse": {"name": "propose_corrections", "input": "not json at all"}},
    ]}}}
    r = rung2_from_response(resp)
    assert not r.passed and "not JSON" in r.failure_reason


# ---- Live ladder (on-demand) -----------------------------------------------

@pytest.mark.skipif(
    not _LIVE,
    reason="Bedrock disabled; set BEDROCK_ENABLED=true to run the live tool-use spiral",
)
def test_spiral_report_well_formed_rung2():
    from tests.command_center.tooluse_spiral import spiral

    report = spiral(["gpt-oss-120b"], max_rung=2)
    assert set(report) >= {"max_rung", "ladder", "models"}
    assert report["models"], "no models resolved"
    for m in report["models"]:
        assert "model_id" in m and "rungs" in m
        for rr in m["rungs"]:
            assert set(rr) >= {"rung", "ran", "passed", "failure_reason", "fix_hint"}
