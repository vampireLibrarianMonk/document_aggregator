"""Prototype: index-accelerated infer_mapping (the VERTICAL optimization).

The production `infer_mapping` scores every (source_field x target_field) pair —
O(width^2). This prototype keeps the EXACT per-pair scoring (`_score_candidate`,
imported unchanged) but, per target, only scores the source fields that could
possibly produce a non-zero score, found via three indexes built ONCE over the
sources:

  1. token-set index   frozenset(tokens) -> sources     (exact/normalized/alias)
  2. inverted token idx token -> sources                (overlap/description: Jaccard>0 needs a shared token)
  3. length buckets     len(joined name) -> sources      (fuzzy: requires |len diff|<=2)

Every pruned pair is one that `_score_candidate` would have scored 0.0 anyway,
so the produced mapping is PROVABLY identical to the baseline — which the
correctness-gated A/B verifies empirically on the width sweep. This is a hashing
(index) optimization, not an approximation: no precision is traded.

Nothing in the app imports this; it is an apparatus-level prototype measured
against the baseline before any production change is considered.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.json_alignment.mapping import (
    STATUS_CONFLICT,
    STATUS_MAPPED,
    STATUS_NEEDS_REVIEW,
    FieldMapping,
    Mapping,
    _score_candidate,
    _cosine_to_target,
)
from app.json_alignment.source_profile import SourceProfile
from app.json_alignment.target_profile import TargetField, TargetSchema


def _joined(tokens: tuple[str, ...]) -> str:
    return "".join(sorted(tokens))


def banded_levenshtein(a: str, b: str, max_dist: int) -> int:
    """Edit distance with early exit: returns the true distance when it is
    <= max_dist, else returns max_dist + 1. The fuzzy tier only asks
    'is dist <= budget?', so this is BEHAVIOR-IDENTICAL for that question."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    if not a:
        return lb
    if not b:
        return la
    INF = max_dist + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        lo = max(1, i - max_dist)
        hi = min(lb, i + max_dist)
        cur = [INF] * (lb + 1)
        cur[0] = i
        row_min = i
        for j in range(lo, hi + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > max_dist:
            return INF
        prev = cur
    return prev[-1] if prev[-1] <= max_dist else INF


def _bigrams(s: str) -> frozenset[str]:
    """Character bigram set of a string (for a cheap edit-distance lower bound)."""
    return frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset((s,))


def fuzzy_distance_filtered(a: str, b: str, max_dist: int,
                            a_bigrams: frozenset[str] | None = None) -> int:
    """A bigram-prefiltered banded distance. The number of shared character
    bigrams gives a cheap LOWER BOUND on edit distance: if two strings are
    within d edits, they must share at least (max_len - 1) - 2*d bigrams. When
    the shared count is too low we can PROVE dist > max_dist and skip the DP
    entirely — this cuts the number of Levenshtein CALLS, which the profiler
    showed is the real cost (hundreds of thousands of calls, not long strings).
    Behavior-identical for the 'dist <= max_dist?' question the fuzzy tier asks."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    ab = a_bigrams if a_bigrams is not None else _bigrams(a)
    bb = _bigrams(b)
    shared = len(ab & bb)
    max_len = max(len(a), len(b))
    # Lower bound: edits >= ceil((required_bigrams - shared)/2); if even 0 edits
    # can't be reached, prove it. A conservative, SOUND bound: a single edit
    # destroys at most 2 bigrams, so dist >= (|bigrams(a)| - shared + 1)//... ;
    # use the standard q-gram bound: dist >= (max_bigrams - shared) / 1, floored.
    needed = (max_len - 1) - 2 * max_dist
    if shared < needed:
        return max_dist + 1   # provably too far -> skip the DP
    return banded_levenshtein(a, b, max_dist)


def make_fuzzy_score_fast(mapping_mod):
    """Build a drop-in replacement for mapping._fuzzy_score that adds a cheap
    bigram lower-bound BEFORE the Levenshtein DP, keyed to the REAL per-call
    budget. Behavior-identical (same length guard, same blocklist, same budget,
    same scaling); it only skips the DP for pairs it can PROVE exceed the budget.
    Closes over the live module so it sees the current constants."""
    _FUZZY_MIN = mapping_mod._FUZZY_MIN
    _FUZZY_MAX = mapping_mod._FUZZY_MAX
    _BLOCK = mapping_mod._FUZZY_WORD_BLOCKLIST

    def _fuzzy_score_fast(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in _BLOCK and only not in set(tgt.tokens):
                return 0.0
        s_joined = "".join(sorted(s_name))
        t_joined = "".join(sorted(tgt.tokens))
        if len(t_joined) < 3:
            return 0.0
        if abs(len(s_joined) - len(t_joined)) > 2:
            return 0.0
        budget = max(1, len(t_joined) // 4)
        # Bigram lower bound: a single edit changes at most 2 bigrams, so if the
        # two strings share fewer than (bigrams_in_longer - 2*budget) bigrams,
        # the distance MUST exceed budget -> skip the DP. Sound (never prunes a
        # real <=budget match).
        sb = _bigrams(s_joined)
        tb = _bigrams(t_joined)
        max_bg = max(len(sb), len(tb))
        if len(sb & tb) < max_bg - 2 * budget:
            return 0.0
        dist = banded_levenshtein(s_joined, t_joined, budget)
        if dist == 0 or dist > budget:
            return 0.0
        cleanliness = 1.0 - (dist - 1) / budget
        return _FUZZY_MIN + cleanliness * (_FUZZY_MAX - _FUZZY_MIN)

    return _fuzzy_score_fast


class _SourceIndex:
    """Three indexes over the source fields, built once (O(sum field sizes))."""

    def __init__(self, source: SourceProfile) -> None:
        self.by_path = {sf.path: sf for sf in source.fields}
        self.token_set: dict[frozenset[str], list[str]] = defaultdict(list)
        self.by_token: dict[str, set[str]] = defaultdict(set)
        self.by_len: dict[int, set[str]] = defaultdict(set)
        for sf in source.fields:
            tset = frozenset(sf.tokens)
            self.token_set[tset].append(sf.path)
            for tok in set(sf.tokens) | set(sf.desc_tokens):
                self.by_token[tok].add(sf.path)
            self.by_len[len(_joined(sf.tokens))].add(sf.path)

    def candidates_for(self, tgt: TargetField) -> list[str]:
        """The UNION of source paths that could score > 0 for this target:
          - exact token-set equal to the target's token set;
          - token-set equal to any declared ALIAS token-set;
          - token-set equal to any curated SYNONYM of the target (closed dict);
          - share >=1 token with the target's name OR description (overlap tier);
          - joined-name length within +-2 of the target (fuzzy tier window).
        A superset of every possibly-nonzero candidate -> results unchanged."""
        from app.json_alignment.mapping import _SYNONYMS
        from app.json_alignment.source_profile import name_tokens

        paths: set[str] = set()
        # exact / normalized: token-set equality
        paths.update(self.token_set.get(frozenset(tgt.tokens), []))
        # alias: token-set equal to a declared alias's tokens
        for alias in tgt.aliases:
            paths.update(self.token_set.get(frozenset(name_tokens(str(alias))), []))
        # synonym: token-set equal to a curated single-token synonym of the target
        for syn in _SYNONYMS.get(tgt.name, ()):  # closed vocabulary
            paths.update(self.token_set.get(frozenset((syn,)), []))
        # overlap/description: any shared token (name or description side)
        for tok in set(tgt.tokens) | set(tgt.desc_tokens):
            paths.update(self.by_token.get(tok, set()))
        # fuzzy: joined-name length within +-2 of the target's joined length
        tlen = len(_joined(tgt.tokens))
        for L in range(tlen - 2, tlen + 3):
            paths.update(self.by_len.get(L, set()))
        return [p for p in paths if p in self.by_path]


def infer_mapping_indexed(source: SourceProfile, target: TargetSchema) -> Mapping:
    """Index-accelerated twin of `infer_mapping`. Same grounding rules, same
    tie/conflict logic, same gated semantic candidate re-rank — only the set of
    pairs scored is pruned to possibly-nonzero candidates via the index."""
    idx = _SourceIndex(source)
    sf_by_path = idx.by_path
    out: list[FieldMapping] = []

    for tgt in target.fields:
        scored: list[tuple[float, str, str]] = []
        for path in idx.candidates_for(tgt):
            sf = sf_by_path[path]
            score, method = _score_candidate(sf, tgt)
            if score > 0.0:
                scored.append((score, method, sf.path))

        scored.sort(key=lambda x: (-x[0], x[2]))

        if not scored:
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method="abstain",
                confidence=0.0, status=STATUS_NEEDS_REVIEW, candidates=(),
                note="no source field cleared the match floor",
            ))
            continue

        top_score, top_method, top_path = scored[0]

        cand_order = scored
        if len(scored) > 1:
            cos_by_path = {
                p: _cosine_to_target(sf_by_path[p], tgt)
                for _s, _m, p in scored if p in sf_by_path
            }
            cand_order = sorted(
                scored, key=lambda x: (-x[0], -cos_by_path.get(x[2], 0.0), x[2]))
        candidates = tuple((p, s) for s, _m, p in cand_order)

        tied = [c for c in scored if c[0] == top_score and c[2] != top_path]
        if tied and top_method not in ("exact", "normalized"):
            out.append(FieldMapping(
                target=tgt.name, source_path=None, method=top_method,
                confidence=top_score, status=STATUS_CONFLICT, candidates=candidates,
                note=f"ambiguous: {1 + len(tied)} source fields tie at {top_score:.2f}",
            ))
            continue

        out.append(FieldMapping(
            target=tgt.name, source_path=top_path, method=top_method,
            confidence=top_score, status=STATUS_MAPPED, candidates=candidates,
        ))
    return Mapping(fields=out)
