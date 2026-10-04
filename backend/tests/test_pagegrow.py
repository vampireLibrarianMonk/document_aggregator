"""Page-growth alpha loop as a test.

Grows a document one page at a time (1..5), distributing a seeded, shuffled
subset of knowledge-base defects per page, and asserts the pipeline stays
correct as complexity increases:
  - no fabrication (resolved values are provenance-backed);
  - figure/table numbering stays contiguous across all pages;
  - unit count scales with pages (the engine actually processes every page);
  - both the in-memory draft AND the rendered multi-page DOCX reconcile equally.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import project as sc  # noqa: E402
from app.knowledge.pagegrow import build_multipage  # noqa: E402
from app.reconcile import reconcile  # noqa: E402

RESOLVED_WITH_VALUE = {"unchanged", "filled", "corrected"}


def _base():
    return dict(
        base_project=sc.load_manifest("1"),
        base_template=sc.load_template("1"),
        base_draft=sc.load_first_attempt("1", "draft"),
        base_corpus=sc.load_corpus("1"),
        base_graphics=sc.load_graphics("1"),
        base_corrections=sc.load_corrections("1"),
    )


def _no_fabrication(report: dict) -> bool:
    for s in report["sections"]:
        for f in s["fields"]:
            if f["status"] in RESOLVED_WITH_VALUE and f["value"] not in (None, ""):
                p = f["provenance"]
                if not (p.get("corpus") or p.get("corrections") or p.get("rule")):
                    return False
    return True


def _contiguous(report: dict) -> bool:
    fig = [g["figure_number"] for s in report["sections"] for g in s["graphics"]
           if g["figure_number"] is not None]
    tbl = [t["table_number"] for s in report["sections"] for t in s["tables"]
           if t["table_number"] is not None]
    return (fig == list(range(1, len(fig) + 1))
            and tbl == list(range(1, len(tbl) + 1)))


@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("pages", [1, 2, 3, 4, 5])
def test_page_growth_stays_correct(pages, seed):
    b = build_multipage(pages=pages, seed=seed, **_base())
    report = reconcile(b["draft"], b["corpus"], b["graphics"], b["corrections"],
                       b["template"], b["project"]).model_dump()
    assert _no_fabrication(report), f"fabrication at pages={pages} seed={seed}"
    assert _contiguous(report), f"numbering gap at pages={pages} seed={seed}"
    # Sections scale with pages (6 base sections per page).
    assert len(report["sections"]) == 6 * pages
    # Graphics scale too (3 per page).
    gfx = sum(len(s["graphics"]) for s in report["sections"])
    assert gfx == 3 * pages


@pytest.mark.parametrize("seed", [1, 7])
def test_unit_count_grows_monotonically_with_pages(seed):
    base = _base()
    prev = 0
    for pages in range(1, 6):
        b = build_multipage(pages=pages, seed=seed, **base)
        report = reconcile(b["draft"], b["corpus"], b["graphics"], b["corrections"],
                           b["template"], b["project"]).model_dump()
        units = report["summary"]["total_units"]
        assert units > prev, f"units did not grow at pages={pages}"
        prev = units


def test_rendered_multipage_docx_matches_inmemory(tmp_path):
    """The rendered multi-page DOCX must convert + reconcile to the same section
    and graphic counts as the in-memory draft (converter handles page breaks)."""
    from app.convert import convert_document
    from build_sample_docs import build_docx_multipage

    b = build_multipage(pages=3, seed=7, **_base())
    docx_path = tmp_path / "mp.docx"
    build_docx_multipage(b["draft"], b["project"], 3, docx_path)

    res = convert_document(docx_path.read_bytes(), "mp.docx", b["template"], b["project"], "draft")
    report = reconcile(res.first_attempt, b["corpus"], b["graphics"], b["corrections"],
                       b["template"], b["project"]).model_dump()
    assert res.fidelity == "high"
    assert len(report["sections"]) == 18
    gfx = sum(len(s["graphics"]) for s in report["sections"])
    assert gfx == 9
    assert _contiguous(report)
    assert _no_fabrication(report)
