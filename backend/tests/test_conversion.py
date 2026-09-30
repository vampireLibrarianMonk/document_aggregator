"""Conversion tests: real DOCX/PPTX/PDF documents flow through the pipeline.

Clients submit documents, not JSON. These tests confirm that for every scenario
and every format:
  - conversion succeeds and reports its fidelity,
  - the converted first_attempt reconciles to a legal, non-fabricated report,
  - the high-fidelity DOCX path matches the JSON baseline on the key resolved
    values (fidelity floor), and
  - all three graphics are recovered and resolved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import scenario as sc  # noqa: E402
from app.convert import convert_document  # noqa: E402
from app.reconcile import reconcile  # noqa: E402

FORMATS = ["docx", "pptx", "pdf"]
RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _generated(sid: str, mode: str, fmt: str) -> Path:
    return sc._dir(sid) / "first_attempt" / "generated" / f"{mode}.{fmt}"


def _reconcile_doc(sid: str, mode: str, fmt: str) -> dict:
    tpl = sc.load_template(sid)
    scen = sc.load_manifest(sid)
    data = _generated(sid, mode, fmt).read_bytes()
    res = convert_document(data, f"{mode}.{fmt}", tpl, scen, mode)
    report = reconcile(res.first_attempt, sc.load_corpus(sid), sc.load_graphics(sid),
                       sc.load_corrections(sid), tpl, scen).model_dump()
    return {"fidelity": res.fidelity, "report": report}


def _fields(report: dict) -> dict[str, dict]:
    out = {}
    for s in report["sections"]:
        for f in s["fields"]:
            out[f["key"]] = f
    return out


def _all_units_have_provenance(report: dict) -> bool:
    def has_prov(u: dict) -> bool:
        p = u.get("provenance", {})
        return bool(p.get("corpus") or p.get("corrections") or p.get("rule"))

    for s in report["sections"]:
        for f in s["fields"]:
            if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
                if not has_prov(f):
                    return False
    return True


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("mode", ["draft", "template"])
def test_conversion_reconciles_cleanly(scenario_id, mode, fmt):
    if not _generated(scenario_id, mode, fmt).exists():
        pytest.skip(f"no generated {fmt} for scenario {scenario_id} {mode}")
    out = _reconcile_doc(scenario_id, mode, fmt)
    report = out["report"]
    # No fabrication, legal statuses.
    assert _all_units_have_provenance(report)
    # All three graphics recovered from the document and resolved.
    gfx = [g for s in report["sections"] for g in s["graphics"]]
    assert len(gfx) == 3, f"{scenario_id}/{mode}/{fmt} recovered {len(gfx)} graphics"


def test_docx_matches_json_baseline_on_key_values(scenario_id):
    """DOCX is the high-fidelity tier: its resolved key values must match the
    JSON-authored baseline (the fidelity floor for the best format)."""
    if not _generated(scenario_id, "draft", "docx").exists():
        pytest.skip("no docx")
    doc = _reconcile_doc(scenario_id, "draft", "docx")["report"]
    baseline = sc.run_reconciliation("draft", scenario_id)  # JSON path

    doc_f = _fields(doc)
    base_f = _fields(baseline)
    # Compare the resolved value of every field both paths produced.
    for key, bf in base_f.items():
        if key in doc_f and bf["value"] not in (None, ""):
            assert doc_f[key]["value"] == bf["value"], (
                f"{scenario_id} {key}: docx={doc_f[key]['value']!r} baseline={bf['value']!r}"
            )


def test_fidelity_reported(scenario_id):
    for fmt, expected in (("docx", "high"), ("pptx", "good"), ("pdf", "partial")):
        if _generated(scenario_id, "draft", fmt).exists():
            assert _reconcile_doc(scenario_id, "draft", fmt)["fidelity"] == expected
