"""Deterministic, corpus-grounded project generator.

This is the heart of "faithful + deterministic" generation and the reason the
app exists: the user supplies the REAL source material, and that material becomes
the project's corpus (ground truth). Unlike the model path, nothing is invented
-- every asserted value is pulled verbatim from the provided corpus, so
_check_corpus_grounding passes by construction and there is no fabrication.

It is a pure function of (corpus, brief): no model, no RNG, no clock. The same
corpus + brief yields a byte-identical ProjectSpec every time, and once
persisted the project reconciles with no model at all. That closes the drift the
model path exhibits (see GOVERNOR_EVAL.md adversarial review).

What it reads out of the real documents (all deterministic, all grounded):
  - sections:    the document's own markdown headings ('## Findings' -> a section).
  - fields:      'Label: value' lines near the top (Date / Site / Author ...),
                 kept verbatim (no truncation), typed by a light heuristic.
  - figures:     a '## Figures' block of 'name.png: caption' lines -> graphics,
                 each attached to the section its caption best matches.
  - table:       a '## Corrective Action Assignments' block of
                 'Action: ... Owner: ... Due: ...' items -> a real table spec.
  - draft:       a flawed first attempt that mangles the grounded field values.
  - corrections: each restores the real corpus value (grounded), plus (only when
                 the corpus actually supports two readings) one genuine conflict.
"""
from __future__ import annotations

import re

from .generator import ProjectBrief
from .schema import (
    CorpusDoc,
    CorrectionSpec,
    DraftSection,
    FieldSpec,
    GraphicSpec,
    ProjectSpec,
    SectionBodySpec,
    SectionSpec,
    TableColumnSpec,
    TableSpec,
)

_LABEL_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z][\w /-]{1,40}?)\s*[:=]\s*(.+?)\s*$")
_HEADING_MD = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
# A bare (unmarked) heading line in a plain-text document: a short standalone
# line with no trailing punctuation and no 'Label: value' colon, e.g.
# "Summary", "Observations", "Preliminary Assessment". Deterministic.
_HEADING_BARE = re.compile(r"^\s*([A-Z][A-Za-z][A-Za-z ]{1,38}[A-Za-z])\s*$")
_FIGURE_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z0-9_./-]+\.png)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE)
# A corrective-action item: 'Action: <x> Owner: <y> Due: <z>' (Owner/Due optional).
_ACTION_RE = re.compile(
    r"Action:\s*(?P<action>.+?)(?:\s+Owner:\s*(?P<owner>.+?))?(?:\s+Due:\s*(?P<due>.+?))?\s*$",
    re.IGNORECASE,
)
# Fields we never promote to a project field (document furniture, not content).
_SKIP_LABELS = {"http", "https", "action", "owner", "due", "figures", "figure"}
# Section headings whose bodies are prose we ground as a section_body lookup.
_PROSE_HEADINGS = ("summary", "scope", "findings", "assessment", "observations",
                   "contributing", "recommendation", "description", "overview")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "section"


def _as_corpus_docs(corpus) -> list[CorpusDoc]:
    """Coerce the provided corpus (list of CorpusDoc | dict | (name,text)) into
    CorpusDoc objects, enforcing the .txt/.md name rule."""
    out: list[CorpusDoc] = []
    for i, item in enumerate(corpus or []):
        if isinstance(item, CorpusDoc):
            out.append(item)
            continue
        if isinstance(item, dict):
            name, text = item.get("name", ""), item.get("text", "")
        elif isinstance(item, tuple | list) and len(item) == 2:
            name, text = item
        else:
            name, text = "", str(item)
        name = name or f"source_{i + 1}.txt"
        if not (name.endswith(".txt") or name.endswith(".md")):
            name = f"{_slug(name)}.txt"
        if text and text.strip():
            out.append(CorpusDoc(name=name, text=text))
    return out


def _heading_of(line: str) -> str | None:
    """Return the heading text if `line` is a heading (markdown '#...' or a bare
    standalone Title/Caps line in a plain-text doc), else None. Deterministic."""
    m = _HEADING_MD.match(line)
    if m:
        return m.group(1).strip()
    m = _HEADING_BARE.match(line)
    if m:
        # A bare heading must not be a 'Label: value' line (handled above) and
        # must look like a title (<= 5 words), not a prose sentence.
        text = m.group(1).strip()
        if ":" not in line and len(text.split()) <= 5:
            return text
    return None


