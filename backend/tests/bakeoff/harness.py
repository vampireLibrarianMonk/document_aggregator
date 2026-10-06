"""Bake-off harness: raw-input assembly, the gold standard, and scoring.

RAW INPUTS (what a user actually uploads) are assembled from each sample project
without using its hand-authored structured JSON manifest/template/draft:
  - corpus:      the raw .txt/.md files under corpus/           (as-is)
  - template:    the raw template document (template/generated/template.docx)
  - first_draft: the raw draft document (first_attempt/generated/draft.docx)
  - corrections: the raw reviewer emails (corrections/emails/email_*.txt)

GOLD STANDARD is the current engine's output on the STRUCTURED inputs
(project.run_reconciliation), i.e. the known-correct corrected report. Each
approach is scored by how well its raw-upload output reproduces the gold field
values + statuses, plus no-fabrication, conflict preservation, and determinism.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app import project as sc
from app.config import settings

PROJECT_IDS = ["1", "2", "3", "4", "5", "6"]


# --------------------------------------------------------------------------
# Raw inputs (what the user uploads)
# --------------------------------------------------------------------------

@dataclass
class RawInputs:
    """The raw materials a user would upload, with NO structured JSON."""
    project_id: str
    corpus: dict[str, str]                 # filename -> text (as uploaded)
    corrections_emails: list[str]          # raw email text, one per message
    template_docx: bytes | None            # raw template document bytes
    draft_docx: bytes | None               # raw first-draft document bytes
    # Convenience: the corpus as one blob (grounding checks).
    @property
    def corpus_blob(self) -> str:
        return "\n".join(self.corpus.values())


def _case_dir(project_id: str) -> Path:
    return settings.resolve_project_data_dir(project_id)


def load_raw_inputs(project_id: str) -> RawInputs:
    d = _case_dir(project_id)
    # Corpus: raw text files only (what a user drops into the corpus area).
    corpus: dict[str, str] = {}
    cdir = d / "corpus"
    if cdir.is_dir():
        for p in sorted(cdir.iterdir()):
            if p.suffix in (".txt", ".md"):
                corpus[p.name] = p.read_text(encoding="utf-8")
    # Corrections: raw emails.
    emails: list[str] = []
    edir = d / "corrections" / "emails"
    if edir.is_dir():
        for p in sorted(edir.glob("email_*.txt")):
            emails.append(p.read_text(encoding="utf-8"))
    # Template + draft documents (raw bytes).
    tdoc = d / "template" / "generated" / "template.docx"
    ddoc = d / "first_attempt" / "generated" / "draft.docx"
    return RawInputs(
        project_id=project_id,
        corpus=corpus,
        corrections_emails=emails,
        template_docx=tdoc.read_bytes() if tdoc.exists() else None,
        draft_docx=ddoc.read_bytes() if ddoc.exists() else None,
    )


# --------------------------------------------------------------------------
# Gold standard
# --------------------------------------------------------------------------

def gold_report(project_id: str) -> dict:
    """The known-correct corrected report: the current deterministic engine on
    the hand-authored STRUCTURED inputs."""
    return sc.run_reconciliation("draft", project_id, "json")


# --------------------------------------------------------------------------
# Report flattening + scoring
# --------------------------------------------------------------------------

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def flatten_fields(report: dict) -> dict[str, dict]:
    """Map unit-key -> {value, status} across sections + furniture, so two
    reports can be compared unit-by-unit."""
    out: dict[str, dict] = {}
    for sec in report.get("sections", []):
        for f in sec.get("fields", []):
            out[f["key"]] = {"value": f.get("value"), "status": f.get("status")}
        for g in sec.get("graphics", []):
            out[f"{sec['key']}.graphic:{g.get('name', '')}"] = {
                "value": g.get("name"), "status": g.get("status")}
    for f in report.get("furniture", {}).get("elements", []):
        out[f["key"]] = {"value": f.get("value"), "status": f.get("status")}
    return out


@dataclass
class Score:
    project_id: str
    approach: str
    # correctness vs gold
    gold_units: int = 0
    value_matches: int = 0           # value equals gold (normalized)
    status_matches: int = 0          # status equals gold
    # safety
    fabrications: int = 0            # emitted values not grounded in corpus/corrections
    conflict_preserved: bool = False # the known severity/approval conflict kept as 'conflict'
    # engineering
    deterministic: bool = True
    offline_capable: bool = True
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def value_pct(self) -> float:
        return (100.0 * self.value_matches / self.gold_units) if self.gold_units else 0.0

    @property
    def status_pct(self) -> float:
        return (100.0 * self.status_matches / self.gold_units) if self.gold_units else 0.0


def score_against_gold(project_id: str, approach: str, produced: dict,
                       raw: RawInputs, deterministic: bool = True,
                       offline_capable: bool = True) -> Score:
    """Score a produced corrected report against the gold standard."""
    s = Score(project_id=project_id, approach=approach,
              deterministic=deterministic, offline_capable=offline_capable)
    if not produced:
        s.error = "no report produced"
        return s
    gold = flatten_fields(gold_report(project_id))
    got = flatten_fields(produced)
    s.gold_units = len(gold)

    for key, g in gold.items():
        p = got.get(key)
        if p is None:
            continue
        if _norm(p.get("value")) == _norm(g.get("value")):
            s.value_matches += 1
        if p.get("status") == g.get("status"):
            s.status_matches += 1

    # No fabrication: every emitted non-empty value must appear in corpus,
    # in a correction email, or match a gold value (grounded).
    grounding = _norm(raw.corpus_blob) + " " + _norm(" ".join(raw.corrections_emails))
    gold_values = {_norm(v.get("value")) for v in gold.values() if v.get("value")}
    for key, p in got.items():
        val = _norm(p.get("value"))
        if not val or p.get("status") in ("needs_review", "conflict"):
            continue
        if val not in grounding and val not in gold_values:
            s.fabrications += 1

    # Conflict preserved: at least one unit that is 'conflict' in gold is also
    # 'conflict' here (the deliberate disagreement must not be silently picked).
    gold_conflicts = {k for k, v in gold.items() if v.get("status") == "conflict"}
    if gold_conflicts:
        s.conflict_preserved = any(
            got.get(k, {}).get("status") == "conflict" for k in gold_conflicts)
    else:
        s.conflict_preserved = True  # nothing to preserve
    return s
