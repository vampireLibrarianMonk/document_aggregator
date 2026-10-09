"""Generate a project's correction-data store from the user's UPLOADED real
documents.

This is the real user flow: a user drops their standard set of source documents
into the Ingestion tab, and the app DERIVES everything the Correction Pipeline
needs (the manifest, template, a flawed first draft, and grounded corrections)
from that raw material — no hand-authored JSON. It is the deterministic,
offline, corpus-grounded generator (CorpusProjectGenerator) applied to the
uploaded corpus, persisted into the project's own data/ store so the pipeline
reconciles exactly as it would for any project.

Trigger: called after each successful Ingestion upload (best-effort). Because
generation is a pure, fast function of the uploaded text (no model, no network),
regenerating on every upload keeps the project in sync with whatever the user
has uploaded so far. The write is atomic (persist_spec stages + swaps).
"""
from __future__ import annotations

from .config import settings
from .store import store

# Kinds whose uploaded documents carry ground-truth source TEXT the generator
# reads. A user drops the corpus notes under "corpus"; a flawed report under
# "first draft" or "template" is also real source text we ground in. Reviewer
# feedback ("corrections") is NOT corpus — it is handled separately as emails.
_TEXT_KINDS = {"corpus", "first_draft", "template"}
_TEXT_SUFFIXES = {".txt", ".md", ".docx", ".pdf", ".pptx"}
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _suffix(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def gather_corpus(project_id: str) -> list[dict]:
    """Parse uploaded documents into {name, text} corpus entries, in a stable
    order. GROUND-TRUTH corpus comes from the plain-text notes (.md/.txt) — these
    define the project's structure. Structured DELIVERABLES (.docx/.pptx/.pdf,
    which are typically the flawed draft or a template) are NOT used for
    structure (their numbered headings and inline figure captions would pollute
    the derived sections); they are consulted for text ONLY as a fallback when
    no .md/.txt corpus was uploaded. Their embedded figures are handled
    separately (gather_deliverable_figures). Deterministic."""
    from .projectgen.corpus_intake import corpus_from_upload

    text_corpus: list[dict] = []
    deliverable_corpus: list[dict] = []
    seen_names: set[str] = set()
    for rec in sorted(store.list_records(project_id), key=lambda r: (r.filename or "")):
        kind = getattr(rec.kind, "value", rec.kind)
        if kind not in _TEXT_KINDS:
            continue
        suffix = _suffix(rec.filename or "")
        if suffix not in _TEXT_SUFFIXES:
            continue
        data = store.read_source_bytes(project_id, rec.id, rec.filename)
        if not data:
            continue
        try:
            docs = corpus_from_upload(rec.filename or "upload", data)
        except ValueError:
            continue  # no extractable text (e.g. an image-only PDF)
        bucket = text_corpus if suffix in (".txt", ".md") else deliverable_corpus
        for doc in docs:
            if doc.name in seen_names:
                continue
            seen_names.add(doc.name)
            bucket.append({"name": doc.name, "text": doc.text})
    # Plain-text notes are authoritative for structure. Only if there are none
    # do we fall back to a deliverable's text (so a draft-only upload still works)
    return text_corpus or deliverable_corpus


def _copy_uploaded_emails(project_id: str) -> None:
    """Copy uploaded reviewer feedback (kind=corrections, text) into the
    generated project's corrections/emails/ so the figure-relabel grounding
    (load_correction_feedback) can resolve prose figure references. These are
    feedback, not corpus; the value corrections are derived from the corpus.
    Best-effort."""
    dest_dir = settings.project_data_dir(project_id) / "corrections" / "emails"
    i = 0
    for rec in sorted(store.list_records(project_id), key=lambda r: (r.filename or "")):
        kind = getattr(rec.kind, "value", rec.kind)
        if kind != "corrections" or _suffix(rec.filename or "") != ".txt":
            continue
        data = store.read_source_bytes(project_id, rec.id, rec.filename)
        if not data:
            continue
        i += 1
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Normalize to email_<n>.txt (what load_correction_feedback globs).
        (dest_dir / f"email_{i}.txt").write_bytes(data)


def _copy_uploaded_figures(project_id: str) -> None:
    """Copy any uploaded figure images into the generated project's
    corpus/figures/ so the real PNGs are available (the pipeline references them
    by name; this makes the rendered/exported output show the actual images).
    Best-effort."""
    dest_dir = settings.project_data_dir(project_id) / "corpus" / "figures"
    for rec in store.list_records(project_id):
        if _suffix(rec.filename or "") not in _FIGURE_SUFFIXES:
            continue
        data = store.read_source_bytes(project_id, rec.id, rec.filename)
        if not data:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (rec.filename or rec.id)).write_bytes(data)


_DELIVERABLE_SUFFIXES = {".docx", ".pptx"}