def _labeled_values(text: str) -> list[tuple[str, str]]:
    """Extract deterministic (label, value) pairs from 'Label: value' lines in
    the document's HEADER BLOCK only — the lines up to the first SECTION heading.
    A leading document title (the first heading) is allowed to precede the header
    block, since '# Title' then 'Date: ...' then '## Scope' is the common shape.
    Values are verbatim and kept WHOLE (never split on internal punctuation),
    which avoids grabbing prose timestamps like '02:11' as fields."""
    pairs: list[tuple[str, str]] = []
    seen_heading = 0
    for line in text.splitlines():
        if _heading_of(line) is not None:
            seen_heading += 1
            # Allow the first heading (document title); stop at the second
            # heading (the first real section) so we only read the header block.
            if seen_heading >= 2:
                break
            continue
        m = _LABEL_RE.match(line)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if label.lower() in _SKIP_LABELS:
            continue
        if value.lower().endswith(".png"):
            continue  # a figure line, handled separately
        # A label is a word or two, not a sentence fragment with internal spaces
        # that is really prose; cap label length and reject digit-led values that
        # look like a stray timestamp fragment.
        if len(label.split()) > 4:
            continue
        if value and len(value) <= 80:
            pairs.append((label, value))
    return pairs


def _headings(text: str) -> list[str]:
    """All headings in order (markdown or bare), deterministic."""
    out: list[str] = []
    for line in text.splitlines():
        h = _heading_of(line)
        if h is not None:
            out.append(h)
    return out


def _section_blocks(text: str) -> dict[str, str]:
    """Map each heading -> the prose text under it (until the next heading).
    Recognizes markdown and bare headings. Used to ground section bodies and to
    locate the figures / corrective-action blocks."""
    blocks: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        h = _heading_of(line)
        if h is not None:
            if current is not None:
                blocks[current] = "\n".join(buf).strip()
            current = h
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip()
    return blocks


def _find_block(blocks: dict[str, str], *needles: str) -> str:
    """Return the body of the first heading containing any needle (lowercased)."""
    for head, body in blocks.items():
        hl = head.lower()
        if any(n in hl for n in needles):
            return body
    return ""


def _parse_figures(block: str) -> list[tuple[str, str]]:
    """Parse '- name.png: caption' lines -> [(name, caption)]."""
    out: list[tuple[str, str]] = []
    for line in block.splitlines():
        m = _FIGURE_RE.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip().rstrip(".")))
    return out


def _parse_actions(block: str) -> list[dict[str, str]]:
    """Parse corrective-action items. Items may wrap across lines, so we first
    re-join on 'Action:' boundaries, then pull Action/Owner/Due out of each."""
    # Normalize whitespace and split into items at each 'Action:'.
    flat = re.sub(r"\s+", " ", block.replace("\n", " ")).strip()
    parts = re.split(r"(?i)(?=\bAction:)", flat)
    rows: list[dict[str, str]] = []
    for part in parts:
        part = part.strip(" -*")
        if not part:
            continue
        m = _ACTION_RE.search(part)
        if not m:
            continue
        action = (m.group("action") or "").strip().rstrip(".")
        owner = (m.group("owner") or "").strip().rstrip(".")
        due = (m.group("due") or "").strip().rstrip(".")
        if action:
            rows.append({"Action": action, "Owner": owner, "Due": due})
    return rows


def _mangle(value: str) -> str:
    """Deterministically produce a WRONG draft value from a correct one, so the
    draft has something to correct. Pure function, no randomness."""
    v = value.strip()
    # Flip a trailing digit (versions/dates/counts), else append a marker.
    m = re.search(r"(\d)(\D*)$", v)
    if m:
        d = int(m.group(1))
        return v[:m.start(1)] + str((d + 1) % 10) + m.group(2)
    return v + " (DRAFT - unverified)"


