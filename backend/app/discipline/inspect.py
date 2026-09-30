"""Document-inspection: check observed evidence against the build discipline.

The app's core purpose. Policy (no silent defaults):
  - a rule that is DECLARED or LEARNED is enforced;
  - a rule that is UNDEFINED (neither declared nor learnable from the document)
    produces an explicit needs_review call-out — never a guessed default.

Findings:
  unchanged     observed matches the discipline
  corrected     a fixable placement/format violation the engine can normalize
  needs_review  a violation needing human input, OR an undefined-rule call-out

"Not observed" (a formatting attribute the document does not reveal) is not a
violation; it simply cannot be checked.
"""
from __future__ import annotations

from ..reconcile.models import CorrectedField, DefectClass, Provenance, Status
from .fonts import fonts_equivalent
from .spec import BuildDiscipline


def _finding(key: str, label: str, status: Status, observed, required, note: str) -> CorrectedField:
    return CorrectedField(
        key=key, label=label, value={"observed": observed, "required": required},
        status=status, defect_class=DefectClass.discipline,
        provenance=Provenance(rule=f"build_discipline.{key}"), note=note,
    )


def _undefined(key: str, label: str) -> CorrectedField:
    return _finding(
        key, label, Status.needs_review, observed="present", required="undefined",
        note=(f"Discipline for {label} is undefined; specify it in the template's "
              "build_discipline block or establish it in the source document."),
    )


def inspect(evidence: dict, discipline: BuildDiscipline) -> list[CorrectedField]:
    if not evidence:
        return []
    findings: list[CorrectedField] = []
    findings += _inspect_images(evidence.get("images", []), discipline)
    findings += _inspect_tables(evidence.get("tables", []), discipline)
    findings += _inspect_header(evidence.get("header", {}), discipline)
    findings += _inspect_footer(evidence, discipline)
    findings += _inspect_page_numbers(evidence.get("page_numbers", {}), discipline)
    findings += _inspect_text_format(
        evidence.get("text_format_samples", {}), discipline)
    return findings


def _inspect_images(images: list[dict], d: BuildDiscipline) -> list[CorrectedField]:
    out = []
    caption_required = d.images.caption_required
    for i, img in enumerate(images):
        name = img.get("name", f"image_{i}")
        # --- caption policy (existing) ---
        if caption_required is None:
            out.append(_undefined(f"image[{i}].caption", f"Caption policy for {name}"))
        elif caption_required and not img.get("has_caption"):
            out.append(_finding(
                f"image[{i}].caption", f"Caption for {name}", Status.corrected,
                observed="missing", required=d.images.caption_format or "required",
                note="Figure caption required by discipline; will be generated.",
            ))
        elif (d.images.caption_position and img.get("caption_position")
              and img["caption_position"] != d.images.caption_position):
            out.append(_finding(
                f"image[{i}].caption_position", f"Caption position for {name}", Status.corrected,
                observed=img["caption_position"], required=d.images.caption_position,
                note=f"Caption should be {d.images.caption_position} the figure.",
            ))

        # --- title policy (divined from the template's figures) ---
        if d.images.title_required:
            if not img.get("has_title"):
                out.append(_finding(
                    f"image[{i}].title", f"Title for {name}", Status.corrected,
                    observed="missing", required="centered title",
                    note="Template figures carry a title; this figure has none. "
                         "A title will be added.",
                ))
            elif (d.images.title_align and img.get("title_align")
                  and img["title_align"] != d.images.title_align):
                out.append(_finding(
                    f"image[{i}].title_align", f"Title alignment for {name}", Status.corrected,
                    observed=img["title_align"], required=d.images.title_align,
                    note=f"Figure title should be {d.images.title_align}-aligned.",
                ))

        # --- size policy (divined width; flag large deviations) ---
        if d.images.width_px and img.get("width"):
            exp, obs = d.images.width_px, img["width"]
            if abs(obs - exp) > max(24, int(exp * 0.15)):  # >15% off (or >24px)
                out.append(_finding(
                    f"image[{i}].width", f"Size for {name}", Status.corrected,
                    observed=f"{obs}px", required=f"~{exp}px",
                    note="Figure width deviates from the template standard; "
                         "will be resized.",
                ))

        # --- alignment policy (divined; flag non-centered when template centers) ---
        if d.images.align and img.get("align") and img["align"] != d.images.align:
            out.append(_finding(
                f"image[{i}].align", f"Placement for {name}", Status.corrected,
                observed=img["align"], required=d.images.align,
                note=f"Figure should be {d.images.align}-aligned per the template.",
            ))
    return out


