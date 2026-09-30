"""Vector-layout (geometry) inspection tests against committed PDF fixtures.

Geometry, not pixels: each element is a box; discipline rules are box
relationships. Fixtures are rendered PDFs with KNOWN placement so we can assert
the geometric inspector detects exactly the injected misplacement. Runs offline
(no LibreOffice needed for these fixtures).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.discipline.spec import LayoutRule  # noqa: E402
from app.layout.geometry import extract_layout  # noqa: E402
from app.layout.inspect_layout import inspect_layout  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"

RULE = LayoutRule(caption_position="below", caption_align_tol=0.15,
                  table_title_position="above", footer_band=0.12,
                  page_number_region="footer-center", header_band=0.12)


def _inspect(name: str):
    pages = extract_layout((FIX / name).read_bytes())
    return pages, inspect_layout(pages, RULE)


def _status(findings, needle: str):
    return [f.status for f in findings if needle in f.key]


def test_extractor_finds_element_boxes():
    pages, _ = _inspect("compliant.pdf")
    roles = {b.role for b in pages[0].boxes}
    assert "figure" in roles and "table" in roles
    assert "caption" in roles and "table_title" in roles and "page_number" in roles


def test_compliant_layout_is_clean():
    _, findings = _inspect("compliant.pdf")
    assert all(s == "unchanged" for s in _status(findings, "caption_geometry"))
    assert all(s == "unchanged" for s in _status(findings, "title_geometry"))
    assert all(s == "unchanged" for s in _status(findings, "page_number_geometry"))


def test_caption_above_detected():
    _, findings = _inspect("caption_above.pdf")
    assert "corrected" in _status(findings, "caption_geometry")


def test_table_title_below_detected():
    _, findings = _inspect("title_below.pdf")
    assert "corrected" in _status(findings, "title_geometry")


def test_page_number_wrong_region_detected():
    _, findings = _inspect("pagenum_header.pdf")
    assert "corrected" in _status(findings, "page_number_geometry")


def test_undefined_geometric_rule_called_out():
    """An undefined caption geometry rule produces a needs_review call-out."""
    pages = extract_layout((FIX / "compliant.pdf").read_bytes())
    undefined_rule = LayoutRule()  # nothing set
    findings = inspect_layout(pages, undefined_rule)
    assert any(f.status == "needs_review" and "caption_position" in f.key for f in findings)