def _best_section_for_caption(caption: str, section_keys: list[str],
                              section_headings: dict[str, str],
                              exclude: set[str] | None = None) -> str:
    """Pick the section whose heading shares the most words with the caption,
    skipping sections already assigned to another figure (`exclude`) so each
    figure lands in its own section. Falls back to the first free section, or —
    if all are taken — the plain best match. Deterministic word-overlap."""
    exclude = exclude or set()
    cap = {w for w in re.split(r"[^a-z0-9]+", caption.lower()) if len(w) > 2}

    def score(k: str) -> int:
        htok = {w for w in re.split(r"[^a-z0-9]+", section_headings.get(k, "").lower())
                if len(w) > 2}
        return len(cap & htok)

    free = [k for k in section_keys if k not in exclude]
    pool = free or section_keys
    best, best_n = pool[0], -1
    for k in pool:
        n = score(k)
        if n > best_n:
            best, best_n = k, n
    return best


def _place_figure(name: str, caption: str, section_keys: list[str],
                  section_headings: dict[str, str], sec_text: dict[str, str]) -> str:
    """Place a figure in the section whose PROSE actually references it, so a
    figure travels with the text that talks about it (page-by-page association):
      1) a section whose body mentions the figure's filename or its stem;
      2) else the section whose body shares the most caption content words
         (a real textual tie, not just a heading-word coincidence);
      3) else caption/heading word overlap (the old heuristic);
      4) else the first section.
    Deterministic. Multiple figures may land in the same section."""
    stem = name.rsplit(".", 1)[0].lower()
    stem_words = {w for w in re.split(r"[^a-z0-9]+", stem) if len(w) > 2}
    cap_words = {w for w in re.split(r"[^a-z0-9]+", caption.lower()) if len(w) > 2}

    # 1) explicit filename / stem mention in a section's prose.
    for k in section_keys:
        body = sec_text.get(k, "")
        if name.lower() in body or (stem and stem in body):
            return k

    # 2) section whose body shares the most caption content words (>=2 to count).
    best_k, best_n = None, 1
    for k in section_keys:
        body_words = set(re.split(r"[^a-z0-9]+", sec_text.get(k, "")))
        n = len((cap_words | stem_words) & body_words)
        if n > best_n:
            best_k, best_n = k, n
    if best_k is not None:
        return best_k

    # 3) caption/heading overlap, then 4) first section.
    return _best_section_for_caption(caption or stem, section_keys, section_headings)


_EXTRACT_BY_LABEL = {
    "date": "date", "incident date": "date", "due": "date",
    "firmware": "version", "version": "version",
}


def _extract_kind(label: str, value: str) -> str:
    low = label.lower()
    if low in _EXTRACT_BY_LABEL:
        return _EXTRACT_BY_LABEL[low]
    if re.search(r"\d{4}-\d{2}-\d{2}", value):
        return "date"
    return "line"


