"""Metric-compatible font substitution.

The proprietary Office fonts (Arial, Times New Roman, Courier New, Calibri,
Cambria) are not redistributable and are absent on Linux; LibreOffice
substitutes them silently at render time. The Liberation family (SIL OFL) and
Carlito/Caladea are METRIC-COMPATIBLE — each glyph has identical width/height to
its proprietary counterpart — so a document specifying Arial renders to the same
box geometry whether the box has Arial or Liberation Sans.

We make that substitution EXPLICIT: normalize any observed/declared font to its
metric-compatible family before conformance checks, so 'Arial' and
'Liberation Sans' are treated as the same family. Combined with pinning the
Liberation set in the image, this makes geometry reproducible.
"""
from __future__ import annotations

# proprietary -> metric-compatible (redistributable) family
SUBSTITUTIONS = {
    "arial": "Liberation Sans",
    "helvetica": "Liberation Sans",
    "arial narrow": "Liberation Sans Narrow",
    "times new roman": "Liberation Serif",
    "times": "Liberation Serif",
    "courier new": "Liberation Mono",
    "courier": "Liberation Mono",
    "calibri": "Carlito",
    "cambria": "Caladea",
}

# Families that are already the canonical target (identity-normalize).
_CANON = {
    "liberation sans": "Liberation Sans",
    "liberation serif": "Liberation Serif",
    "liberation mono": "Liberation Mono",
    "liberation sans narrow": "Liberation Sans Narrow",
    "carlito": "Carlito",
    "caladea": "Caladea",
}


def normalize_font(name: str | None) -> str | None:
    """Map a font name to its metric-compatible canonical family. Unknown fonts
    pass through unchanged (still comparable to themselves)."""
    if not name:
        return name
    key = name.strip().lower()
    if key in SUBSTITUTIONS:
        return SUBSTITUTIONS[key]
    if key in _CANON:
        return _CANON[key]
    return name


def fonts_equivalent(a: str | None, b: str | None) -> bool:
    """Two fonts conform if they normalize to the same metric-compatible family."""
    if a is None or b is None:
        return False
    return normalize_font(a) == normalize_font(b)
