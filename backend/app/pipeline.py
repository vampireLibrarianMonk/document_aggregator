"""The ingestion pipeline.

Stages tracked (and surfaced to the frontend board):

    ingest  -> persist immutable bytes, hash, identity
    parse   -> route to parser, build canonical blocks/artifacts
    chunk   -> structurally-aware chunking with stable chunk IDs
    embed   -> local embedding vectors per chunk
    index   -> write vectors + chunks + manifest (lexical + vector search)

Every stage records started/completed timestamps and a short detail string so
the UI can show exactly where each document is. A parser failure marks its
stage failed but does not take down the service.
"""
from __future__ import annotations

import hashlib
import mimetypes
import uuid

from .config import settings
from .embeddings import embedder
from .models import (
    Artifact,
    Block,
    CanonicalDocument,
    Chunk,
    DocumentRecord,
    Provenance,
    Stage,
    StageStatus,
    TimestampEvidence,
    utcnow,
)
from .parsers import route_and_parse
from .store import store

STAGE_NAMES = ["ingest", "parse", "chunk", "embed", "index"]


def new_document_id() -> str:
    return "doc_" + uuid.uuid4().hex[:12]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def _init_stages() -> list[Stage]:
    return [Stage(name=n) for n in STAGE_NAMES]


def _start(rec: DocumentRecord, name: str, detail: str = "") -> Stage:
    s = rec.stage(name)
    s.status = StageStatus.processing
    s.started_at = utcnow()
    if detail:
        s.detail = detail
    store.save_record(rec)
    return s


def _finish(rec: DocumentRecord, name: str, detail: str, status: StageStatus = StageStatus.completed) -> None:
    s = rec.stage(name)
    s.status = status
    s.completed_at = utcnow()
    if detail:
        s.detail = detail
    store.save_record(rec)


# --------------------------------------------------------------------------
# Timestamp resolution: choose effective DTG deterministically, keep evidence.
# --------------------------------------------------------------------------

def resolve_timestamps(source_meta: dict) -> TimestampEvidence:
    ev = TimestampEvidence()
    ev.created = source_meta.get("created")
    ev.modified = source_meta.get("modified")
    ev.filesystem_mtime = source_meta.get("filesystem_mtime")
    # Deterministic priority: created > modified > filesystem > ingestion.
    for value, src in (
        (ev.created, "office.created"),
        (ev.modified, "office.modified"),
        (ev.filesystem_mtime, "filesystem.mtime"),
    ):
        if value:
            ev.effective_dtg = value
            ev.effective_dtg_source = src
            return ev
    ev.effective_dtg = ev.ingested_at
    ev.effective_dtg_source = "ingestion.fallback"
    return ev


# --------------------------------------------------------------------------
# Structurally-aware chunking with stable IDs.
# --------------------------------------------------------------------------