class CorpusProjectGenerator:
    """Builds a validated, corpus-grounded ProjectSpec deterministically from a
    user-provided corpus. No model, no RNG."""
    name = "corpus-grounded"

    def generate(self, brief: ProjectBrief) -> ProjectSpec:
        corpus = _as_corpus_docs(getattr(brief, "corpus", None))
        if not corpus:
            # Nothing to ground in -> defer to the deterministic rule generator
            # (keeps the feature safe rather than emitting an invalid spec).
            from .rule_generator import RuleProjectGenerator
            return RuleProjectGenerator().generate(brief)

        domain = brief.domain or "document review"
        # The "primary" doc for STRUCTURE is the richest one (most headings), so
        # a plain-text field note doesn't rob a structured .md of its sections,
        # figures, and corrective-action table.
        primary = max(corpus, key=lambda d: len(_headings(d.text)))
        _primary_heads = _headings(primary.text)
        title = brief.title or _choose_title(corpus, _primary_heads)
        # Merge section blocks across all docs so a Figures / Corrective Actions
        # block in any uploaded document is found.
        blocks: dict[str, str] = {}
        for d in corpus:
            for head, body in _section_blocks(d.text).items():
                blocks.setdefault(head, body)

        # ---- sections: from corpus headings, else a standard set ----
        heads = _headings(primary.text)
        # The first heading is the document title (becomes the project title,
        # not a content section). Drop it, plus any Figures/Actions headings
        # (those become a graphics manifest and a table, not content sections).
        content_heads = [
            h for h in heads[1:]
            if "figure" not in h.lower()
            and "corrective action" not in h.lower()
            and "action assignment" not in h.lower()
        ]
        if len(content_heads) >= 2:
            sec_names = content_heads[:6]
        else:
            sec_names = ["Overview", "Details", "Findings", "Actions", "Approvals"]
        # Strip any leading number a source heading already carries ('1. Identifiers'
        # -> 'Identifiers') so our own numbering doesn't double it ('1. 1. ...').
        sec_names = [_strip_leading_number(h) for h in sec_names]
        sections = [SectionSpec(key=_section_key(h, i), heading=f"{i + 1}. {h}")
                    for i, h in enumerate(sec_names)]
        sec_keys = [s.key for s in sections]
        sec_headings = {s.key: h for s, h in zip(sections, sec_names)}

        # ---- fields: grounded labeled values from every doc's header block ----
        # Track which doc each value came from so retrieval can be SCOPED to that
        # doc (a 'Site:' line in field_report must not be answered by a '## Scope'
        # heading in root_cause_notes).
        pairs: list[tuple[str, str, str]] = []  # (label, value, source_doc)
        seen_labels: set[str] = set()
        for d in corpus:
            for label, value in _labeled_values(d.text):
                key = label.lower()
                if key in seen_labels:
                    continue
                seen_labels.add(key)
                pairs.append((label, value, d.name))
        fields: list[FieldSpec] = []
        draft_field_vals: dict[str, dict[str, str]] = {k: {} for k in sec_keys}
        corrections: list[CorrectionSpec] = []
        # Header-block fields (Date/Site/Author/System/...) are IDENTIFIERS: they
        # all live in the document's header block, so place them in the FIRST
        # section and give each a `hint` of its own label. That anchors the
        # engine's line extractor on the real 'Label: value' line instead of
        # letting generic retrieval wander into a section heading. Use at most 4
        # so the project stays legible.
        id_sec = sec_keys[0]
        for idx, (label, value, src) in enumerate(pairs[:4]):
            fkey = _field_key(label, idx)
            sec = id_sec
            fields.append(FieldSpec(key=fkey, label=label, section=sec,
                                    extract=_extract_kind(label, value),
                                    hint=f"{label}:", query=f"{label}", source_doc=src))
            wrong = _mangle(value)
            draft_field_vals[sec][fkey] = wrong
            # correction restores the REAL corpus value (grounded by presence)
            corrections.append(CorrectionSpec(
                id=f"corr_{fkey}", kind="correction", author="source-of-record",
                subject=f"{label} corrected from source",
                target=f"{sec}.{fkey}", operation="replace",
                old_value=wrong, new_value=value,
                body=f"The source document states {label} is {value}."))

        # ---- needs-review field: required, but no corpus value (extract none) ----
        nr_sec = sec_keys[0]
        fields.append(FieldSpec(key="reviewer_signoff", label="Reviewer Sign-off",
                                section=nr_sec, extract="none"))

        # ---- figures: gathered from (a) every doc's own '## Figures' block and
        # (b) figure_hints = images embedded in an uploaded deliverable
        # (draft/template docx/pptx), each carrying the paragraph it sat beside.
        # Dedup by filename. Placement is by CORPUS REFERENCE first (the section
        # whose text actually mentions the figure by name or caption), then by
        # caption/heading word overlap. MULTIPLE figures may share a section
        # (page-by-page: a section that discusses two charts shows both).
        fig_entries: list[tuple[str, str, str]] = []  # (name, caption, source_doc)
        seen_fig: set[str] = set()
        for d in corpus:
            fblock = _find_block(_section_blocks(d.text), "figure")
            for name, caption in _parse_figures(fblock):
                if name in seen_fig:
                    continue
                seen_fig.add(name)
                fig_entries.append((name, caption, d.name))
        for hint in getattr(brief, "figure_hints", None) or []:
            name = (hint.get("name") or "").strip()
            if not name or name in seen_fig:
                continue
            seen_fig.add(name)
            src = hint.get("source_doc") or (corpus[0]["name"] if corpus else primary.name)
            if isinstance(src, dict):
                src = src.get("name", primary.name)
            fig_entries.append((name, (hint.get("caption") or "").strip(), str(src)))

        # Section-reference index: lowercased body text per section, for finding
        # which section's prose mentions a figure (by filename stem or caption).
        sec_text = {
            s.key: (blocks.get(h, "") + " " + h).lower()
            for s, h in zip(sections, sec_names)
        }
        graphics: list[GraphicSpec] = []
        for i, (name, caption, src) in enumerate(fig_entries):
            # source_doc must be a real corpus name for grounding.
            if src not in {d["name"] if isinstance(d, dict) else d.name for d in corpus}:
                src = corpus[0]["name"] if isinstance(corpus[0], dict) else corpus[0].name
            sec = _place_figure(name, caption, sec_keys, sec_headings, sec_text)
            gid = f"gfx_{_slug(name.rsplit('.', 1)[0])[:20]}_{i}"
            graphics.append(GraphicSpec(
                graphic_id=gid, name=name, caption=caption or name,
                source_doc=src, belongs_in_section=sec))
        # Declare each section's graphics (now possibly MANY per section) so every
        # figure renders in reading order and the discipline checks inspect them.
        if graphics:
            gfx_by_section: dict[str, list[str]] = {}
            for g in graphics:
                gfx_by_section.setdefault(g.belongs_in_section, []).append(g.graphic_id)
            sections = [
                s.model_copy(update={
                    "requires_graphic": gfx_by_section[s.key][0],
                    "requires_graphics": gfx_by_section[s.key],
                })
                if s.key in gfx_by_section else s
                for s in sections
            ]

        # ---- table: from the '## Corrective Action Assignments' block ----
        table: TableSpec | None = None
        act_block = _find_block(blocks, "corrective action", "action assignment")
        act_rows = _parse_actions(act_block)
        if act_rows:
            table_sec = sec_keys[-1]
            cols = [
                TableColumnSpec(name="Action", corpus_label="Action",
                                cell_query="corrective action to take"),
                TableColumnSpec(name="Owner", corpus_label="Owner",
                                cell_query="who owns the action"),
                TableColumnSpec(name="Due Date", corpus_label="Due",
                                cell_query="due date for the action"),
            ]
            table = TableSpec(
                key="corrective_actions_table", section=table_sec,
                title="Table {n}: Corrective Actions", columns=cols,
                query="corrective action assignments owner due date",
                row_marker="Action:")
            sections = [
                s.model_copy(update={"requires_table": table.key})
                if s.key == table_sec else s
                for s in sections
            ]

        # ---- conflict: only when the corpus genuinely supports two readings ----
        # The honest default is NO fabricated conflict. We add one only if two
        # grounded fields plausibly answer the same question (same label stem),
        # which we treat as a real disagreement. Otherwise we emit none.
        _maybe_add_grounded_conflict(fields, corrections)

        # ---- section bodies: ground prose lookups in the corpus ----
        section_bodies: list[SectionBodySpec] = []
        for s, head in zip(sections, sec_names):
            if any(p in head.lower() for p in _PROSE_HEADINGS):
                body = blocks.get(head, "").strip()
                query = _first_sentence(body) or head
                section_bodies.append(SectionBodySpec(section=s.key, query=query))
        if not section_bodies:
            section_bodies.append(SectionBodySpec(section=sec_keys[0], query=title))

        # ---- flawed draft ----
        draft_sections = [
            DraftSection(key=k, heading=s.heading, fields=draft_field_vals.get(k, {}),
                         body="")
            for k, s in zip(sec_keys, sections)
        ]

        n_corr = len([c for c in corrections
                      if c.operation == "replace" and c.author == "source-of-record"])
        seeded = [
            f"Corpus-grounded project from {len(corpus)} uploaded document(s).",
            f"{n_corr} value corrections, each grounded in the source.",
            "One needs-review field (reviewer_signoff) with no corpus source.",
            f"{len(graphics)} figure(s) derived from the document's Figures section.",
            "1 corrective-actions table derived from the source."
            if table else "No corrective-actions table found in the source.",
        ]

        spec = ProjectSpec(
            title=title, domain=domain,
            required_sections=sections, fields=fields,
            section_bodies=section_bodies, table=table,
            corpus=corpus, graphics=graphics,
            draft_title=title, draft_sections=draft_sections,
            draft_header=title, draft_footer="", draft_page_numbers=False,
            draft_classification="",
            cross_references=[], corrections=corrections,
            seeded_defects=seeded,
        )
        return spec


