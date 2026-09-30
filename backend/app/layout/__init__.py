"""Vector-layout tier: extract element GEOMETRY (bounding boxes) from a rendered
PDF and inspect it against geometric discipline rules.

This is geometry, not pixels — every element (figure, caption, table, header /
footer band, page number) is a box on a page, and discipline rules are box
relationships (caption below and aligned to its figure, table title above the
table, header/footer within their bands, page number in the expected region).

Requires a rendered PDF (DOCX -> LibreOffice headless -> PDF). When LibreOffice
is absent the pipeline degrades to structural inspection; this tier simply does
not run.
"""
from .geometry import Box, PageLayout, extract_layout
from .inspect_layout import inspect_layout

__all__ = ["Box", "PageLayout", "extract_layout", "inspect_layout"]
