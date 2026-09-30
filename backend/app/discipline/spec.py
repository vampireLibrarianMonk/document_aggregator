"""The build-discipline specification.

A single declarative contract for element placement and formatting, resolved
for a given template + presented document. Core policy (per project decision):

    Nothing is silently defaulted. Every rule is either
      (a) explicitly declared in the template's `build_discipline` block, or
      (b) LEARNED from the user's presented template/draft (its own dominant
          per-element formatting), or
      (c) left UNDEFINED — and the inspector calls it out as needs_review
          ("discipline for X is undefined; specify it") rather than guessing.

Placement rules (caption/title required, page-number presence, header/footer
content) are declared or derived from the template's legacy furniture/table_specs
blocks. Text-formatting rules (font/size/weight/casing per element type) are
declared or learned from the document; if neither, they are undefined.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TextFormat(BaseModel):
    font: str | None = None
    size_pt: int | None = None
    weight: str | None = None      # normal | bold
    casing: str | None = None      # none | title | upper | sentence
    source: str = "undefined"      # declared | learned | undefined

    def is_defined(self) -> bool:
        return self.source != "undefined" and any(
            v is not None for v in (self.font, self.size_pt, self.weight, self.casing)
        )


class ImageRule(BaseModel):
    caption_required: bool | None = None       # None = undefined
    caption_format: str | None = None
    caption_position: str | None = None        # below | above
    numbering: str | None = None
    # Figure title expectations (learned from the template's own figures).
    title_required: bool | None = None         # None = undefined
    title_align: str | None = None             # center | left | right
    width_px: int | None = None                # expected rendered width
    align: str | None = None                   # figure alignment: center | left | right


class TableRule(BaseModel):
    title_required: bool | None = None
    title_format: str | None = None
    title_position: str | None = None          # above | below
    numbering: str | None = None
    header_style: str | None = None
    column_alignment: str | None = None        # left | center | right


class HeaderRule(BaseModel):
    required: bool | None = None
    must_contain: list[str] = Field(default_factory=list)


class FooterRule(BaseModel):
    required: bool | None = None
    must_contain: list[str] = Field(default_factory=list)


class PageNumberRule(BaseModel):
    required: bool | None = None
    position: str | None = None                # footer | header


class LayoutRule(BaseModel):
    """Geometric placement rules checked against a rendered PDF's element boxes.
    Fractions are of page height (bands) or page width (alignment tolerance).
    None = undefined (called out, never defaulted)."""
    caption_position: str | None = None        # below | above (relative to figure)
    caption_align_tol: float | None = None      # max center-x offset as fraction of page width
    table_title_position: str | None = None     # above | below
    header_band: float | None = None            # top fraction reserved for header
    footer_band: float | None = None            # bottom fraction reserved for footer
    page_number_region: str | None = None        # footer-center | footer-right | header-right ...


class BuildDiscipline(BaseModel):
    images: ImageRule = Field(default_factory=ImageRule)
    tables: TableRule = Field(default_factory=TableRule)
    header: HeaderRule = Field(default_factory=HeaderRule)
    footer: FooterRule = Field(default_factory=FooterRule)
    page_numbers: PageNumberRule = Field(default_factory=PageNumberRule)
    layout: LayoutRule = Field(default_factory=LayoutRule)
    cross_references_must_resolve: bool | None = None

    # Per element-type text formatting. Populated by declaration or learning;
    # any element type absent here is UNDEFINED (inspector calls it out).
    text_format: dict[str, TextFormat] = Field(default_factory=dict)

    # Provenance of how each rule area was established (declared|learned|undefined).
    rule_sources: dict[str, str] = Field(default_factory=dict)


def load_profile(profile_id: str) -> BuildDiscipline:
    """Load a committed, pinned discipline profile (e.g. 'gov_standard') as a
    fully-declared BuildDiscipline. This is the explicit, reproducible baseline."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent / "profiles" / f"{profile_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    d = BuildDiscipline(
        images=ImageRule(**data.get("images", {})),
        tables=TableRule(**data.get("tables", {})),
        header=HeaderRule(**data.get("header", {})),
        footer=FooterRule(**data.get("footer", {})),
        page_numbers=PageNumberRule(**data.get("page_numbers", {})),
        layout=LayoutRule(**data.get("layout", {})),
        cross_references_must_resolve=data.get("cross_references_must_resolve"),
        text_format={k: TextFormat(**v) for k, v in data.get("text_format", {}).items()},
    )
    d.rule_sources["placement"] = "declared"
    d.rule_sources["text_format"] = "declared"
    d.rule_sources["profile"] = profile_id
    return d


def load_discipline(template: dict) -> BuildDiscipline:
    """Build the placement discipline from a template's explicit block, a pinned
    profile reference, or its legacy furniture/table_specs. Text formatting is
    not defaulted here — it is either declared (incl. via profile) or learned
    from the presented document. Nothing is silently defaulted to Arial/11pt."""
    d = BuildDiscipline()

    # A template may pin a committed profile: build_discipline: {"profile": "gov_standard"}
    bd = template.get("build_discipline")
    if isinstance(bd, dict) and bd.get("profile"):
        return load_profile(bd["profile"])

    if "build_discipline" in template:
        block = template["build_discipline"]
        parsed = BuildDiscipline(**block)
        parsed.rule_sources.setdefault("placement", "declared")
        if parsed.text_format:
            for tf in parsed.text_format.values():
                if tf.source == "undefined":
                    tf.source = "declared"
            parsed.rule_sources.setdefault("text_format", "declared")
        return parsed

    furn = template.get("furniture", {})
    if furn:
        figs = furn.get("figures", {})
        if figs:
            d.images.caption_required = figs.get("caption_required")
            d.images.caption_format = figs.get("caption_format")
            d.images.caption_position = figs.get("caption_position")
            d.images.numbering = figs.get("numbering")
        tbls = furn.get("tables", {})
        if tbls:
            d.tables.title_required = tbls.get("title_required")
            d.tables.title_position = tbls.get("title_position")
            d.tables.numbering = tbls.get("numbering")
        if furn.get("header"):
            d.header.required = furn["header"].get("required", True)
            d.header.must_contain = furn["header"].get("must_contain", [])
        if furn.get("footer"):
            d.footer.required = furn["footer"].get("required", True)
            d.footer.must_contain = furn["footer"].get("must_contain", [])
            d.page_numbers.required = "page_number" in d.footer.must_contain
            d.page_numbers.position = "footer" if d.page_numbers.required else None
        xr = furn.get("cross_references", {})
        if xr:
            d.cross_references_must_resolve = xr.get("must_resolve")
        # Geometric layout rules mirror the declared placement positions so the
        # vector tier can check them as box relationships.
        if d.images.caption_position:
            d.layout.caption_position = d.images.caption_position
            d.layout.caption_align_tol = 0.15
        if d.tables.title_position:
            d.layout.table_title_position = d.tables.title_position
        if d.page_numbers.required:
            d.layout.footer_band = 0.12
            d.layout.page_number_region = "footer-center"
        if d.header.required:
            d.layout.header_band = 0.12
        d.rule_sources["placement"] = "declared"

    specs: dict[str, Any] = template.get("table_specs", {})
    if specs:
        first = next(iter(specs.values()))
        d.tables.header_style = first.get("header_style")
    return d