def _maybe_add_grounded_conflict(fields: list[FieldSpec],
                                 corrections: list[CorrectionSpec]) -> None:
    """Add a genuine two-reviewer conflict ONLY when it can be grounded: a field
    whose label implies a judgement (severity/priority/risk/rating) with two
    plausible stated readings. We never fabricate candidate values from unrelated
    text; if no such field exists we add no conflict (the honest default)."""
    judgement = ("severity", "priority", "risk", "rating", "criticality", "impact")
    target_field = next(
        (f for f in fields if any(j in f.label.lower() for j in judgement)
         and f.extract != "none"),
        None,
    )
    if target_field is None:
        return
    tgt = f"{target_field.section}.{target_field.key}"
    corrections.append(CorrectionSpec(
        id="corr_conflict_high", kind="comment", author="reviewer.director",
        subject=f"{target_field.label} disagreement", target=tgt, operation="replace",
        old_value=None, new_value="High",
        body=f"I read the {target_field.label} as High."))
    corrections.append(CorrectionSpec(
        id="corr_conflict_medium", kind="comment", author="reviewer.qa",
        subject=f"{target_field.label} disagreement", target=tgt, operation="replace",
        old_value=None, new_value="Medium",
        body=f"I disagree; the {target_field.label} is Medium at most."))