def gather_deliverable_figures(project_id: str) -> tuple[list[dict], dict[str, bytes]]:
    """Extract embedded images (with paragraph anchors) from uploaded
    deliverables (docx/pptx). Returns (hints, image_bytes):
      hints: [{name, caption, source_doc, anchor_text}] — one per embedded image,
             where anchor_text is the paragraph the image sits beside (its
             caption), so the generator can place the figure next to that text.
      image_bytes: {name -> bytes} for writing the real PNGs to the store.
    Deterministic + best-effort; a parse failure yields no hints for that doc."""
    from .parsers import route_and_parse
    from .pipeline import guess_mime

    hints: list[dict] = []
    image_bytes: dict[str, bytes] = {}
    seen: set[str] = set()
    for rec in sorted(store.list_records(project_id), key=lambda r: (r.filename or "")):
        name = rec.filename or ""
        if _suffix(name) not in _DELIVERABLE_SUFFIXES:
            continue
        data = store.read_source_bytes(project_id, rec.id, name)
        if not data:
            continue
        try:
            result = route_and_parse(name, guess_mime(name), data, doc_id="deliverable")
        except Exception:  # noqa: BLE001
            continue
        blocks_by_id = {b.id: b for b in result.blocks}
        for art in result.artifacts:
            if art.type != "image":
                continue
            img_name = art.metadata.get("image_name") or f"{rec.id}_{art.id}.png"
            if img_name in seen:
                continue
            seen.add(img_name)
            if img_name in result.images:
                image_bytes[img_name] = result.images[img_name]
            anchor = blocks_by_id.get(art.anchor_block_id or "")
            anchor_text = (anchor.text if anchor else "") or art.description or ""
            hints.append({
                "name": img_name,
                "caption": anchor_text.strip(),
                "source_doc": name,
                "anchor_text": anchor_text.strip(),
            })
    return hints, image_bytes


def regenerate_from_uploads(project_id: str) -> dict:
    """(Re)generate the project's correction-data store from the uploaded
    documents. Returns a small status dict. Never raises: generation is additive
    to ingestion and must never fail the upload response.

    Shape:
      {generated: bool, corpus_docs: [names], reason?: str}
    """
    try:
        corpus = gather_corpus(project_id)
    except Exception as exc:  # noqa: BLE001
        return {"generated": False, "corpus_docs": [], "reason": f"gather failed: {exc}"}

    if not corpus:
        return {"generated": False, "corpus_docs": [],
                "reason": "no source text uploaded yet"}

    # Figures embedded in an uploaded deliverable (draft/template docx/pptx),
    # with the paragraph they were anchored to (their caption).
    try:
        fig_hints, deliverable_images = gather_deliverable_figures(project_id)
    except Exception:  # noqa: BLE001
        fig_hints, deliverable_images = [], {}

    try:
        from .projectgen.corpus_generator import CorpusProjectGenerator
        from .projectgen.generator import ProjectBrief
        from .projectgen.persist import persist_spec
        from .projectgen.schema import validate_spec

        spec = CorpusProjectGenerator().generate(
            ProjectBrief(corpus=corpus, figure_hints=fig_hints))
        problems = validate_spec(spec)
        if problems:
            return {"generated": False, "corpus_docs": [d["name"] for d in corpus],
                    "reason": "generated spec invalid: " + "; ".join(problems)}
        # Persist into THIS project's own id (overwrites data/ atomically).
        persist_spec(spec, project_id=project_id, build_assets=True)
        _copy_uploaded_figures(project_id)
        _write_deliverable_images(project_id, deliverable_images)
        _copy_uploaded_emails(project_id)
    except Exception as exc:  # noqa: BLE001
        return {"generated": False, "corpus_docs": [d["name"] for d in corpus],
                "reason": f"generation failed: {exc}"}

    return {"generated": True, "corpus_docs": [d["name"] for d in corpus]}


def _write_deliverable_images(project_id: str, images: dict[str, bytes]) -> None:
    """Write images extracted from a deliverable (docx/pptx) into the project's
    corpus/figures/ so they can be served + embedded. Best-effort."""
    if not images:
        return
    dest_dir = settings.project_data_dir(project_id) / "corpus" / "figures"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in images.items():
        try:
            (dest_dir / name).write_bytes(blob)
        except Exception:  # noqa: BLE001
            continue


def ingest_generation_ready(project_id: str) -> dict:
    """Readiness for the UPLOAD-and-GENERATE flow: the pipeline is ready once the
    project has been generated from the user's uploaded documents (i.e. a
    corpus-grounded project.json exists in the data/ store). The only real
    prerequisite is 'upload at least one source document'.

    Returns {ready, has_corpus, generated, problems}.
    """
    data_dir = settings.project_data_dir(project_id)
    generated = (data_dir / "project.json").exists()
    corpus_dir = data_dir / "corpus"
    has_corpus = corpus_dir.is_dir() and any(
        p.suffix in (".txt", ".md") for p in corpus_dir.iterdir()
    )
    problems: list[str] = []
    if not generated or not has_corpus:
        problems.append(
            "Upload your source documents (the corpus notes / report). The app "
            "builds the project from them automatically."
        )
    return {
        "ready": generated and has_corpus,
        "has_corpus": has_corpus,
        "generated": generated,
        "problems": problems,
    }
