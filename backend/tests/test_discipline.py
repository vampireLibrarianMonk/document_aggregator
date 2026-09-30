"""Build-discipline inspection alpha loop.

Document inspection is the app's core purpose. For each deliberately injected
placement/format violation, convert the document and confirm the inspector
detects EXACTLY that violation (and that a compliant document is clean on the
inspected dimension). Also confirm the spec loads and evidence flows through the
engine as discipline findings.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import scenario as sc  # noqa: E402
from app.convert import convert_document  # noqa: E402
from app.discipline import learn_discipline, load_discipline  # noqa: E402
from app.discipline.inject import build_docx_with_violation  # noqa: E402
from app.discipline.inspect import inspect as inspect_discipline  # noqa: E402


def _evidence_for(violation):
    data = build_docx_with_violation(violation)
    res = convert_document(data, "t.docx", sc.load_template("1"), sc.load_manifest("1"), "draft")
    return res.first_attempt.get("_evidence", {})


def _findings(violation):
    """Placement rules come from scenario 1's template; text-format rules are
    learned from the document (no hardcoded default)."""
    evidence = _evidence_for(violation)
    discipline = learn_discipline(load_discipline(sc.load_template("1")), evidence)
    return inspect_discipline(evidence, discipline)


def _keys_with_status(findings, status):
    return {f.key for f in findings if f.status == status}


def test_discipline_loads_from_template():
    d = load_discipline(sc.load_template("1"))
    assert d.images.caption_required
    assert d.tables.title_required
    assert "classification" in d.footer.must_contain


def test_missing_caption_detected():
    corrected = _keys_with_status(_findings("missing_caption"), "corrected")
    assert any(k.startswith("image[") and "caption" in k for k in corrected)


def test_missing_table_title_detected():
    corrected = _keys_with_status(_findings("missing_table_title"), "corrected")
    assert any("table[" in k and "title" in k for k in corrected)


def test_missing_page_numbers_detected():
    corrected = _keys_with_status(_findings("missing_page_numbers"), "corrected")
    assert "page_numbers" in corrected


def test_missing_header_detected():
    findings = _findings("missing_header")
    review = {f.key for f in findings if f.status == "needs_review"}
    assert "header" in review


def test_wrong_table_header_style_detected():
    corrected = _keys_with_status(_findings("wrong_table_header_style"), "corrected")
    assert any("header_style" in k for k in corrected)


def test_compliant_document_is_clean_on_core_dimensions():
    """A compliant doc must NOT raise corrected findings for caption/title/page
    numbers/header (classification still needs_review since corpus lacks it)."""
    findings = _findings(None)
    corrected = _keys_with_status(findings, "corrected")
    assert not any(k.startswith("image[") and "caption" in k for k in corrected)
    assert not any("table[" in k and ".title" in k for k in corrected)
    assert "page_numbers" not in corrected
    review = {f.key for f in findings if f.status == "needs_review"}
    assert "header" not in review


@pytest.mark.parametrize("violation", ["missing_caption", "missing_table_title",
                                       "missing_page_numbers", "wrong_table_header_style"])
def test_violation_flows_through_engine_as_discipline_finding(violation):
    from app.reconcile import reconcile

    data = build_docx_with_violation(violation)
    res = convert_document(data, "t.docx", sc.load_template("1"), sc.load_manifest("1"), "draft")
    report = reconcile(res.first_attempt, sc.load_corpus("1"), sc.load_graphics("1"),
                       sc.load_corrections("1"), sc.load_template("1"),
                       sc.load_manifest("1")).model_dump()
    assert report["discipline_findings"], "engine should surface discipline findings"
    assert all(f["defect_class"] == "discipline" for f in report["discipline_findings"])
