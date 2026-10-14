"""Conformance-frontier alpha loop.

For each malformation AXIS, walk severity from 0 upward, run the REAL
deterministic matcher (run_alignment) over mutated records, and score the
engine's output against the mutation's threshold-independent truth map:

  recovered    golden G mapped to a source key whose truth IS G      (recall win)
  missed       golden G has a true source key but engine abstained   (recall loss)
  false_match  golden G mapped to a key whose truth is NOT G          (PRECISION loss)

The axis BREAKS at the lowest severity where any REQUIRED golden field is no
longer recovered (the agreed criterion). We binary-search that break point. At
every severity we also run the hard junk negative control and record its
false-match count, so a precision regression is visible across the whole sweep.

Output: a machine-readable frontier map (JSON) + a readable table. Deterministic
and offline; the embedding backend in use is recorded so a semantic-tier fix can
be compared per-backend. Nothing here is wired into the app — it is a research
harness run on demand (and as a light pytest smoke test).
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.json_alignment.mapping import (
    STATUS_CONFLICT,
    STATUS_NEEDS_REVIEW,
    _type_compatible,
    infer_mapping,
)
from app.json_alignment.pipeline import run_alignment
from app.json_alignment.semantic import SemanticProposal, apply_semantic_tier
from app.json_alignment.source_profile import profile_source
from app.json_alignment.target_profile import extract_target

from .mutations import (
    AXES,
    GOLDEN_FIELDS,
    REQUIRED_FIELDS,
    MutationResult,
    junk_record,
)

# A fixed pool of clean golden records to mutate (so results don't depend on one
# record's quirks). Deterministic.
CLEAN_POOL: list[dict[str, Any]] = [
    {"name": "Portal", "releaseYear": "2007", "developer": "Valve",
     "publisher": "Valve", "platform": "Windows PC", "genres": ["Puzzle"],
     "criticScore": 90, "ESRB": "T"},
    {"name": "Celeste", "releaseYear": "2018", "developer": "Maddy Makes Games",
     "publisher": "Maddy Makes Games", "platform": "Windows PC",
     "genres": ["Platformer"], "criticScore": 92, "ESRB": "E10+"},
    {"name": "Hades: Battle Out of Hell", "releaseYear": "2020",
     "developer": "Supergiant Games", "publisher": "Supergiant Games",
     "platform": "Windows PC", "genres": ["Roguelike", "Action"],
     "criticScore": 93, "ESRB": "T"},
]


@dataclass
class SeverityScore:
    """Field-level scoring of the engine at one axis+severity, aggregated over
    the clean pool."""
    severity: int
    recovered: int = 0
    missed: int = 0
    false_match: int = 0
    present_golden: int = 0          # recall denominator (true source keys)
    required_recovered: int = 0
    required_present: int = 0
    conflicts: int = 0               # engine abstained-with-candidates (safe)
    junk_false_match: int = 0        # false matches on the junk control

    @property
    def required_broken(self) -> bool:
        """The break criterion: some REQUIRED golden field that HAS a true source
        key was not recovered at this severity."""
        return self.required_present > 0 and self.required_recovered < self.required_present

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["required_broken"] = self.required_broken
        return d


@dataclass
class AxisFrontier:
    axis: str
    max_severity: int
    scores: list[SeverityScore] = field(default_factory=list)
    break_severity: int | None = None   # lowest severity that breaks a required field
    last_ok_severity: int | None = None  # highest severity before the break
    broken_severities: list[int] = field(default_factory=list)  # ALL severities that break
    monotonic: bool = True              # required-breakage never recovers once broken

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "max_severity": self.max_severity,
            "break_severity": self.break_severity,
            "last_ok_severity": self.last_ok_severity,
            "broken_severities": self.broken_severities,
            "monotonic": self.monotonic,
            "scores": [s.to_dict() for s in self.scores],
        }


def _score_one(mr: MutationResult, mapped: dict[str, str],
               conflicts: list[str]) -> tuple[int, int, int, int, int, int]:
    """Score one mutated record's engine output against its truth map.

    `mapped` is golden -> source_path (confident maps only). Returns
    (recovered, missed, false_match, required_recovered, required_present,
    conflict_count). A source_path may carry an array marker '[]'; compare on the
    bare path.

    FALSE-MATCH RULE (tightened after adversarial review): a confident mapping of
    golden G to source S is a false match whenever S is NOT one of G's TRUE
    source keys — regardless of whether S is a labelled decoy, a different
    golden's key, or anything else. This catches both (a) a present golden mapped
    to the wrong key and (b) an ABSENT golden (no true source key at all) mapped
    to anything. A conflict/abstention is never a false match (the engine made no
    confident claim). Earlier versions only counted known decoys, which let a
    cross-wired mapping (golden A <- golden B's key) slip through as "clean".

    STATUS EXCLUSIVITY is asserted by the caller; `mapped` here is confident maps
    only, so a conflicted/abstained target never appears in it."""
    # golden -> set of source LEAF keys that truly are it.
    truth_inv: dict[str, set[str]] = {}
    for k, g in mr.truth.items():
        if g is not None:
            truth_inv.setdefault(g, set()).add(_bare(k))
    present = mr.present_golden

    recovered = missed = false = 0
    req_rec = req_present = 0

    # Recall over the goldens that HAVE a true source key present.
    for g in present:
        if g in REQUIRED_FIELDS:
            req_present += 1
        src = mapped.get(g)
        if src is None:
            missed += 1            # abstained/conflicted -> recall loss, not false
            continue
        if _bare(src) in truth_inv.get(g, set()):
            recovered += 1
            if g in REQUIRED_FIELDS:
                req_rec += 1
        else:
            false += 1             # confidently mapped to a non-true key

    # Precision over goldens with NO true source key present (absent goldens):
    # any confident mapping of such a golden is a false match (it mapped to a
    # decoy OR to some OTHER golden's key — both are fabrications of a
    # correspondence that does not exist).
    for g, src in mapped.items():
        if g not in present:
            false += 1

    return recovered, missed, false, req_rec, req_present, len(set(conflicts))


def _bare(path: str) -> str:
    """Normalize an engine source path to its LEAF key for comparison with the
    truth map (whose keys are leaf names). The engine returns full dotted paths
    for nested fields (e.g. 'level0.name') and an array marker ('genres[]'); the
    mutation's truth records the leaf ('name', 'genres'). Compare on the leaf so
    a correct nested mapping isn't miscounted as a false match. Mutations here
    use unique leaf names per record, so leaf comparison is unambiguous."""
    return path.split(".")[-1].replace("[]", "")


def _assert_leaf_unique(mr: MutationResult) -> None:
    """Breaker invariant: the harness compares engine source paths to the truth
    map on their LEAF key (`_bare`). That is only sound if every truth key has a
    DISTINCT leaf — otherwise a mapping to the wrong field could be miscounted as
    recovered (or a false match hidden) by a leaf collision. Enforce it so the
    scoring guarantee is explicit, not assumed."""
    bares = [_bare(k) for k in mr.truth]
    if len(bares) != len(set(bares)):
        dupes = sorted({b for b in bares if bares.count(b) > 1})
        raise AssertionError(
            f"truth map has colliding leaf keys {dupes}; leaf-based scoring "
            f"would be ambiguous. Fix the axis to emit unique leaf keys.")


def _assert_status_exclusive(res: Any) -> None:
    """Guardian invariant: a target must never be BOTH confidently mapped AND in
    conflict. If it were, `mapped()` and `conflicts()` would disagree and a
    false match could be silently excused as 'a safe conflict'. Assert the two
    sets are disjoint before scoring."""
    overlap = set(res.mapping.mapped()) & set(res.mapping.conflicts())
    if overlap:
        raise AssertionError(
            f"status not exclusive: {sorted(overlap)} are both mapped and "
            f"conflicted — scoring would be unsound.")


def _junk_false_matches(rng: random.Random) -> int:
    """Hard-negative control, strengthened: run SEVERAL unrelated records plus a
    golden-adjacent hard negative, and aggregate the total golden fields that
    confidently ground across all of them. Any non-zero is a precision failure of
    the control. A single weather record was too easy; this adds records whose
    keys share surface tokens with golden fields but are semantically unrelated,
    so the control actually probes the floor."""
    total = 0
    negatives: list[dict[str, Any]] = [
        junk_record(rng),
        junk_record(rng),
        # Golden-adjacent hard negative: tokens overlap golden names but it is a
        # weather/station record, not a game. The matcher must still ground none.
        {
            "station_name": rng.choice(["North Field", "South Field"]),
            "year_installed": rng.randint(1990, 2020),
            "developer_contact": "ops@example.com",
            "platform_vendor": rng.choice(["Acme", "Globex"]),
            "score_rating": rng.randint(1, 5),
            "genre_of_station": "coastal",
        },
    ]
    for neg in negatives:
        jres = run_alignment([neg], _schema())
        _assert_status_exclusive(jres)
        total += len(jres.mapping.mapped())
    return total


def _sweep_severity(axis_name: str, severity: int, rng: random.Random) -> SeverityScore:
    """Run the whole clean pool through one axis+severity, aggregate the score,
    and add the junk-control false-match count."""
    ax = AXES[axis_name]
    sc = SeverityScore(severity=severity)
    # A base-independent axis (e.g. junk_leak) builds a fixed record regardless
    # of the clean base, so run it ONCE rather than once per pooled record (which
    # would just triple identical counts). Other axes run over the whole pool so
    # results don't hinge on one record's quirks.
    bases: list[dict[str, Any]] = [{}] if ax.base_independent else CLEAN_POOL
    for base in bases:
        mr = ax.fn(dict(base), severity, rng)
        _assert_leaf_unique(mr)
        res = run_alignment([mr.record], _schema())
        _assert_status_exclusive(res)
        mapped = res.mapping.mapped()
        rec, miss, false, rr, rp, nconf = _score_one(
            mr, mapped, res.mapping.conflicts())
        sc.recovered += rec
        sc.missed += miss
        sc.false_match += false
        sc.present_golden += len(mr.present_golden)
        sc.required_recovered += rr
        sc.required_present += rp
        sc.conflicts += nconf
    sc.junk_false_match = _junk_false_matches(rng)
    return sc


_SCHEMA_CACHE: dict[str, Any] | None = None


def _schema() -> dict[str, Any]:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        p = (Path(__file__).resolve().parents[3]
             / "sample_docs" / "project" / "7" / "golden" / "target_schema.json")
        _SCHEMA_CACHE = json.loads(p.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def run_axis(axis_name: str, *, seed: int = 1337) -> AxisFrontier:
    """Walk an axis 0..max_severity, score each, and locate the break point (the
    lowest severity where a REQUIRED field stops being recovered). We do a full
    linear walk (axes are short) AND record the first break; a binary search is
    overkill at these sizes but the break is reported either way."""
    ax = AXES[axis_name]
    fr = AxisFrontier(axis=axis_name, max_severity=ax.max_severity)
    for sev in range(0, ax.max_severity + 1):
        rng = random.Random(f"{seed}:{axis_name}:{sev}")
        sc = _sweep_severity(axis_name, sev, rng)
        fr.scores.append(sc)
        if sc.required_broken:
            fr.broken_severities.append(sev)
            if fr.break_severity is None:
                fr.break_severity = sev
                fr.last_ok_severity = sev - 1 if sev > 0 else None
    # Monotonicity check (Breaker must-fix): `break_severity`/`last_ok_severity`
    # only MEAN "the frontier" if required-breakage is monotonic in severity —
    # i.e. once a required field breaks it never recovers at a higher severity.
    # If a higher severity recovers (broken set has a gap), the single break
    # point is a mislabel; we flag it and the FULL broken-severity set is the
    # authoritative record. Compute: broken set must be a contiguous suffix.
    if fr.broken_severities:
        expected_suffix = list(range(fr.broken_severities[0], ax.max_severity + 1))
        fr.monotonic = fr.broken_severities == expected_suffix
    return fr


# --------------------------------------------------------------------------
# Semantic-tier exercise (Guardian must-fix): the optional LLM tier is a no-op
# offline, so the frontier sweep never touches it. Here we drive
# apply_semantic_tier DIRECTLY with injected fake proposers over a scenario that
# forces abstentions, and assert its deterministic re-verification gate behaves:
#   - an HONEST proposer that picks the true source key UPGRADES correctly and
#     introduces NO false match;
#   - an ADVERSARIAL decoy-picker that names a key OUTSIDE the offered candidate
#     set (or a fabricated path, or a type-incompatible pick) is REJECTED by the
#     gate (left needs_review) — the model cannot fabricate a correspondence;
#   - an in-candidate WRONG pick (a real, type-compatible decoy that the gate
#     cannot distinguish) IS accepted by the gate, and that is exactly the case
#     the HARNESS scoring must still catch as a false_match. This proves the gate
#     is a necessary filter, not a sufficient precision guarantee, and that the
#     scoring is the backstop.
# --------------------------------------------------------------------------

@dataclass
class _SemCtx:
    """Everything a semantic plan-builder needs to construct a pick plan."""
    abstained: list[str]
    src_paths: list[str]
    truth_inv: dict[str, set[str]]
    source: Any            # SourceProfile
    target: Any            # TargetSchema
    base: Any              # Mapping
    mr: MutationResult


class _FixedProposer:
    """Injected fake. `plan` maps target -> source_path (or None to abstain).
    Mimics a model that returns a fixed set of picks."""

    def __init__(self, name: str, plan: dict[str, str | None]):
        self.name = name
        self._plan = plan

    def propose(self, requests: list[dict[str, Any]]) -> list[SemanticProposal]:
        out: list[SemanticProposal] = []
        for req in requests:
            t = req["target"]
            if t in self._plan:
                out.append(SemanticProposal(target=t, source_path=self._plan[t]))
        return out


def _abstained_targets(mapping: Any) -> list[str]:
    return [fm.target for fm in mapping.fields
            if fm.status in (STATUS_NEEDS_REVIEW, STATUS_CONFLICT)]


def _semantic_case(name: str, mr: MutationResult,
                   plan_builder: Any) -> dict[str, Any]:
    """Run apply_semantic_tier with one injected proposer over `mr`, score the
    resulting mapping against the truth map, and summarize gate behavior.

    `plan_builder(ctx)` returns the proposer's target->pick plan. `ctx` carries
    everything a builder needs to construct honest, fabricated, or
    genuinely-gate-passing-but-wrong picks (see _SemCtx)."""
    src = profile_source([mr.record])
    target = extract_target(_schema())
    base = infer_mapping(src, target)
    abstained_before = _abstained_targets(base)

    src_paths = [f.path for f in src.fields]
    truth_inv: dict[str, set[str]] = {}
    for k, g in mr.truth.items():
        if g is not None:
            truth_inv.setdefault(g, set()).add(_bare(k))

    ctx = _SemCtx(
        abstained=abstained_before,
        src_paths=src_paths,
        truth_inv=truth_inv,
        source=src,
        target=target,
        base=base,
        mr=mr,
    )
    plan = plan_builder(ctx)
    proposer = _FixedProposer(name, plan)
    upgraded = apply_semantic_tier(base, src, target, proposer=proposer)

    # How many abstentions did the tier turn into confident maps, and were any
    # of the proposer's non-None picks REJECTED by re-verification?
    mapped_before = set(base.mapped())
    mapped_after = upgraded.mapped()
    accepted = {t for t in mapped_after if t not in mapped_before}
    non_abstain_picks = {t for t, sp in plan.items() if sp is not None}
    rejected = sorted(non_abstain_picks - accepted)

    # Fabrication guard: no accepted upgrade may point at a path that is not a
    # real source field (the gate must guarantee this).
    valid_paths = {f.path for f in src.fields}
    fabricated = [t for t in accepted if mapped_after[t] not in valid_paths]
    if fabricated:
        raise AssertionError(
            f"[{name}] re-verification FABRICATED paths for {fabricated} "
            f"-> {[mapped_after[t] for t in fabricated]}; the gate must only "
            f"accept real source fields.")

    # Score precision of BOTH the base (deterministic-only) mapping and the
    # post-tier mapping with the SAME scorer the sweep uses, and report the
    # DELTA the tier introduced. Isolating the delta is essential: the base
    # matcher may already produce some false matches on this hostile decoy-only
    # scenario, and the gate's job is only to never ADD to them. (A non-zero
    # base_false_match is itself a frontier finding, surfaced via the sweep's
    # noise_decoyonly axis.)
    _, _, base_false, _, _, _ = _score_one(mr, base.mapped(), base.conflicts())
    _, _, after_false, _, _, _ = _score_one(mr, mapped_after, upgraded.conflicts())

    return {
        "proposer": name,
        "abstained_before": sorted(abstained_before),
        "accepted_upgrades": sorted(accepted),
        "rejected_picks": rejected,
        "base_false_match": base_false,
        "false_match": after_false,
        "false_match_delta": after_false - base_false,
    }


def run_semantic_tier_exercise() -> list[dict[str, Any]]:
    """Three injected-proposer cases over a scenario built to force abstentions
    with real, type-compatible decoys available. Returns readable case records;
    raises on any re-verification-gate violation (fabrication accepted, honest
    guessing, or an accepted wrong pick going undetected by scoring)."""
    # Purpose-built scenario: a few golden fields have NO true source key and NO
    # token-sharing decoy, so the deterministic matcher ABSTAINS (needs_review
    # with empty candidates -> the semantic tier's offered set falls back to all
    # source paths). The record also carries real, type-compatible but
    # semantically-unrelated string fields (a weather station), which are the
    # decoys the adversarial picker will choose — real + type-compatible, so the
    # gate genuinely ACCEPTS them, and the harness scoring must catch the wrong
    # mappings as false matches. `name`/`releaseYear` keep true keys so the base
    # matcher still produces a sane partial mapping.
    rec = {
        "name": "Portal",
        "release_year": "2007",          # token-sharing -> matcher recovers
        # developer/publisher/platform/criticScore/ESRB: NO true key present.
        "station_name": "North Field",   # real string decoy (unrelated)
        "operator_label": "Globex",      # real string decoy (unrelated)
        "region_code": "KSEA",           # real string decoy (unrelated)
    }
    truth: dict[str, str | None] = {
        "name": "name",
        "release_year": "releaseYear",
        "station_name": None,
        "operator_label": None,
        "region_code": None,
    }
    mr = MutationResult(rec, truth)

    def honest(ctx: _SemCtx) -> dict[str, str | None]:
        # Pick the TRUE source leaf when one exists; here the true keys were
        # dropped, so honest correctly abstains everywhere. The floor case: an
        # honest model given no real option must not invent.
        plan: dict[str, str | None] = {}
        bare_to_path = {_bare(p): p for p in ctx.src_paths}
        for t in ctx.abstained:
            trues = ctx.truth_inv.get(t, set())
            plan[t] = next((bare_to_path[b] for b in trues if b in bare_to_path), None)
        return plan

    def fabricator(ctx: _SemCtx) -> dict[str, str | None]:
        # Names a path that does NOT exist in the source at all -> the gate must
        # reject every one (fabrication). Proves closed-output enforcement.
        return {t: f"__not_a_real_path__{t}" for t in ctx.abstained}

    def decoy_picker(ctx: _SemCtx) -> dict[str, str | None]:
        # The hostile case that MUST be caught: for each abstained target, pick a
        # REAL source field that (a) is type-compatible with the target and
        # (b) is a DECOY (not a true source for that target), so the gate's
        # real-field + closed-set + type checks all PASS and it genuinely
        # ACCEPTS a wrong mapping. The harness scoring must then flag every such
        # acceptance as a false match — proving the gate is necessary but not
        # sufficient and the scoring is the backstop.
        plan: dict[str, str | None] = {}
        tgt_by_name = ctx.target.by_name()
        for t in ctx.abstained:
            tf = tgt_by_name.get(t)
            trues = ctx.truth_inv.get(t, set())
            pick = None
            for sf in ctx.source.fields:
                if _bare(sf.path) in trues:
                    continue  # would be a correct pick, not a decoy
                if tf is not None and _type_compatible(sf.primary_type, tf):
                    pick = sf.path
                    break
            plan[t] = pick
        return plan

    # Second scenario, built to probe the CLOSED-SET gate specifically (the one
    # the decoy-only scenario can't exercise, because there every abstention has
    # empty matcher candidates so the offered set falls back to ALL source
    # paths). Here `name` is forced into a CONFLICT (two description-tier keys
    # tie), so its FieldMapping.candidates is a PROPER SUBSET of the source paths
    # — exactly {game_title, title_game}. We then have a proposer pick a REAL,
    # type-compatible source field that is OUTSIDE that offered subset
    # (`unrelated_label`); the gate's closed-set clause must reject it even
    # though it is a real, type-compatible field. If that clause were deleted,
    # this case would start accepting the out-of-set pick and the assertion
    # below would fire.
    conflict_rec = {
        "release_year": "2007",            # keeps a sane base map on releaseYear
        "game_title": "Portal",            # ties for `name` (description tier)
        "title_game": "Portal",            # ties for `name` (description tier)
        "unrelated_label": "North Field",  # real, string, NOT a name candidate
    }
    conflict_truth: dict[str, str | None] = {
        "release_year": "releaseYear",
        "game_title": None,                # a title fragment competitor, not THE name
        "title_game": None,
        "unrelated_label": None,
    }
    conflict_mr = MutationResult(conflict_rec, conflict_truth)

    def out_of_set_picker(ctx: _SemCtx) -> dict[str, str | None]:
        # For each abstained/conflicted target, pick a REAL, type-compatible
        # source field that is NOT among the matcher-offered candidates for that
        # target. The closed-set gate must reject every such pick.
        offered: dict[str, set[str]] = {}
        for fm in ctx.base.fields:
            if fm.status in (STATUS_NEEDS_REVIEW, STATUS_CONFLICT) and fm.candidates:
                offered[fm.target] = {p for p, _s in fm.candidates}
        tgt_by_name = ctx.target.by_name()
        plan: dict[str, str | None] = {}
        for t, offer in offered.items():
            tf = tgt_by_name.get(t)
            pick = None
            for sf in ctx.source.fields:
                if sf.path in offer:
                    continue  # must be OUTSIDE the offered closed set
                if tf is not None and _type_compatible(sf.primary_type, tf):
                    pick = sf.path
                    break
            plan[t] = pick
        return plan

    cases = [
        _semantic_case("honest(abstains)", mr, honest),
        _semantic_case("adversarial-fabricator", mr, fabricator),
        _semantic_case("adversarial-decoy-picker", mr, decoy_picker),
        _semantic_case("adversarial-out-of-set", conflict_mr, out_of_set_picker),
    ]

    # Assertions the Guardian requires (all stated against the DELTA the tier
    # introduces, so the base matcher's own behavior on this hostile scenario is
    # not conflated with the gate's behavior):
    honest_case, fab_case, decoy_case, outset_case = cases
    # 1. The fabricator names paths that are not in the source at all; the gate
    #    MUST reject every one, so the tier accepts nothing and adds no false
    #    match (closed-output / no-fabrication guarantee).
    assert not fab_case["accepted_upgrades"], (
        f"fabricated picks were accepted: {fab_case['accepted_upgrades']}")
    assert fab_case["false_match_delta"] == 0, (
        "fabricator ADDED a false match beyond the deterministic base — the "
        "gate failed to reject out-of-source paths")
    # 2. The honest proposer, given no real option in a decoy-only scenario,
    #    abstains and so adds nothing.
    assert honest_case["false_match_delta"] == 0, (
        f"honest proposer added false matches: {honest_case}")
    assert not honest_case["accepted_upgrades"], (
        f"honest proposer upgraded despite no true key present: {honest_case}")
    # 3. The decoy-picker names a REAL in-source decoy. The gate accepts it only
    #    if it is also within the offered candidate set and type-compatible;
    #    whatever it accepts, the SCORING must count every accepted upgrade as a
    #    false match (the picked key is never a true source for the target). So
    #    the tier's false-match DELTA must equal the number of accepted upgrades
    #    — an accepted wrong pick can never be invisible.
    assert decoy_case["false_match_delta"] == len(decoy_case["accepted_upgrades"]), (
        f"decoy-picker: accepted upgrades {decoy_case['accepted_upgrades']} but "
        f"false_match_delta={decoy_case['false_match_delta']} — an accepted "
        f"wrong pick went undetected by scoring")
    # 4. The out-of-set picker names REAL, type-compatible fields that are
    #    OUTSIDE each target's offered candidate set. The closed-set gate clause
    #    (`sp not in allowed`) must reject every one, so NOTHING is accepted and
    #    no false match is added. This probes the gate dimension the decoy-only
    #    scenario cannot (there the offered set is all source paths). It is a
    #    guard error if the exercise produced no out-of-set pick to test.
    assert outset_case["abstained_before"], (
        "out-of-set scenario produced no abstention/conflict to probe — the "
        "closed-set gate would be untested")
    assert not outset_case["accepted_upgrades"], (
        f"closed-set gate FAILED: out-of-set picks were accepted "
        f"{outset_case['accepted_upgrades']} — the `sp not in allowed` clause is "
        f"not enforcing the offered candidate set")
    assert outset_case["false_match_delta"] == 0, (
        f"out-of-set picker added false matches: {outset_case}")
    for c in cases:
        c["verdict"] = "ok"
    return cases


def run_frontier(*, seed: int = 1337) -> dict[str, Any]:
    """Run every axis and assemble the frontier map, recording the embedding
    backend so a later semantic-tier fix can be compared per-backend."""
    axes = {name: run_axis(name, seed=seed).to_dict() for name in AXES}
    semantic = run_semantic_tier_exercise()
    return {
        "embedding_backend": getattr(settings, "EMBEDDING_BACKEND", "unknown"),
        "bedrock_enabled": bool(getattr(settings, "BEDROCK_ENABLED", False)),
        "semantic_tier": semantic,
        "golden_fields": GOLDEN_FIELDS,
        "required_fields": sorted(REQUIRED_FIELDS),
        "seed": seed,
        "axes": axes,
    }


def format_frontier(frontier: dict[str, Any]) -> str:
    """A compact readable table of the frontier map."""
    lines = [
        f"Conformance frontier  (embedding={frontier['embedding_backend']}, "
        f"bedrock={frontier['bedrock_enabled']}, seed={frontier['seed']})",
        "axis            break@  last_ok  mono  per-severity recovered/present (false, junk_fp)",
    ]
    for name, ax in frontier["axes"].items():
        brk = ax["break_severity"]
        mono = "y" if ax.get("monotonic", True) else "N"
        cells = " ".join(
            f"s{s['severity']}:{s['recovered']}/{s['present_golden']}"
            f"({s['false_match']},{s['junk_false_match']})"
            for s in ax["scores"]
        )
        lines.append(
            f"{name:15} {str(brk):>5}  {str(ax['last_ok_severity']):>6}  "
            f"{mono:>4}   {cells}")
    sem = frontier.get("semantic_tier")
    if sem:
        lines.append("")
        lines.append("semantic-tier re-verification (apply_semantic_tier, injected proposers):")
        for case in sem:
            lines.append(
                f"  {case['proposer']:24} upgrades={len(case['accepted_upgrades'])} "
                f"rejected={len(case['rejected_picks'])} "
                f"false_match={case['false_match']} "
                f"(base={case['base_false_match']}, delta={case['false_match_delta']})"
                f"  -> {case['verdict']}")
    return "\n".join(lines)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Run the conformance-frontier alpha loop.")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "frontier_results.json")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()
    settings.BEDROCK_ENABLED = False
    frontier = run_frontier(seed=args.seed)
    args.out.write_text(json.dumps(frontier, indent=2), encoding="utf-8")
    print(format_frontier(frontier))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
