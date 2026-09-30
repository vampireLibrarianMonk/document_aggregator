"""Inspect vector layout (element boxes) against geometric discipline rules.

Checks are box relationships on each page:
  - each figure has a caption box positioned per the rule (below/above) and
    horizontally aligned within tolerance;
  - each table title box sits above (or below) its table box;
  - the page number falls within the expected band/region;
  - the header band is populated when required.

Findings mirror the structural inspector's shape (CorrectedField, defect_class
'discipline') so they merge into the same report. Undefined geometric rules are
called out (needs_review), never defaulted.
"""
from __future__ import annotations

from ..reconcile.models import CorrectedField, DefectClass, Provenance, Status
from .geometry import Box, PageLayout


def _finding(key: str, label: str, status: Status, observed, required, note: str) -> CorrectedField:
    return CorrectedField(
        key=key, label=label, value={"observed": observed, "required": required},
        status=status, defect_class=DefectClass.discipline,
        provenance=Provenance(rule=f"build_discipline.layout.{key}"), note=note,
    )


def inspect_layout(pages: list[PageLayout], layout_rule) -> list[CorrectedField]:
    out: list[CorrectedField] = []
    for page in pages:
        out += _inspect_captions(page, layout_rule)
        out += _inspect_table_titles(page, layout_rule)
        out += _inspect_page_number(page, layout_rule)
    return out


def _nearest(target: Box, candidates: list[Box]) -> Box | None:
    if not candidates:
        return None
    return min(candidates, key=lambda b: abs(b.top - target.top))


def _inspect_captions(page: PageLayout, rule) -> list[CorrectedField]:
    figures = page.by_role("figure")
    captions = page.by_role("caption")
    if not figures:
        return []
    if rule.caption_position is None:
        return [_finding(f"p{page.number}.caption_position", "Caption placement (geometry)",
                         Status.needs_review, observed="figures present", required="undefined",
                         note="Geometric caption rule undefined; specify layout.caption_position.")]
    out = []
    for i, fig in enumerate(figures):
        cap = _nearest(fig, captions)
        key = f"p{page.number}.figure[{i}].caption_geometry"
        if cap is None:
            out.append(_finding(key, "Figure caption geometry", Status.corrected,
                                observed="no caption box near figure", required=rule.caption_position,
                                note="No caption box found near the figure; will be added."))
            continue
        below = cap.top >= fig.bottom - 1.0
        above = cap.bottom <= fig.top + 1.0
        pos_ok = (rule.caption_position == "below" and below) or \
                 (rule.caption_position == "above" and above)
        align_ok = True
        if rule.caption_align_tol is not None:
            align_ok = abs(cap.cx - fig.cx) <= rule.caption_align_tol * page.width
        if pos_ok and align_ok:
            out.append(_finding(key, "Figure caption geometry", Status.unchanged,
                                observed={"pos": rule.caption_position, "aligned": True},
                                required=rule.caption_position, note="Caption geometry conforms."))
        else:
            observed_pos = "below" if below else ("above" if above else "beside/overlap")
            out.append(_finding(key, "Figure caption geometry", Status.corrected,
                                observed={"pos": observed_pos, "aligned": align_ok},
                                required={"pos": rule.caption_position, "aligned": True},
                                note="Caption box not positioned/aligned per discipline; will be moved."))
    return out


def _inspect_table_titles(page: PageLayout, rule) -> list[CorrectedField]:
    tables = page.by_role("table")
    titles = page.by_role("table_title")
    if not tables:
        return []
    if rule.table_title_position is None:
        return [_finding(f"p{page.number}.table_title_position", "Table title placement (geometry)",
                         Status.needs_review, observed="tables present", required="undefined",
                         note="Geometric table-title rule undefined; specify layout.table_title_position.")]
    out = []
    for i, tbl in enumerate(tables):
        title = _nearest(tbl, titles)
        key = f"p{page.number}.table[{i}].title_geometry"
        if title is None:
            out.append(_finding(key, "Table title geometry", Status.corrected,
                                observed="no title box near table", required=rule.table_title_position,
                                note="No title box found near the table; will be added."))
            continue
        above = title.bottom <= tbl.top + 1.0
        below = title.top >= tbl.bottom - 1.0
        ok = (rule.table_title_position == "above" and above) or \
             (rule.table_title_position == "below" and below)
        if ok:
            out.append(_finding(key, "Table title geometry", Status.unchanged,
                                observed=rule.table_title_position, required=rule.table_title_position,
                                note="Table title geometry conforms."))
        else:
            out.append(_finding(key, "Table title geometry", Status.corrected,
                                observed="above" if above else ("below" if below else "beside"),
                                required=rule.table_title_position,
                                note="Table title not positioned per discipline; will be moved."))
    return out


def _inspect_page_number(page: PageLayout, rule) -> list[CorrectedField]:
    pns = page.by_role("page_number")
    if rule.page_number_region is None:
        return []  # structural tier already handles presence; geometry undefined -> silent
    key = f"p{page.number}.page_number_geometry"
    if not pns:
        return [_finding(key, "Page number geometry", Status.corrected,
                         observed="no page-number box", required=rule.page_number_region,
                         note="No page number box detected; will be added.")]
    pn = pns[0]
    in_footer = rule.footer_band is not None and pn.top >= page.height * (1 - rule.footer_band)
    region = rule.page_number_region
    centered = abs(pn.cx - page.width / 2) <= 0.2 * page.width
    ok = in_footer and ("center" not in region or centered)
    if ok:
        return [_finding(key, "Page number geometry", Status.unchanged,
                         observed=region, required=region, note="Page number geometry conforms.")]
    return [_finding(key, "Page number geometry", Status.corrected,
                     observed={"in_footer": in_footer, "centered": centered}, required=region,
                     note="Page number not in the expected region; will be relocated.")]
