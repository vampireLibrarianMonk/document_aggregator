"""Optional LLM semantic-mapping tier for the abstentions a deterministic pass
could not resolve.

Design (matches the repo's "model proposes, deterministic code verifies,
governor accepts or rejects" rule and the existing BedrockInterpreter pattern):

  - BOUNDED: the model is only ever asked about targets the deterministic
    matcher ABSTAINED on (needs_review / conflict). Confident mappings are never
    sent and never overridden.
  - CLOSED OUTPUT: the model does not free-generate a mapping. For each abstained
    target it PICKS one of that target's already-profiled source candidates, or
    explicitly ABSTAINS. The output space is the candidate list, so it cannot
    invent a source path. (Research: constrained/candidate-ranking with an
    explicit "don't know" option, so forced-valid JSON doesn't guess.)
  - DETERMINISTIC RE-VERIFICATION: every pick is re-checked by the SAME gates
    the deterministic matcher uses -- the chosen source_path must be a REAL
    source field and must be type-compatible with the target. Anything that
    fails verification is dropped back to needs_review. The model can only
    UPGRADE an abstention; it never fabricates and never collapses a conflict.
  - GATED + OFFLINE-SAFE: enabled only when settings.BEDROCK_ENABLED and a live
    client can be built. Offline (or on any error) this is a no-op and the
    deterministic mapping stands unchanged.

The model call lives behind a tiny Protocol so tests inject a fake proposer
(the test_governor FakeAdapter convention) without boto3.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import settings
from .mapping import (
    STATUS_MAPPED,
    STATUS_NEEDS_REVIEW,
    FieldMapping,
    Mapping,
    _type_compatible,
)
from .source_profile import SourceProfile
from .target_profile import TargetSchema

# Confidence assigned to a model pick that PASSES deterministic re-verification.
# Below the deterministic alias tier (0.9) and description ceiling, so a model
# upgrade is always marked as the lowest-trust accepted mapping.
_MODEL_CONFIDENCE = 0.6
_MODEL_METHOD = "semantic_model"

# Sentinel the model returns to decline a target.
_ABSTAIN = "abstain"


@dataclass
class SemanticProposal:
    """One model proposal: for `target`, use `source_path` (or abstain=None)."""
    target: str
    source_path: str | None


class SemanticProposer(Protocol):
    """Proposes a source_path (from the given candidates) for each abstained
    target, or None to abstain. Implementations: Bedrock tool-use, or a fake."""
    name: str

    def propose(self, requests: list[dict[str, Any]]) -> list[SemanticProposal]: ...


# --------------------------------------------------------------------------
# Bedrock proposer (optional; tool-use, temperature 0, bounded tokens)
# --------------------------------------------------------------------------

def _tool_spec() -> dict:
    """Force a single structured tool call: one choice per abstained target."""
    return {
        "toolSpec": {
            "name": "propose_field_mappings",
            "description": (
                "For each target field, choose which ONE of its listed source "
                "candidates best supplies it, or 'abstain' if none clearly do. "
                "Only choose from the provided candidates; never invent a path."
            ),
            "inputSchema": {"json": {
                "type": "object",
                "properties": {
                    "mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string"},
                                "source_path": {
                                    "type": "string",
                                    "description": "a candidate path, or 'abstain'",
                                },
                            },
                            "required": ["target", "source_path"],
                        },
                    }
                },
                "required": ["mappings"],
            }},
        }
    }


class BedrockSemanticProposer:
    """Uses Bedrock tool-use to pick among candidates. Lazy boto3; temperature 0."""
    name = "bedrock"

    def __init__(self) -> None:
        import boto3  # lazy so offline installs need not have it

        self._client = boto3.client("bedrock-runtime",
                                    region_name=settings.BEDROCK_REGION)
        self._model = settings.BEDROCK_MODEL

    def propose(self, requests: list[dict[str, Any]]) -> list[SemanticProposal]:
        system = (
            "You align messy source JSON fields to a canonical target schema. "
            "For each target, pick the ONE source candidate that supplies it, or "
            "'abstain' if none clearly do. Choose ONLY from the listed candidates; "
            "never invent a source path. Prefer abstaining over guessing."
        )
        user = json.dumps({"targets": requests}, ensure_ascii=False)
        resp = self._client.converse(
            modelId=self._model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            toolConfig={
                "tools": [_tool_spec()],
                "toolChoice": {"tool": {"name": "propose_field_mappings"}},
            },
            inferenceConfig={"maxTokens": 1024, "temperature": 0},
        )
        return self._extract(resp)

    @staticmethod
    def _extract(resp: dict) -> list[SemanticProposal]:
        out: list[SemanticProposal] = []
        for block in resp.get("output", {}).get("message", {}).get("content", []):
            tool = block.get("toolUse")
            if tool and tool.get("name") == "propose_field_mappings":
                payload = tool.get("input", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                for m in payload.get("mappings", []):
                    sp = m.get("source_path")
                    out.append(SemanticProposal(
                        target=m.get("target", ""),
                        source_path=None if sp in (None, "", _ABSTAIN) else sp))
        return out


def get_proposer() -> SemanticProposer | None:
    """A live Bedrock proposer when enabled+reachable, else None (offline no-op)."""
    if not settings.BEDROCK_ENABLED:
        return None
    try:
        return BedrockSemanticProposer()
    except Exception:  # boto3 missing / no creds -> stay deterministic
        return None


# --------------------------------------------------------------------------
# The tier: bound -> propose -> deterministically re-verify -> upgrade
# --------------------------------------------------------------------------

def _abstained_requests(mapping: Mapping, source: SourceProfile,
                        target: TargetSchema) -> list[dict[str, Any]]:
    """Build the bounded per-target request payload for the abstained targets.

    Each request carries the target's name/description/type and the ranked
    source candidates the deterministic matcher already found (plus a few extra
    source paths as fallback context), so the model picks from a closed set."""
    tgt_by_name = target.by_name()
    src_paths = [f.path for f in source.fields]
    src_examples = {f.path: list(f.examples)[:2] for f in source.fields}
    requests: list[dict[str, Any]] = []
    for fm in mapping.fields:
        if fm.status not in (STATUS_NEEDS_REVIEW, "conflict"):
            continue
        tf = tgt_by_name.get(fm.target)
        if tf is None:
            continue
        # Candidates the matcher ranked, else all source paths as the closed set.
        cand = [p for p, _s in fm.candidates] or src_paths
        requests.append({
            "target": fm.target,
            "description": tf.description,
            "type": tf.primary_type,
            "candidates": [
                {"source_path": p, "examples": src_examples.get(p, [])}
                for p in cand
            ],
        })
    return requests


def apply_semantic_tier(mapping: Mapping, source: SourceProfile,
                        target: TargetSchema, *,
                        proposer: SemanticProposer | None = None) -> Mapping:
    """Return a mapping with abstentions UPGRADED by verified model picks.

    No-op (returns the input mapping) when no proposer is available. Every
    accepted upgrade passed deterministic re-verification; unverifiable picks
    are left as needs_review. The input mapping is not mutated."""
    proposer = proposer if proposer is not None else get_proposer()
    if proposer is None:
        return mapping

    requests = _abstained_requests(mapping, source, target)
    if not requests:
        return mapping

    try:
        proposals = proposer.propose(requests)
    except Exception:  # model unreachable mid-flight -> deterministic stands
        return mapping

    by_target = {p.target: p for p in proposals}
    valid_paths = {f.path: f for f in source.fields}
    tgt_by_name = target.by_name()
    # Candidate set per target the model was ALLOWED to pick from (closed set).
    allowed: dict[str, set[str]] = {}
    for req in requests:
        allowed[req["target"]] = {c["source_path"] for c in req["candidates"]}

    upgraded: list[FieldMapping] = []
    for fm in mapping.fields:
        prop = by_target.get(fm.target)
        if (fm.status not in (STATUS_NEEDS_REVIEW, "conflict")
                or prop is None or prop.source_path is None):
            upgraded.append(fm)
            continue

        sp = prop.source_path
        tf = tgt_by_name.get(fm.target)
        sf = valid_paths.get(sp)
        # Re-verification gates (same posture as the deterministic matcher):
        #  (1) the pick must be a REAL source field,
        #  (2) it must be within the closed candidate set we offered,
        #  (3) it must be type-compatible with the target.
        if (sf is None or tf is None
                or sp not in allowed.get(fm.target, set())
                or not _type_compatible(sf.primary_type, tf)):
            upgraded.append(fm)  # unverifiable -> stays needs_review
            continue

        upgraded.append(FieldMapping(
            target=fm.target, source_path=sp, method=_MODEL_METHOD,
            confidence=_MODEL_CONFIDENCE, status=STATUS_MAPPED,
            candidates=fm.candidates,
            note=f"model-proposed, re-verified (type {sf.primary_type} -> "
                 f"{tf.primary_type})",
        ))
    return Mapping(fields=upgraded)


__all__ = [
    "BedrockSemanticProposer",
    "SemanticProposal",
    "SemanticProposer",
    "apply_semantic_tier",
    "get_proposer",
]
