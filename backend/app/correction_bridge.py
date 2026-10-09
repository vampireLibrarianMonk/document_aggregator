"""Bridge uploaded Ingestion documents into the correction-data store.

The reconcile engine reads a project's correction data from
`DATA_DIR/projects/<id>/data/` in a fixed layout (project.py loaders):

    data/project.json                               the manifest (fields/sections/table)
    data/corpus/<name>.txt|.md                      ground-truth source text
    data/corpus/graphics.json                       named figure manifest
    data/template/incident_report_template.json     the blank rubric
    data/first_attempt/incident_report_draft.json   the flawed draft
    data/corrections/comments.json                  reviewer feedback (structured)
    data/corrections/emails/email_*.txt             raw reviewer feedback (optional)

Historically ONLY sample instantiation (copytree) or the project generator wrote
this tree, so the Correction Pipeline worked only for those. Plain Ingestion
uploads landed in a SEPARATE store (source/documents/index) that the engine never
reads — so an upload-built project showed an empty pipeline.

This module closes that gap (Option B): when a user uploads a scenario folder's
files, each is ALSO persisted here in the shape the engine expects, routed by
upload `kind` + filename. It is purely additive — the ingestion store writes
(search/preview) are untouched. Deterministic and offline.
"""
from __future__ import annotations

from pathlib import Path

from .config import settings

# Canonical destination filenames the engine's loaders look for.
_TEMPLATE_NAME = "incident_report_template.json"
_DRAFT_NAME = "incident_report_draft.json"
_CORRECTIONS_NAME = "comments.json"

# Filenames that carry a fixed role regardless of which area they are dropped in.
_MANIFEST_FILES = {"project.json"}
_GRAPHICS_FILES = {"graphics.json"}

# Corpus text we store verbatim (what load_corpus reads).
_CORPUS_TEXT_SUFFIXES = {".txt", ".md"}


def _data_dir(project_id: str) -> Path:
    """The correction-data root the engine reads. For an upload-built project
    this is DATA_DIR/projects/<id>/data/ (a brand-new id resolves there)."""
    return settings.project_data_dir(project_id)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def bridge_upload(project_id: str, kind: str, filename: str, data: bytes) -> str | None:
    """Persist one uploaded file into the correction-data store in the engine's
    layout. Returns the relative destination path written (for logging/tests),
    or None if the file has no correction-store role (e.g. a binary figure, which
    the engine references by name via graphics.json, not by raw bytes).

    Routing (filename takes precedence over area, since manifest/graphics carry a
    fixed role; otherwise the upload `kind`/area decides):
      - project.json              -> data/project.json              (the manifest)
      - graphics.json             -> data/corpus/graphics.json
      - kind=template  + .json    -> data/template/incident_report_template.json
      - kind=first_draft + .json  -> data/first_attempt/incident_report_draft.json
      - kind=corrections + .json  -> data/corrections/comments.json
      - kind=corrections + email_*.txt -> data/corrections/emails/<name>
      - kind=corpus    + .txt/.md -> data/corpus/<name>
      - anything else             -> None (not part of the correction input)
    """
    name = Path(filename).name
    lower = name.lower()
    suffix = Path(lower).suffix
    root = _data_dir(project_id)

    # Fixed-role files first, regardless of the area chosen.
    if lower in _MANIFEST_FILES:
        _write_bytes(root / "project.json", data)
        return "project.json"
    if lower in _GRAPHICS_FILES:
        _write_bytes(root / "corpus" / "graphics.json", data)
        return "corpus/graphics.json"

    if kind == "template" and suffix == ".json":
        _write_bytes(root / "template" / _TEMPLATE_NAME, data)
        return f"template/{_TEMPLATE_NAME}"

    if kind == "first_draft" and suffix == ".json":
        _write_bytes(root / "first_attempt" / _DRAFT_NAME, data)
        return f"first_attempt/{_DRAFT_NAME}"

    if kind == "corrections":
        if lower.startswith("email_") and suffix == ".txt":
            _write_bytes(root / "corrections" / "emails" / name, data)
            return f"corrections/emails/{name}"
        if suffix == ".json":
            _write_bytes(root / "corrections" / _CORRECTIONS_NAME, data)
            return f"corrections/{_CORRECTIONS_NAME}"

    if kind == "corpus" and suffix in _CORPUS_TEXT_SUFFIXES:
        _write_bytes(root / "corpus" / name, data)
        return f"corpus/{name}"

    # A corpus .json that is neither the manifest nor graphics is ambiguous; a
    # binary figure (png/jpg) is referenced via graphics.json, not stored as
    # corpus text. Neither feeds the engine directly.
    return None


def correction_store_ready(project_id: str) -> dict:
    """Report whether the correction-data store has what the engine needs to run,
    independent of the ingestion DocumentRecord tags. The two valid shapes are:
      A) manifest + template   + corpus + corrections
      B) manifest + first_draft + corpus + corrections
    Returns {ready, has_manifest, has_template, has_first_draft, has_corpus,
    has_corrections, problems}.
    """
    root = _data_dir(project_id)
    has_manifest = (root / "project.json").exists()
    has_template = (root / "template" / _TEMPLATE_NAME).exists()
    has_first_draft = (root / "first_attempt" / _DRAFT_NAME).exists()
    corpus_dir = root / "corpus"
    has_corpus = corpus_dir.is_dir() and any(
        p.suffix in _CORPUS_TEXT_SUFFIXES for p in corpus_dir.iterdir()
    )
    has_corrections = (root / "corrections" / _CORRECTIONS_NAME).exists()

    problems: list[str] = []
    if not has_manifest:
        problems.append("Upload the project manifest (project.json).")
    if not has_corpus:
        problems.append("Upload the original corpus (at least one .txt/.md).")
    if not (has_template or has_first_draft):
        problems.append("Upload a template or a first draft (at least one).")
    if not has_corrections:
        problems.append("Upload corrections (comments.json).")

    return {
        "ready": not problems,
        "has_manifest": has_manifest,
        "has_template": has_template,
        "has_first_draft": has_first_draft,
        "has_corpus": has_corpus,
        "has_corrections": has_corrections,
        "problems": problems,
    }