def _inspect_tables(tables: list[dict], d: BuildDiscipline) -> list[CorrectedField]:
    out = []
    for i, tbl in enumerate(tables):
        if d.tables.title_required is None:
            out.append(_undefined(f"table[{i}].title", f"Table {i + 1} title policy"))
        elif d.tables.title_required and not tbl.get("has_title"):
            out.append(_finding(
                f"table[{i}].title", f"Table {i + 1} title", Status.corrected,
                observed="missing", required=d.tables.title_format or "required",
                note="Table title required by discipline; will be generated.",
            ))
        elif (d.tables.title_position and tbl.get("title_position")
              and tbl["title_position"] != d.tables.title_position):
            out.append(_finding(
                f"table[{i}].title_position", f"Table {i + 1} title position", Status.corrected,
                observed=tbl["title_position"], required=d.tables.title_position,
                note=f"Table title should be {d.tables.title_position} the table.",
            ))
        hs = tbl.get("header_style")
        if d.tables.header_style and hs and hs != d.tables.header_style:
            out.append(_finding(
                f"table[{i}].header_style", f"Table {i + 1} header style", Status.corrected,
                observed=hs, required=d.tables.header_style,
                note="Table header style non-conforming; will be normalized.",
            ))
    return out


def _inspect_header(header: dict, d: BuildDiscipline) -> list[CorrectedField]:
    if d.header.required is None:
        return [_undefined("header", "Header policy")]
    if not d.header.required:
        return []
    text = (header.get("text") or "").strip()
    if not text:
        return [_finding("header", "Header", Status.needs_review,
                         observed="missing", required=d.header.must_contain or "required",
                         note="Header required by discipline; no content observed.")]
    return [_finding("header", "Header", Status.unchanged,
                     observed="present", required="conforms", note="Header present.")]


def _inspect_footer(evidence: dict, d: BuildDiscipline) -> list[CorrectedField]:
    if d.footer.required is None:
        return [_undefined("footer", "Footer policy")]
    if not d.footer.required:
        return []
    out = []
    footer = evidence.get("footer", {})
    text = (footer.get("text") or "").strip()
    if "classification" in d.footer.must_contain and not evidence.get("classification"):
        out.append(_finding(
            "footer.classification", "Footer classification marking", Status.needs_review,
            observed="missing", required="classification marking",
            note="Footer must carry a classification marking; none observed (human input).",
        ))
    if not text and not evidence.get("classification"):
        out.append(_finding(
            "footer", "Footer", Status.corrected, observed="empty",
            required=d.footer.must_contain or "required", note="Footer assembled with page number.",
        ))
    return out


def _inspect_page_numbers(pn: dict, d: BuildDiscipline) -> list[CorrectedField]:
    if d.page_numbers.required is None:
        return [_undefined("page_numbers", "Page-number policy")]
    if not d.page_numbers.required:
        return []
    if not pn.get("present"):
        return [_finding("page_numbers", "Page numbers", Status.corrected,
                         observed="absent",
                         required=f"present in {d.page_numbers.position or 'footer'}",
                         note="Page numbers required by discipline; will be added.")]
    if d.page_numbers.position and pn.get("position") and pn["position"] != d.page_numbers.position:
        return [_finding("page_numbers.position", "Page number position", Status.corrected,
                         observed=pn["position"], required=d.page_numbers.position,
                         note="Page numbers in the wrong region; will be relocated.")]
    return [_finding("page_numbers", "Page numbers", Status.unchanged,
                     observed="present", required="conforms", note="Page numbers conform.")]


def _inspect_text_format(samples: dict, d: BuildDiscipline) -> list[CorrectedField]:
    """Enforce per-element text formatting against declared/learned rules; flag
    within-document drift; call out element types with no established rule."""
    out = []
    for etype, sample_list in samples.items():
        rule = d.text_format.get(etype)
        sl = sample_list if isinstance(sample_list, list) else [sample_list]
        if rule is None or not rule.is_defined():
            out.append(_undefined(f"text_format.{etype}", f"{etype} formatting policy"))
            continue
        for j, obs in enumerate(sl):
            for attr in ("font", "size_pt", "weight", "casing"):
                o = obs.get(attr)
                req = getattr(rule, attr)
                if not o or req is None:
                    continue
                # Fonts compare by metric-compatible family: 'Arial' conforms to
                # 'Liberation Sans' because they are metrically identical.
                if attr == "font":
                    if fonts_equivalent(o, req):
                        continue
                elif o == req:
                    continue
                out.append(_finding(
                    f"text_format.{etype}[{j}].{attr}", f"{etype} {attr}", Status.corrected,
                    observed=o, required=req,
                    note=f"{etype} {attr} deviates from the established style "
                         f"({rule.source}); will be normalized.",
                ))
    return out