def chunk_blocks(document_id: str, blocks: list[Block], max_chars: int = 800) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf: list[Block] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        text = "\n".join(b.text for b in buf if b.text)
        # Stable chunk ID derived from content + block ids -> reproducible.
        basis = document_id + "|" + "|".join(b.id for b in buf) + "|" + text
        cid = "chunk_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
        pages = [b.page for b in buf if b.page is not None]
        chunks.append(Chunk(
            chunk_id=cid, document_id=document_id, text=text,
            block_ids=[b.id for b in buf],
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            section_path=buf[0].section_path,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            embedding_model=embedder.model,
        ))
        buf, size = [], 0

    for b in blocks:
        # Headings start a fresh chunk to respect structure.
        if b.type == "heading" and buf:
            flush()
        buf.append(b)
        size += len(b.text)
        if size >= max_chars:
            flush()
    flush()
    return chunks


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def ingest_document(project_id: str, filename: str, data: bytes,
                    source_meta: dict | None = None) -> DocumentRecord:
    source_meta = source_meta or {}
    doc_id = new_document_id()
    mime = guess_mime(filename)
    sha = sha256_hex(data)

    rec = DocumentRecord(
        id=doc_id, project_id=project_id, filename=filename,
        mime_type=mime, sha256=sha, byte_size=len(data), stages=_init_stages(),
    )
    store.save_record(rec)

    # ---- ingest ----
    _start(rec, "ingest", "hashing + persisting immutable original")
    existing = store.find_record_by_hash(project_id, sha)
    store.save_source_bytes(project_id, doc_id, filename, data)
    ts = resolve_timestamps(source_meta)
    rec.effective_dtg = ts.effective_dtg
    rec.effective_dtg_source = ts.effective_dtg_source
    dup_note = f"duplicate of {existing.id}" if existing else "new content"
    _finish(rec, "ingest", f"sha256={sha[:12]}… ({dup_note})")

    # ---- parse ----
    _start(rec, "parse", "routing to parser")
    try:
        result = route_and_parse(filename, mime, data, doc_id)
    except Exception as exc:  # hostile/malformed document isolation
        rec.error = f"parse failed: {exc}"
        _finish(rec, "parse", str(exc), StageStatus.failed)
        for n in ("chunk", "embed", "index"):
            _finish(rec, n, "skipped due to parse failure", StageStatus.skipped)
        return rec

    blocks: list[Block] = result.blocks
    artifacts: list[Artifact] = result.artifacts
    rec.block_count = len(blocks)
    rec.artifact_count = len(artifacts)
    parse_detail = f"{result.parser} v{result.parser_version}: {len(blocks)} blocks, {len(artifacts)} artifacts"
    if result.notes:
        parse_detail += f" — {result.notes}"
    _finish(rec, "parse", parse_detail)

    # Build + persist canonical document
    canonical = CanonicalDocument(
        schema_version=settings.SCHEMA_VERSION,
        document={"id": doc_id, "filename": filename, "sha256": sha,
                  "mime_type": mime, "byte_size": len(data)},
        timestamps=ts,
        metadata={"source_meta": source_meta},
        structure={"blocks": blocks},
        artifacts=artifacts,
        provenance=Provenance(parser=result.parser, parser_version=result.parser_version,
                              method=result.method, source_document_id=doc_id),
        extraction={
            "pipeline_version": settings.PIPELINE_VERSION,
            "components": [{"name": result.parser, "version": result.parser_version}],
            "method": result.method,
            "notes": result.notes,
            "started_at": rec.ingested_at,
            "completed_at": utcnow(),
        },
        revisions=[{"revision": 1, "created_at": utcnow(), "source": "initial_extraction"}],
    )
    store.save_canonical(project_id, doc_id, canonical)

    # ---- chunk ----
    _start(rec, "chunk", "structurally-aware chunking")
    chunks = chunk_blocks(doc_id, blocks)
    rec.chunk_count = len(chunks)
    _finish(rec, "chunk", f"{len(chunks)} chunks (stable IDs)")

    # ---- embed ----
    vectors, chunk_map, _ = store.load_index(project_id)
    if settings.EMBEDDINGS_ENABLED:
        _start(rec, "embed", f"{embedder.model} dim={embedder.dim}")
        for ch in chunks:
            vectors[ch.chunk_id] = embedder.embed(ch.text)
        _finish(rec, "embed", f"embedded {len(chunks)} chunks with {embedder.model}")
    else:
        _finish(rec, "embed", "embeddings disabled", StageStatus.skipped)

    # ---- index ----
    _start(rec, "index", "writing vectors + chunks + manifest")
    for ch in chunks:
        chunk_map[ch.chunk_id] = ch.model_dump()
    manifest = {
        "index_schema_version": settings.SCHEMA_VERSION,
        "embedding": {
            "model": embedder.model, "revision": embedder.revision,
            "dimensions": embedder.dim, "normalized": True,
        },
        "document_count": len(store.list_records(project_id)),
        "chunk_count": len(chunk_map),
        "updated_at": utcnow(),
    }
    store.save_index(project_id, vectors, chunk_map, manifest)
    _finish(rec, "index", f"index now holds {len(chunk_map)} chunks (lexical + vector)")

    return rec
