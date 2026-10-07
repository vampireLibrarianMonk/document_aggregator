"""Batch clustering + per-cluster pathway decision.

The thousands-across-teams workload: many JSON documents arrive, of a FEW
distinct shapes, all meant to conform to one golden schema. We don't decide one
document at a time — we CLUSTER by shape, then decide a single pathway PER
CLUSTER (the user-confirmed granularity), because a thousand files of the same
shape share one decision and one human approval.

Pathways a cluster can be routed to:

  replay_clean      HIGH-matches an APPROVED profile and shows no drift ->
                    replay the frozen mapping deterministically (cheap, async).
  drift_repair      matched an approved profile but drift was detected ->
                    re-emerge into research on the BROKEN fields only.
  novel_research    relevant to the golden but no approved profile matches ->
                    infer a PROVISIONAL profile for one-time human approval.
  review            relevance landed in the middle band -> a human confirms the
                    cluster is actually ours before converting.
  reject_irrelevant relevance rejects AND no research pathway can plausibly help
                    (nothing of the required core maps, no model tier) ->
                    quarantine with reasons. Nothing is ever force-converted.

This module makes the DECISION and summarizes it (BatchReport). It does not run
conversions or call the Coordinator — that is the BatchAlignmentAgent
(coordinator phase), which consumes these decisions. Keeping the policy pure
makes it deterministic and testable in isolation.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .drift import DriftReport, FillBaseline, assess_drift, fill_rates
from .executor import execute_mapping
from .mapping import infer_mapping
from .profile_store import (
    ProfileLibrary,
)
from .relevance import (
    VERDICT_CONVERTIBLE,
    VERDICT_REVIEW,
    RelevanceVerdict,
    assess_relevance,
)
from .signature import (
    BAND_HIGH,
    BAND_MODERATE,
    Signature,
    best_match,
    source_signature,
)
from .source_profile import profile_source
from .target_profile import TargetSchema, extract_target

# Pathway labels.
PATHWAY_REPLAY = "replay_clean"
PATHWAY_DRIFT_REPAIR = "drift_repair"
PATHWAY_NOVEL = "novel_research"
PATHWAY_REVIEW = "review"
PATHWAY_REJECT = "reject_irrelevant"


@dataclass
class SourceDoc:
    """One incoming document: an id plus its records (one file may hold many)."""
    doc_id: str
    records: list[dict[str, Any]]
    descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class Cluster:
    """A group of documents that share one source shape (signature hash)."""
    key: str                                  # signature hash
    signature: Signature
    doc_ids: list[str]
    records: list[dict[str, Any]]             # all records across the cluster's docs
    descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def doc_count(self) -> int:
        return len(self.doc_ids)

    @property
    def record_count(self) -> int:
        return len(self.records)


def cluster_documents(docs: list[SourceDoc]) -> list[Cluster]:
    """Group documents by their source-shape signature hash.

    Deterministic: clusters are returned in first-seen order of their hash, and
    doc ids within a cluster preserve input order."""
    groups: OrderedDict[str, Cluster] = OrderedDict()
    for doc in docs:
        sig = source_signature(profile_source(doc.records, doc.descriptions or None))
        c = groups.get(sig.hash)
        if c is None:
            groups[sig.hash] = Cluster(
                key=sig.hash, signature=sig, doc_ids=[doc.doc_id],
                records=list(doc.records), descriptions=dict(doc.descriptions),
            )
        else:
            c.doc_ids.append(doc.doc_id)
            c.records.extend(doc.records)
            # merge descriptions (same shape -> same keys; last-writer is fine)
            c.descriptions.update(doc.descriptions)
    return list(groups.values())


@dataclass
class ClusterDecision:
    """The pathway chosen for one cluster, with the evidence behind it."""
    cluster_key: str
    pathway: str
    doc_ids: tuple[str, ...]
    record_count: int
    matched_profile: str | None = None          # library profile id, if any
    match_band: str = ""
    relevance: RelevanceVerdict | None = None
    drift: DriftReport | None = None
    broken_fields: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_key": self.cluster_key,
            "pathway": self.pathway,
            "doc_ids": list(self.doc_ids),
            "record_count": self.record_count,
            "matched_profile": self.matched_profile,
            "match_band": self.match_band,
            "relevance": self.relevance.to_dict() if self.relevance else None,
            "drift": self.drift.to_dict() if self.drift else None,
            "broken_fields": list(self.broken_fields),
            "reasons": list(self.reasons),
        }


def decide_pathway(cluster: Cluster, target_schema: dict[str, Any],
                   library: ProfileLibrary, *, reject_below: float,
                   research_available: bool = True) -> ClusterDecision:
    """Decide a single pathway for one cluster.

    Policy order:
      1. If an APPROVED profile HIGH-matches the shape: check drift. No drift ->
         replay_clean; drift -> drift_repair (broken fields only).
      2. Else assess relevance against the golden schema:
         - convertible -> novel_research (infer a provisional profile) when a
           research pathway exists; otherwise it can't be converted yet.
         - review -> review (human confirms).
         - reject -> novel_research IF a research pathway (model tier) might
           still recover it; otherwise reject_irrelevant (quarantine).
    `research_available` reflects whether a non-deterministic recovery path
    (model tier) is enabled; when False, a deterministic reject is final."""
    target = extract_target(target_schema)
    approved_sigs = library.signatures(approved_only=True)
    pid, match = best_match(cluster.signature, approved_sigs)
    reasons: list[str] = []

    # 1. Known, approved shape -> replay or drift-repair. A HIGH match with no
    #    drift replays; a HIGH match WITH drift, or a MODERATE match to an
    #    approved shape (the classic "same shape, something changed" signal),
    #    goes to drift-repair so only the broken fields are re-researched.
    if pid is not None and match.band in (BAND_HIGH, BAND_MODERATE):
        entry = library.get(pid)
        drift = _check_drift(cluster, target_schema, entry)
        clean = match.band == BAND_HIGH and (drift is None or not drift.drifted)
        if clean:
            reasons.append(f"HIGH match to approved profile '{pid}'; no drift")
            return ClusterDecision(
                cluster_key=cluster.key, pathway=PATHWAY_REPLAY,
                doc_ids=tuple(cluster.doc_ids), record_count=cluster.record_count,
                matched_profile=pid, match_band=match.band, drift=drift,
                reasons=tuple(reasons))
        kinds = "; ".join(drift.kinds) if drift and drift.drifted else \
            f"shape match dropped to '{match.band}'"
        reasons.append(f"matched approved profile '{pid}' but drift detected: {kinds}")
        broken = drift.broken_field_names if drift else ()
        return ClusterDecision(
            cluster_key=cluster.key, pathway=PATHWAY_DRIFT_REPAIR,
            doc_ids=tuple(cluster.doc_ids), record_count=cluster.record_count,
            matched_profile=pid, match_band=match.band, drift=drift,
            broken_fields=broken, reasons=tuple(reasons))

    # 2. Unknown shape -> relevance decides.
    rel = _relevance(cluster, target, reject_below=reject_below)
    reasons.extend(rel.reasons)

    if rel.verdict == VERDICT_CONVERTIBLE:
        pathway = PATHWAY_NOVEL if research_available else PATHWAY_REVIEW
        if not research_available:
            reasons.append("no research pathway enabled; sending to human review")
    elif rel.verdict == VERDICT_REVIEW:
        pathway = PATHWAY_REVIEW
    else:  # VERDICT_REJECT
        # A deterministic reject can still be rescued by a model tier IF the
        # file is not utterly unrelated (some required field mapped, or the
        # research path is explicitly allowed to try opaque shapes).
        if research_available and rel.required_total > 0:
            pathway = PATHWAY_NOVEL
            reasons.append("deterministic reject, but research pathway may "
                           "recover opaque fields; sending to research")
        else:
            pathway = PATHWAY_REJECT

    return ClusterDecision(
        cluster_key=cluster.key, pathway=pathway,
        doc_ids=tuple(cluster.doc_ids), record_count=cluster.record_count,
        matched_profile=pid, match_band=match.band, relevance=rel,
        reasons=tuple(reasons))


def _relevance(cluster: Cluster, target: TargetSchema, *, reject_below: float,
               ) -> RelevanceVerdict:
    src = profile_source(cluster.records, cluster.descriptions or None)
    mapping = infer_mapping(src, target)
    return assess_relevance(mapping, target, reject_below=reject_below)


def _check_drift(cluster: Cluster, target_schema: dict[str, Any], entry,
                 ) -> DriftReport | None:
    """Compare the cluster against an approved profile for drift. Behavioral
    drift uses the profile's stored FillBaseline (in meta) when present."""
    if entry is None:
        return None
    # Current behavior: run the frozen mapping and measure fill rates.
    target = extract_target(target_schema)
    _, prov = execute_mapping(cluster.records, entry.profile.mapping, target)
    current_fill = fill_rates(prov)
    baseline_meta = (entry.profile.meta or {}).get("fill_baseline")
    baseline = FillBaseline.from_dict(baseline_meta) if baseline_meta else None
    mapping_targets = {sp: tgt for tgt, sp in entry.profile.mapping.mapped().items()}
    return assess_drift(
        cluster.signature, entry.signature,
        current_fill=current_fill, baseline=baseline,
        mapping_targets=mapping_targets)


