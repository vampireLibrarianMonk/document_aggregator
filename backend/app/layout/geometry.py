"""Extract vector layout (element bounding boxes) from a PDF via pdfplumber.

Coordinates use PDF points with a TOP-LEFT origin (pdfplumber's `top`/`bottom`),
so "below" means a larger `top`. We classify text spans into roles by their
content (figure caption, table title, page number) and record image rectangles
and detected table bounding boxes. This is offline and deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FIG_CAPTION = re.compile(r"^\s*(figure|fig\.?)\s*\d+", re.IGNORECASE)
_TABLE_TITLE = re.compile(r"^\s*table\s*\d+", re.IGNORECASE)
_PAGE_NUM = re.compile(r"^\s*(page\s*)?\d{1,3}\s*$", re.IGNORECASE)


@dataclass
class Box:
    x0: float
    top: float
    x1: float
    bottom: float
    role: str = ""        # figure | caption | table | table_title | page_number | text
    text: str = ""

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1))


@dataclass
class PageLayout:
    number: int
    width: float
    height: float
    boxes: list[Box] = field(default_factory=list)

    def by_role(self, role: str) -> list[Box]:
        return [b for b in self.boxes if b.role == role]


def extract_layout(pdf_bytes: bytes) -> list[PageLayout]:
    import io

    import pdfplumber

    pages: list[PageLayout] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pl = PageLayout(number=i, width=float(page.width), height=float(page.height))
            # Text lines with bounding boxes, classified by content.
            for line in _lines(page):
                role = _classify(line["text"])
                pl.boxes.append(Box(line["x0"], line["top"], line["x1"], line["bottom"],
                                    role=role, text=line["text"]))
            # Embedded raster images are figures.
            for img in page.images:
                pl.boxes.append(Box(float(img["x0"]), float(img["top"]),
                                    float(img["x1"]), float(img["bottom"]), role="figure"))
            # Detected tables (bounding boxes) first, so we can exclude their
            # rectangles from figure candidates.
            table_boxes: list[tuple[float, float, float, float]] = []
            try:
                for tb in page.find_tables():
                    x0, top, x1, bottom = tb.bbox
                    table_boxes.append((float(x0), float(top), float(x1), float(bottom)))
                    pl.boxes.append(Box(float(x0), float(top), float(x1), float(bottom), role="table"))
            except Exception:
                pass
            # A large standalone rectangle (not a table, not tiny) is a figure
            # candidate — real figures render as either an image or a framed box.
            for rect in getattr(page, "rects", []):
                rx0, rtop, rx1, rbottom = (float(rect["x0"]), float(rect["top"]),
                                           float(rect["x1"]), float(rect["bottom"]))
                area = (rx1 - rx0) * (rbottom - rtop)
                if area < 4000:  # ignore small rules/cell borders
                    continue
                if _overlaps_any((rx0, rtop, rx1, rbottom), table_boxes):
                    continue
                if not any(b.role == "figure" and _overlaps((rx0, rtop, rx1, rbottom),
                                                             (b.x0, b.top, b.x1, b.bottom))
                           for b in pl.boxes):
                    pl.boxes.append(Box(rx0, rtop, rx1, rbottom, role="figure"))
            pages.append(pl)
    return pages


def _overlaps(a: tuple, b: tuple) -> bool:
    ax0, atop, ax1, abot = a
    bx0, btop, bx1, bbot = b
    return not (ax1 < bx0 or bx1 < ax0 or abot < btop or bbot < atop)


def _overlaps_any(a: tuple, boxes: list[tuple]) -> bool:
    return any(_overlaps(a, b) for b in boxes)


def _lines(page) -> list[dict]:
    """Group words into visual lines with a merged bounding box."""
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    lines: dict[int, list[dict]] = {}
    for w in words:
        key = int(round(float(w["top"]) / 3.0))  # ~3pt line bucketing
        lines.setdefault(key, []).append(w)
    out = []
    for _, ws in sorted(lines.items()):
        x0 = min(float(w["x0"]) for w in ws)
        x1 = max(float(w["x1"]) for w in ws)
        top = min(float(w["top"]) for w in ws)
        bottom = max(float(w["bottom"]) for w in ws)
        text = " ".join(w["text"] for w in ws)
        out.append({"x0": x0, "x1": x1, "top": top, "bottom": bottom, "text": text})
    return out


def _classify(text: str) -> str:
    if _FIG_CAPTION.match(text):
        return "caption"
    if _TABLE_TITLE.match(text):
        return "table_title"
    if _PAGE_NUM.match(text):
        return "page_number"
    return "text"