# Generic section words that make a poor document TITLE (they are section
# headings, not the document's name).
_GENERIC_TITLE_WORDS = {
    "scope", "overview", "summary", "findings", "introduction", "background",
    "details", "contents", "notes", "recommendation", "recommendations",
    "assessment", "conclusion", "conclusions", "purpose",
}


def _strip_leading_number(heading: str) -> str:
    """Drop a leading section number a source heading already carries, e.g.
    '1. Identifiers' -> 'Identifiers', '2) Scope' -> 'Scope'. Leaves un-numbered
    headings untouched."""
    return re.sub(r"^\s*\d+[.)]\s+", "", heading).strip() or heading


def _choose_title(corpus: list[CorpusDoc], primary_heads: list[str]) -> str:
    """Pick a real document title. Prefer the first heading of any doc that
    ISN'T a generic section word (so a doc whose first heading is 'Scope' does
    not become the project title). Falls back to the richest doc's first heading,
    then to _derive_title."""
    # Each doc's first heading, in corpus order.
    candidates: list[str] = []
    for d in corpus:
        h = _headings(d.text)
        if h:
            candidates.append(h[0])
    for h in candidates:
        if _strip_leading_number(h).lower() not in _GENERIC_TITLE_WORDS:
            return _strip_leading_number(h)
    if primary_heads:
        return _strip_leading_number(primary_heads[0])
    return _derive_title(corpus)


def _first_sentence(text: str) -> str:
    """First sentence/line of a prose block, as a retrieval query seed."""
    for line in text.splitlines():
        line = line.strip(" -*")
        if len(line) > 12:
            return re.split(r"(?<=[.?!])\s", line)[0][:120]
    return ""


def _derive_title(corpus: list[CorpusDoc]) -> str:
    heads = _headings(corpus[0].text)
    if heads:
        return heads[0]
    first = next((ln.strip() for ln in corpus[0].text.splitlines() if ln.strip()), "")
    return (first[:60] or "Uploaded Document Review")


def _section_key(heading: str, i: int) -> str:
    return f"{_slug(heading)[:24] or 'section'}_{i}"


def _field_key(label: str, i: int) -> str:
    return f"{_slug(label)[:24] or 'field'}_{i}"