@dataclass
class BatchReport:
    """Aggregate view of a batch: per-cluster decisions + rolled-up counts."""
    decisions: list[ClusterDecision] = field(default_factory=list)

    def by_pathway(self) -> dict[str, list[ClusterDecision]]:
        out: dict[str, list[ClusterDecision]] = {}
        for d in self.decisions:
            out.setdefault(d.pathway, []).append(d)
        return out

    def doc_counts(self) -> dict[str, int]:
        """Documents routed to each pathway (the number that matters at scale)."""
        counts: dict[str, int] = {}
        for d in self.decisions:
            counts[d.pathway] = counts.get(d.pathway, 0) + len(d.doc_ids)
        return counts

    def quarantined(self) -> list[ClusterDecision]:
        return [d for d in self.decisions if d.pathway == PATHWAY_REJECT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clusters": len(self.decisions),
            "doc_counts_by_pathway": self.doc_counts(),
            "decisions": [d.to_dict() for d in self.decisions],
        }


def plan_batch(docs: list[SourceDoc], target_schema: dict[str, Any],
               library: ProfileLibrary, *, reject_below: float,
               research_available: bool = True) -> BatchReport:
    """Cluster a batch and decide a pathway per cluster, returning a BatchReport.

    Pure policy: no conversion runs here. Deterministic given the inputs."""
    clusters = cluster_documents(docs)
    decisions = [
        decide_pathway(c, target_schema, library, reject_below=reject_below,
                       research_available=research_available)
        for c in clusters
    ]
    return BatchReport(decisions=decisions)


__all__ = [
    "PATHWAY_DRIFT_REPAIR",
    "PATHWAY_NOVEL",
    "PATHWAY_REJECT",
    "PATHWAY_REPLAY",
    "PATHWAY_REVIEW",
    "BatchReport",
    "Cluster",
    "ClusterDecision",
    "SourceDoc",
    "cluster_documents",
    "decide_pathway",
    "plan_batch",
]
