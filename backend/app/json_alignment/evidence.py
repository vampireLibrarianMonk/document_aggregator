"""Evidence-string construction for the optional semantic (embedding) matcher
tier.

A field's "evidence string" is the deterministic text we embed when comparing a
source field to a target field by MEANING: the field name (de-camel/snake-cased)
followed by its description (a sentence or paragraph, when the schema/source
provides one) and a few example values. Richer text is a stronger signal — a
measurement (see tests/alpha_frontier/evidence.py) showed that embedding this
evidence string ranks real synonyms correctly where embedding the bare 2-word
name does not.

IMPORTANT: embeddings are used only as a RECALL/ranking signal. The same
measurement showed they do NOT separate signal from in-domain junk on their own
(a 'year_installed' column embeds as close to releaseYear as a true synonym), so
the embedding matcher tier never grounds on cosine alone — every proposal must
still clear the deterministic value-evidence gate. This module only builds the
text; the gating lives in mapping.py.
"""
from __future__ import annotations

import re
from typing import Any

_MAX_EXAMPLES = 3
_MAX_VALUE_CHARS = 80


def clean_name(name: str) -> str:
    """A readable form of a field key: split camelCase/snake/kebab to words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return re.sub(r"[_\-]+", " ", spaced).strip().lower()


def evidence_string(name: str, description: str = "",
                    examples: list[Any] | tuple[Any, ...] = ()) -> str:
    """Build the deterministic text embedded for a field.

    name + description (sentence/paragraph) + up to a few truncated example
    values. Empty / whitespace-only parts are skipped. Pure and reproducible: the
    same inputs always produce the same string, so embeddings are cacheable."""
    parts: list[str] = []
    cn = clean_name(name)
    if cn:
        parts.append(cn)
    if description and description.strip():
        parts.append(description.strip())
    vals: list[str] = []
    for e in examples:
        if e is None:
            continue                      # skip Nones without consuming a slot
        s = str(e).strip()
        if not s:
            continue
        if len(s) > _MAX_VALUE_CHARS:
            s = s[:_MAX_VALUE_CHARS]
        vals.append(s)
        if len(vals) >= _MAX_EXAMPLES:
            break
    if vals:
        parts.append("examples: " + "; ".join(vals))
    return ". ".join(parts)


__all__ = ["clean_name", "evidence_string"]
