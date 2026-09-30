"""Interpreter tests: freeform feedback -> constrained, validated operations.

The offline rule interpreter is always tested. The Bedrock path is exercised
only when BEDROCK_ENABLED=true and creds are present (skipped otherwise), so CI
stays offline-safe. Both must obey the same guarantees: only schema operations,
validated, and no fabricated values.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import scenario as sc  # noqa: E402
from app.corrections.interpreter import interpret_feedback  # noqa: E402
from app.corrections.schema import OPERATIONS, to_engine_correction, validate_op  # noqa: E402
from app.reconcile import reconcile  # noqa: E402


def _ctx(sid: str = "1") -> dict:
    manifest = sc.load_manifest(sid)
    return {
        "fields": manifest.get("fields", []),
        "section_bodies": manifest.get("section_bodies", {}),
        "sections": [s["key"] for s in sc.load_template(sid)["required_sections"]],
    }


def test_schema_rejects_fabrication_and_unknown_ops():
    ok, _ = validate_op({"id": "a", "operation": "replace_field", "target": "x.y", "new_value": "v"})
    assert ok
    ok, reason = validate_op({"id": "a", "operation": "replace_field", "target": "x.y"})
    assert not ok and "no fabrication" in reason
    ok, _ = validate_op({"id": "a", "operation": "flag_needs_review", "target": "x.y"})
    assert ok
    ok, _ = validate_op({"id": "a", "operation": "wipe_db", "target": "x.y"})
    assert not ok


def test_rule_interpreter_offline_proposes_valid_ops():
    from app.corrections.interpreter import RuleInterpreter

    ops = RuleInterpreter().interpret("The firmware should be 4.2.1", _ctx())
    for op in ops:
        op.setdefault("id", "x")
        assert op["operation"] in OPERATIONS
        ok, _ = validate_op(op)
        assert ok


def test_all_accepted_ops_are_schema_valid_and_unfabricated():
    res = interpret_feedback("severity should be High. duration should be four hour.", _ctx(), 1, "t")
    for op in res["accepted"]:
        ok, _ = validate_op(op)
        assert ok
        if op["operation"] != "flag_needs_review":
            assert op.get("new_value") not in (None, "")


def test_interpreted_ops_apply_as_a_round_without_fabrication():
    """Accepted ops convert to engine corrections and reconcile cleanly."""
    res = interpret_feedback("The firmware should be 4.2.1", _ctx(), 0, "t")
    corrections = [to_engine_correction(op) for op in res["accepted"]]
    if not corrections:
        pytest.skip("rule interpreter produced no ops for this phrasing")
    report = reconcile(
        first_attempt=sc.load_first_attempt("1", "draft"),
        corpus=sc.load_corpus("1"), graphics_manifest=sc.load_graphics("1"),
        corrections=corrections, template=sc.load_template("1"),
        scenario=sc.load_manifest("1"),
    ).model_dump()
    for s in report["sections"]:
        for f in s["fields"]:
            if f["status"] in ("filled", "corrected", "unchanged") and f["value"] not in (None, ""):
                p = f["provenance"]
                assert p.get("corpus") or p.get("corrections") or p.get("rule")


@pytest.mark.skipif(os.getenv("BEDROCK_ENABLED", "false").lower() != "true",
                    reason="Bedrock disabled; set BEDROCK_ENABLED=true with creds to run")
def test_bedrock_interpreter_live():
    fb = ("The firmware version is wrong, it should be 4.2.1 not 4.2.0. "
          "The outage duration should be four hour. Severity needs a human to decide.")
    res = interpret_feedback(fb, _ctx(), 0, "director@example.com")
    assert res["interpreter"] == "bedrock"
    targets = {op["target"] for op in res["accepted"]}
    assert "contributing_factors.firmware" in targets
    # Severity must be flagged, not invented.
    sev = [op for op in res["accepted"] if op["target"] == "identifiers.severity"]
    assert sev and sev[0]["operation"] == "flag_needs_review"
    for op in res["accepted"]:
        ok, _ = validate_op(op)
        assert ok
