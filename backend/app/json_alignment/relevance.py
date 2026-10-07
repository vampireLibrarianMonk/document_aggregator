"""Relevance gating: decide whether an incoming JSON has any business becoming
the golden shape at all, BEFORE committing it to a conversion pathway.

The batch workload mixes real source documents with files that have nothing to
do with the golden schema. We need a cheap, deterministic verdict:

    convertible   enough of the golden's REQUIRED fields can be grounded
    review        some required coverage, but below the project's confidence
                  dial -> a human should confirm it's one of ours
    reject         zero required fields map (definitely unrelated), OR required
                  coverage falls under the per-project `reject_below` dial

The score is derived from the SAME deterministic matcher used everywhere else
(infer_mapping), so relevance inherits its precision-first, no-fabrication
posture: a field only counts as "covered" when a confident, grounded mapping
exists. An unrelated file abstains on everything -> coverage 0 -> reject. There
is no guessing to inflate relevance.

Policy (user-approved):
  - zero mappable REQUIRED fields is ALWAYS a reject (the hard floor), even if
    the dial is set permissively.
  - otherwise compare required-field coverage against `reject_below`
    (per-project, default 0.5): below -> reject, within a small band above ->
    review, comfortably above -> convertible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mapping import Mapping, infer_mapping
from .source_profile import SourceProfile, profile_source
from .target_profile import TargetSchema, extract_target

# Default per-project reject dial. A file whose share of REQUIRED golden fields
# that can be confidently mapped is below this is rejected as unrelated.
DEFAULT_REJECT_BELOW = 0.5

# Width of the "review" band sitting just above the reject line: a file that
# clears the dial but only barely is sent to a human rather than auto-accepted.
_REVIEW_MARGIN = 0.2

VERDICT_CONVERTIBLE = "convertible"
VERDICT_REVIEW = "review"
VERDICT_REJECT = "reject"


@dataclass
class RelevanceVerdict:
    """The relevance decision for one source shape against the golden schema."""
    verdict: str                       # convertible | review | reject
    coverage: float                    # share of ALL target fields mapped
    required_coverage: float           # share of REQUIRED target fields mapped
    required_total: int
    required_mapped: int
    reject_below: float
    mapped_required: tuple[str, ...] = ()
    unmapped_required: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def is_rejected(self) -> bool:
        return self.verdict == VERDICT_REJECT

    @property
    def needs_human(self) -> bool:
        return self.verdict == VERDICT_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "coverage": round(self.coverage, 4),
            "required_coverage": round(self.required_coverage, 4),
            "required_total": self.required_total,
            "required_mapped": self.required_mapped,
            "reject_below": self.reject_below,
            "mapped_required": list(self.mapped_required),
            "unmapped_required": list(self.unmapped_required),
            "reasons": list(self.reasons),
        }


def _coverage_from_mapping(mapping: Mapping, target: TargetSchema,
                           ) -> tuple[float, float, tuple[str, ...], tuple[str, ...]]:
    """Return (coverage, required_coverage, mapped_required, unmapped_required)."""
    mapped_targets = set(mapping.mapped())           # target -> source_path keys
    all_fields = [f.name for f in target.fields]
    required = list(target.required_names())

    coverage = (sum(1 for t in all_fields if t in mapped_targets) / len(all_fields)
                if all_fields else 0.0)

    mapped_req = tuple(t for t in required if t in mapped_targets)
    unmapped_req = tuple(t for t in required if t not in mapped_targets)
    required_coverage = (len(mapped_req) / len(required)) if required else 1.0
    return coverage, required_coverage, mapped_req, unmapped_req


def assess_relevance(mapping: Mapping, target: TargetSchema, *,
                     reject_below: float = DEFAULT_REJECT_BELOW,
                     ) -> RelevanceVerdict:
    """Turn an inferred Mapping into a RelevanceVerdict against the golden schema.

    `reject_below` is the per-project dial (0..1). The hard floor (zero required
    fields mapped -> reject) always applies regardless of the dial. When the
    schema declares NO required fields, we fall back to overall coverage so a
    file still can't be declared relevant on zero evidence."""
    reject_below = _clamp01(reject_below)
    coverage, req_cov, mapped_req, unmapped_req = _coverage_from_mapping(mapping, target)
    required_total = len(target.required_names())
    required_mapped = len(mapped_req)
    reasons: list[str] = []

    # Decide on the metric that matters: required coverage when there ARE
    # required fields, else overall coverage (so a schema with no required
    # fields still needs *some* grounded mapping to be called relevant).
    metric = req_cov if required_total else coverage
    metric_name = "required-field" if required_total else "overall"

    # Hard floor: nothing of the required core maps -> definitely unrelated.
    hard_floor_hit = (required_total > 0 and required_mapped == 0) or \
        (required_total == 0 and coverage == 0.0)

    if hard_floor_hit:
        verdict = VERDICT_REJECT
        reasons.append(
            "zero required golden fields could be grounded" if required_total
            else "no golden field could be grounded")
    elif metric < reject_below:
        verdict = VERDICT_REJECT
        reasons.append(
            f"{metric_name} coverage {metric:.0%} is below the reject dial "
            f"{reject_below:.0%}")
    elif metric < reject_below + _REVIEW_MARGIN:
        verdict = VERDICT_REVIEW
        reasons.append(
            f"{metric_name} coverage {metric:.0%} clears the dial but only "
            f"within the review margin; a human should confirm relevance")
    else:
        verdict = VERDICT_CONVERTIBLE
        reasons.append(f"{metric_name} coverage {metric:.0%} meets the dial")

    if unmapped_req and verdict != VERDICT_REJECT:
        reasons.append(
            f"{len(unmapped_req)} required field(s) still unmapped: "
            + ", ".join(unmapped_req))

    return RelevanceVerdict(
        verdict=verdict, coverage=coverage, required_coverage=req_cov,
        required_total=required_total, required_mapped=required_mapped,
        reject_below=reject_below, mapped_required=mapped_req,
        unmapped_required=unmapped_req, reasons=tuple(reasons),
    )


def relevance_of(records: list[dict[str, Any]], target_schema: dict[str, Any], *,
                 reject_below: float = DEFAULT_REJECT_BELOW,
                 source_descriptions: dict[str, str] | None = None,
                 ) -> RelevanceVerdict:
    """One-shot relevance: profile a batch, infer a mapping, and assess it.

    This is the "dry pass" the batch router runs to decide a pathway before any
    conversion is committed. Deterministic; `source_descriptions` is an optional
    honest signal (e.g. a source metadata document), never fabricated."""
    src: SourceProfile = profile_source(records, source_descriptions)
    target = extract_target(target_schema)
    mapping = infer_mapping(src, target)
    return assess_relevance(mapping, target, reject_below=reject_below)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


__all__ = [
    "DEFAULT_REJECT_BELOW",
    "VERDICT_CONVERTIBLE",
    "VERDICT_REJECT",
    "VERDICT_REVIEW",
    "RelevanceVerdict",
    "assess_relevance",
    "relevance_of",
]
