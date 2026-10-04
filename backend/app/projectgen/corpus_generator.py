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

Shape of the generated project (same contract as RuleProjectGenerator):
  - corpus:      the user's documents, used verbatim.
  - sections:    derived from the corpus headings (or a standard fallback set).
  - fields:      labeled values discovered in the corpus (grounded) + one
                 needs-review field (a required value with no corpus source).
  - draft:       a flawed first attempt that mangles the grounded values.
  - corrections: each restores the real corpus value (grounded), plus one
                 conflict (two corrections on one target, both stated in-body).
"""
from __future__ import annotations

import re

from .generator import ProjectBrief
from .schema import (
    CorpusDoc,
    CorrectionSpec,
    DraftSection,
    FieldSpec,
    ProjectSpec,
    SectionBodySpec,
    SectionSpec,
)

_LABEL_RE = re.compile(r"^\s*([A-Za-z][\w /-]{1,40}?)\s*[:=]\s*(.+?)\s*$")
_HEADING_MD = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


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


def _labeled_values(text: str) -> list[tuple[str, str]]:
    """Extract deterministic (label, value) pairs from 'Label: value' lines.
    Ordered by appearance; values are verbatim substrings of the corpus."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            label, value = m.group(1).strip(), m.group(2).strip()
            # keep short, single-value lines (avoid grabbing prose sentences)
            if value and len(value) <= 80 and label.lower() not in {"http", "https"}:
                pairs.append((label, value))
    return pairs


def _headings(text: str) -> list[str]:
    """Markdown-style headings in order (deterministic)."""
    return [m.group(1).strip() for line in text.splitlines()
            if (m := _HEADING_MD.match(line))]


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

        title = brief.title or _derive_title(corpus)
        domain = brief.domain or "document review"
        primary = corpus[0]

        # ---- sections: from corpus headings, else a standard set ----
        heads = _headings(primary.text)
        if len(heads) >= 2:
            sec_names = heads[:6]
        else:
            sec_names = ["Overview", "Details", "Findings", "Actions", "Approvals"]
        sections = [SectionSpec(key=_section_key(h, i), heading=f"{i + 1}. {h}")
                    for i, h in enumerate(sec_names)]
        sec_keys = [s.key for s in sections]

        # ---- fields: grounded labeled values, distributed across sections ----
        pairs = _labeled_values(primary.text)
        fields: list[FieldSpec] = []
        draft_field_vals: dict[str, dict[str, str]] = {k: {} for k in sec_keys}
        corrections: list[CorrectionSpec] = []
        # Use at most 4 grounded fields so the project stays legible.
        for idx, (label, value) in enumerate(pairs[:4]):
            fkey = _field_key(label, idx)
            sec = sec_keys[idx % len(sec_keys)]
            fields.append(FieldSpec(key=fkey, label=label, section=sec, extract="line"))
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

        # ---- conflict: two corrections on one target, both stated in-body ----
        if fields:
            # Reuse the first grounded field's target for a reviewer disagreement.
            conflict_target = f"{fields[0].section}.{fields[0].key}"
            alt = corpus[0].text.strip().splitlines()[0][:60] if corpus[0].text.strip() else title
            corrections.append(CorrectionSpec(
                id="corr_conflict_a", kind="comment", author="reviewer.a",
                subject="value disagreement", target=conflict_target, operation="replace",
                old_value=None, new_value=fields[0].label,
                body=f"I read this as {fields[0].label}."))
            corrections.append(CorrectionSpec(
                id="corr_conflict_b", kind="comment", author="reviewer.b",
                subject="value disagreement", target=conflict_target, operation="replace",
                old_value=None, new_value=alt,
                body=f"I disagree; the correct reading is {alt}."))

        # ---- section bodies: ground prose lookups in the corpus ----
        section_bodies = [
            SectionBodySpec(section=sec_keys[0], query=title),
        ]

        # ---- flawed draft ----
        draft_sections = [
            DraftSection(key=k, heading=s.heading, fields=draft_field_vals.get(k, {}),
                         body="")
            for k, s in zip(sec_keys, sections)
        ]

        seeded = [
            f"Corpus-grounded project from {len(corpus)} uploaded document(s).",
            f"{len([c for c in corrections if c.id.startswith('corr_') and c.operation == 'replace'])} "
            "value corrections, each grounded in the source.",
            "One needs-review field (reviewer_signoff) with no corpus source.",
            "One conflict (two reviewers disagree on the lead field).",
        ]

        spec = ProjectSpec(
            title=title, domain=domain,
            required_sections=sections, fields=fields,
            section_bodies=section_bodies, table=None,
            corpus=corpus, graphics=[],
            draft_title=title, draft_sections=draft_sections,
            draft_header=title, draft_footer="", draft_page_numbers=False,
            draft_classification="",
            cross_references=[], corrections=corrections,
            seeded_defects=seeded,
        )
        return spec


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
