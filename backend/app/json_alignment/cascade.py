"""Files-only auto-approve cascade (Part 2, design §2.2–§2.3).

Given a single provisional profile `P` the human is about to approve, answer —
truthfully, deterministically, and WITHOUT any inference — how many OTHER files
(files in clusters other than P's own) become fully conformant *solely* because
P is approved.

The cascade is a pure reduction over the existing router + conformance seams:

  routing      decide_pathway(cluster, schema, library_view,
                              reject_below=…, research_available=False)
  conformance  run_alignment(records, schema, profile=<frozen P mapping>)
                              .conformant_flags()

Guardrails that make this safe (all enforced below):
  * the ONLY mutation is `library_post = approve(deepcopy(library), P)` — a
    single-shape promotion on a COPY; the persisted library is never touched
    during a read-only preview;
  * routing always pins `research_available=False` so the cascade can only ever
    reach the frozen-replay branch it counts (never novel_research/drift_repair
    inference, never the semantic tier);
  * conformance ALWAYS supplies a frozen `profile=` — never `profile=None`,
    never `use_semantic=True` — so there is no inference and no I/O beyond the
    persisted ledger/library/schema reads; this is what makes preview-N ==
    apply-N on unchanged state (determinism, NFR2.3);
  * conformance is judged against `effective_target_schema` (the value gate);
  * a file is counted iff `post_flips AND NOT baseline_conformant`, which bounds
    the vocab-removal over-count edge (design §2.2 step 4).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from .batch import (
    PATHWAY_REPLAY,
    Cluster,
    cluster_documents,
    decide_pathway,
)
from .batch import SourceDoc as _SourceDoc
from .pipeline import run_alignment
from .profile_store import ProfileLibrary

log = logging.getLogger(__name__)

# The cascade's routing reject_below mirrors the batch default (op_batch_align
# uses payload.get("reject_below", 0.5)); the only pathway the cascade counts is
# an approved-profile replay, which decide_pathway decides before the relevance
# reject gate, so this value does not change N.
_DEFAULT_REJECT_BELOW = 0.5


def schema_fingerprint(schema: dict[str, Any]) -> str:
    """sha256 hex of the canonical JSON of `schema` (sorted keys). A stable
    telemetry signal that the effective schema (incl. learned vocabulary) changed
    since the batch; it does NOT gate the count (design §2.2 step 1)."""
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@dataclass
class CascadeResult:
    """The outcome of a cascade count: how many OTHER files flip on approving P."""
    n: int
    doc_ids: list[str]
    schema_fingerprint: str


def _cluster_for_file(doc_id: str, records: list[dict[str, Any]],
                      descriptions: dict[str, str]) -> Cluster:
    """Rebuild a one-file Cluster from a ledger entry exactly the way
    cluster_documents builds a cluster for a single doc (same signature)."""
    clusters = cluster_documents([
        _SourceDoc(doc_id=doc_id, records=list(records),
                   descriptions=dict(descriptions or {}))
    ])
    # A single doc always yields exactly one cluster.
    return clusters[0]


def _replay_conformant(records: list[dict[str, Any]], schema: dict[str, Any],
                       profile) -> bool:
    """True iff EVERY record strictly conforms when replayed through `profile`'s
    frozen mapping. Never calls run_alignment with profile=None. A raise is
    propagated to the caller, which decides how to treat it."""
    if not records:
        return False
    res = run_alignment(records, schema, profile=profile)
    flags = res.conformant_flags()
    return bool(flags) and all(flags)


def cascade_count(project_id: str, approved_profile_id: str,
                  library: ProfileLibrary, schema: dict[str, Any] | None,
                  ) -> CascadeResult:
    """Count the OTHER files that become fully conformant solely because
    `approved_profile_id` is approved (design §2.2).

    `schema` is the CURRENT effective_target_schema (the value gate). `library`
    is the persisted library; it is deep-copied internally and never mutated.
    Pure and deterministic: same persisted state -> same n and doc_ids.
    """
    from .project_store import load_pending_ledger

    # None-schema graceful degrade (design §2.2 step 1 / Finding 5): no schema
    # saved -> n=0, never fingerprint(None) / run_alignment(..., None, ...).
    if schema is None:
        return CascadeResult(n=0, doc_ids=[], schema_fingerprint="")

    fp = schema_fingerprint(schema)

    ledger = load_pending_ledger(project_id)
    if not ledger:
        # Ledger absent (old project / best-effort write failed) -> graceful
        # degrade: nothing to cascade (design §2.7 "ledger absent", NIT-2).
        return CascadeResult(n=0, doc_ids=[], schema_fingerprint=fp)

    files = ledger.get("files", []) or []
    if approved_profile_id not in library.entries:
        # Caller (endpoint) 404s unknown ids before mutation; here, defensively,
        # an unknown id flips nothing.
        return CascadeResult(n=0, doc_ids=[], schema_fingerprint=fp)

    # Two library VIEWS: baseline = library as persisted; post = a deep copy on
    # which ONLY `approved_profile_id` is flipped to approved. The sole mutation
    # in the whole computation — the single-shape promotion guarantee (§2.2 step
    # 2): library_post.signatures(approved_only=True) differs from baseline by
    # exactly this one id.
    library_baseline = copy.deepcopy(library)
    library_post = copy.deepcopy(library)
    library_post.approve(approved_profile_id)

    # Exclusion set (§2.2 step 3 / NIT-2): P's own producing cluster(s). A file
    # whose own cluster PRODUCED P is a direct producer, not an "OTHER file"; it
    # was already emitted by the batch. Derived from the ledger cluster_key —
    # never the profile's capped source_docs meta. If NO ledger file produced P,
    # P_cluster_keys is empty -> exclusion set is empty (safe, NIT-2).
    p_cluster_keys = {
        f.get("cluster_key")
        for f in files
        if f.get("produced_cluster_profile") == approved_profile_id
    }
    p_cluster_keys.discard(None)
    excluded = {
        f.get("doc_id")
        for f in files
        if f.get("cluster_key") in p_cluster_keys
    }

    counted: list[str] = []
    for f in files:
        doc_id = f.get("doc_id")
        if doc_id in excluded:
            continue
        records = f.get("records", []) or []
        descriptions = f.get("descriptions", {}) or {}
        cluster = _cluster_for_file(doc_id, records, descriptions)

        try:
            # Baseline routing/conformance under the CURRENT schema (status is
            # advisory only; always recomputed — §2.2 step 1, Finding 2).
            d0 = decide_pathway(cluster, schema, library_baseline,
                                reject_below=_DEFAULT_REJECT_BELOW,
                                research_available=False)
            if d0.pathway == PATHWAY_REPLAY and d0.matched_profile:
                entry0 = library_baseline.get(d0.matched_profile)
                baseline_conformant = (
                    entry0 is not None
                    and _replay_conformant(records, schema, entry0.profile)
                )
            else:
                # drift_repair/review/reject -> not conformant BY DEFINITION;
                # no run_alignment with profile=None, no inference (Finding 2).
                baseline_conformant = False

            # Post-approval routing/conformance.
            d1 = decide_pathway(cluster, schema, library_post,
                                reject_below=_DEFAULT_REJECT_BELOW,
                                research_available=False)
            post_entry = library_post.get(approved_profile_id)
            post_flips = (
                d1.pathway == PATHWAY_REPLAY
                and d1.matched_profile == approved_profile_id
                and post_entry is not None
                and _replay_conformant(records, schema, post_entry.profile)
            )
        except Exception:  # noqa: BLE001 - a file we cannot re-evaluate is not
            # provably conformant; exclude it and keep going (design §2.9).
            log.warning("cascade: run_alignment failed for doc_id=%s "
                        "(project_id=%s); excluding", doc_id, project_id,
                        exc_info=True)
            continue

        # Count iff the single P-promotion is what flips the file, and it was not
        # already conformant at baseline (§2.2 step 4). The NOT baseline term
        # bounds the vocab-removal over-count edge.
        if post_flips and not baseline_conformant:
            counted.append(doc_id)

    counted = sorted(counted)
    return CascadeResult(n=len(counted), doc_ids=counted, schema_fingerprint=fp)


__all__ = [
    "CascadeResult",
    "cascade_count",
    "schema_fingerprint",
]
