"""Turn an uploaded document into project corpus docs.

Bridges the ingestion parsers (which already handle docx/pdf/pptx/txt/md) to the
project CorpusDoc shape, so a user's real document can become the ground-truth
corpus of a generated project. Pure extraction: it reproduces the document's
text faithfully (headings preserved as markdown so the corpus generator can
derive sections), and never adds or invents content.
"""
from __future__ import annotations

from .schema import CorpusDoc


def _blocks_to_markdown(blocks) -> str:
    """Render parsed blocks back to plain text, preserving heading structure as
    markdown '#'. Deterministic and faithful: only the document's own text."""
    lines: list[str] = []
    for b in blocks:
        text = (getattr(b, "text", "") or "").strip()
        if not text:
            continue
        btype = getattr(b, "type", "paragraph")
        if btype in ("heading", "slide_title"):
            lines.append(f"# {text}")
        elif btype == "list_item":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return "\n".join(lines).strip()


def corpus_from_upload(filename: str, data: bytes) -> list[CorpusDoc]:
    """Parse an uploaded document and return it as a single CorpusDoc (the whole
    document is one ground-truth source). Raises ValueError if nothing usable
    could be extracted (e.g. an image-only PDF needing OCR)."""
    from ..parsers import route_and_parse
    from ..pipeline import guess_mime

    mime = guess_mime(filename)
    result = route_and_parse(filename, mime, data, doc_id="upload")
    text = _blocks_to_markdown(result.blocks)
    if not text or len(text) < 10:
        raise ValueError(
            "no usable text could be extracted from the document "
            f"(parser method: {getattr(result, 'method', 'unknown')}). "
            "A scanned/image-only document would need OCR, which is not enabled.")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = f"{stem}.md"
    return [CorpusDoc(name=name, text=text)]


def corpus_from_texts(texts: list[dict]) -> list[CorpusDoc]:
    """Build corpus docs from already-extracted {name, text} entries (e.g. a doc
    the client parsed, or an ingested canonical document). Faithful passthrough;
    enforces the .txt/.md name rule via CorpusDoc."""
    out: list[CorpusDoc] = []
    for i, t in enumerate(texts or []):
        name = (t.get("name") or f"source_{i + 1}").strip()
        body = t.get("text") or ""
        if not body.strip():
            continue
        if not (name.endswith(".txt") or name.endswith(".md")):
            name = f"{name}.md"
        out.append(CorpusDoc(name=name, text=body))
    if not out:
        raise ValueError("no non-empty source texts provided")
    return out
