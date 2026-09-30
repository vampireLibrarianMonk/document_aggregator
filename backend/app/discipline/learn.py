"""Learn the discipline from the user's presented template/draft.

Rather than defaulting to a hardcoded style, the conformance baseline for text
formatting is the document's OWN dominant per-element formatting. The converter
records, per element type, the formatting observed across the document; the
learner takes the most common (dominant) value per attribute as the rule, so
subsequent deviations read as inconsistencies against the document's own style.

Only attributes actually observed become learned rules; anything the document
never establishes stays undefined (the inspector calls it out).
"""
from __future__ import annotations

from collections import Counter

from .fonts import normalize_font
from .spec import BuildDiscipline, TextFormat


def learn_discipline(base: BuildDiscipline, evidence: dict) -> BuildDiscipline:
    """Fill undefined text-format rules by learning the dominant per-element
    formatting from the presented document's evidence. Declared rules are left
    untouched (declaration wins over learning)."""
    if not evidence:
        return base
    observed = evidence.get("text_format_samples") or evidence.get("text_format") or {}
    if not observed:
        return base

    for etype, samples in observed.items():
        # samples may be a single dict (first-seen) or a list of dicts.
        sample_list = samples if isinstance(samples, list) else [samples]
        learned = _dominant(sample_list)
        if learned is None:
            continue
        existing = base.text_format.get(etype)
        if existing and existing.source == "declared":
            continue  # declaration wins
        base.text_format[etype] = learned
    if base.text_format:
        base.rule_sources.setdefault("text_format", "learned")

    _learn_image_placement(base, evidence)
    return base


def _learn_image_placement(base: BuildDiscipline, evidence: dict) -> None:
    """Learn figure title/size/alignment expectations from the template's own
    figures. A well-formed template establishes the standard (every figure has a
    centered title at a consistent width); the draft is then judged against it.
    Only learn where the template is consistent; declaration still wins."""
    images = evidence.get("images") or []
    if not images:
        return
    ir = base.images

    # title_required: learn True only if EVERY figure in the template has a title
    # (a consistent standard); otherwise leave undefined rather than guess.
    if ir.title_required is None:
        titled = [bool(im.get("has_title")) for im in images]
        if titled and all(titled):
            ir.title_required = True
        elif titled and not any(titled):
            ir.title_required = False

    # title_align / figure align / width: dominant observed value, only if the
    # template actually reveals it.
    if ir.title_align is None:
        aligns = Counter(im.get("title_align") for im in images if im.get("title_align"))
        if aligns:
            ir.title_align = aligns.most_common(1)[0][0]
    if ir.align is None:
        faligns = Counter(im.get("align") for im in images if im.get("align"))
        if faligns:
            ir.align = faligns.most_common(1)[0][0]
    if ir.width_px is None:
        widths = Counter(im.get("width") for im in images if im.get("width"))
        if widths:
            ir.width_px = widths.most_common(1)[0][0]

    base.rule_sources.setdefault("image_placement", "learned")


def _dominant(samples: list[dict]) -> TextFormat | None:
    """Most common observed value per attribute across samples. An attribute
    never observed (always empty/None) stays None -> undefined for that attr."""
    if not samples:
        return None
    fonts = Counter(normalize_font(s.get("font")) for s in samples if s.get("font"))
    sizes = Counter(s.get("size_pt") for s in samples if s.get("size_pt"))
    weights = Counter(s.get("weight") for s in samples if s.get("weight"))
    casings = Counter(s.get("casing") for s in samples if s.get("casing"))

    def top(c: Counter):
        return c.most_common(1)[0][0] if c else None

    tf = TextFormat(font=top(fonts), size_pt=top(sizes), weight=top(weights),
                    casing=top(casings), source="learned")
    return tf if tf.is_defined() else None
