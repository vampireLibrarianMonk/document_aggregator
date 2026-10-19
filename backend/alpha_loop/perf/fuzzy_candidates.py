"""Fuzzy-scorer optimization candidates, each a drop-in for mapping._fuzzy_score.

Every candidate preserves the baseline's exact semantics (same length guard,
blocklist, budget, scaling, <=budget verdict). They differ only in how cheaply
they AVOID the Levenshtein DP via SOUND lower bounds on edit distance:

  A single edit (ins/del/sub) changes:
    - string length by at most 1          -> length bound
    - the character MULTISET L1 by <= 2    -> count bound: dist >= ceil(L1/2)
    - the character PRESENCE set by <= 2   -> presence bound: dist >= ceil(popXOR/2)
    - the bigram set by at most 2          -> bigram bound: shared >= max_bg - 2d

All bounds are PROVABLE lower bounds, so a pair pruned by them has true
edit distance > budget and the baseline would have scored it 0.0 too. The
correctness gate (optbench) verifies identical final mappings regardless.

Built as factories closing over the live mapping module so they see current
constants/blocklist.
"""
from __future__ import annotations

from typing import Callable


def _plain_levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _bounded_levenshtein(a: str, b: str, max_dist: int) -> int:
    """True distance if <= max_dist, else max_dist+1 (banded early-exit)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    INF = max_dist + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        lo, hi = max(1, i - max_dist), min(lb, i + max_dist)
        cur = [INF] * (lb + 1)
        cur[0] = i
        rmin = i
        for j in range(lo, hi + 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
            cur[j] = v
            if v < rmin:
                rmin = v
        if rmin > max_dist:
            return INF
        prev = cur
    return prev[-1] if prev[-1] <= max_dist else INF


# --------------------------------------------------------------------------
# Candidate factories. Each returns a function(s_name:set, tgt)->float.
# --------------------------------------------------------------------------

def _preamble(mapping_mod):
    """Shared closure pieces: constants + the common guard up to the point where
    a distance is needed. Returns (FMIN, FMAX, BLOCK)."""
    return mapping_mod._FUZZY_MIN, mapping_mod._FUZZY_MAX, mapping_mod._FUZZY_WORD_BLOCKLIST


def baseline_like(mapping_mod) -> Callable:
    """Faithful re-implementation (full DP) — sanity control, should match."""
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        if len(t) < 3 or abs(len(s) - len(t)) > 2:
            return 0.0
        budget = max(1, len(t) // 4)
        d = _plain_levenshtein(s, t)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


def count_bound(mapping_mod) -> Callable:
    """Add a character-COUNT L1 lower bound before the DP. dist >= ceil(L1/2)."""
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    from collections import Counter

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        if len(t) < 3 or abs(len(s) - len(t)) > 2:
            return 0.0
        budget = max(1, len(t) // 4)
        # count L1 / 2 is a sound lower bound on edit distance
        ca, cb = Counter(s), Counter(t)
        l1 = sum((ca - cb).values()) + sum((cb - ca).values())
        if (l1 + 1) // 2 > budget:
            return 0.0
        d = _plain_levenshtein(s, t)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


def count_banded(mapping_mod) -> Callable:
    """count bound + banded early-exit DP (double filter cascade)."""
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    from collections import Counter

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        if len(t) < 3 or abs(len(s) - len(t)) > 2:
            return 0.0
        budget = max(1, len(t) // 4)
        ca, cb = Counter(s), Counter(t)
        l1 = sum((ca - cb).values()) + sum((cb - ca).values())
        if (l1 + 1) // 2 > budget:
            return 0.0
        d = _bounded_levenshtein(s, t, budget)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


# --------------------------------------------------------------------------
# Signature-cached cascade: compute each unique string's cheap signatures ONCE
# (presence bitmask + bigram set), memoized, so the width^2 loop reuses them.
# Cascade cheapest-first: length -> presence-popcount -> bigram -> DP. Each is a
# SOUND edit-distance lower bound, so the <=budget verdict is identical.
# --------------------------------------------------------------------------

def _mask(s: str) -> int:
    """26-bit character-presence bitmask (lowercase ascii)."""
    m = 0
    for ch in s:
        o = ord(ch) - 97
        if 0 <= o < 26:
            m |= (1 << o)
    return m


def cascade_cached(mapping_mod) -> Callable:
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    # Per-string signature cache: joined -> (mask, bigram_frozenset, len).
    sig: dict[str, tuple[int, frozenset, int]] = {}

    def _sig(s: str):
        hit = sig.get(s)
        if hit is None:
            bg = frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset((s,))
            hit = (_mask(s), bg, len(s))
            sig[s] = hit
        return hit

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        lt = len(t)
        if lt < 3 or abs(len(s) - lt) > 2:
            return 0.0
        budget = max(1, lt // 4)
        sm, sbg, _ = _sig(s)
        tm, tbg, _ = _sig(t)
        # presence bound (near-free): dist >= ceil(popcount(sm^tm)/2)
        xor = sm ^ tm
        if xor:
            pop = bin(xor).count("1")
            if (pop + 1) // 2 > budget:
                return 0.0
        # bigram bound: shared >= max_bg - 2*budget required
        max_bg = len(sbg) if len(sbg) > len(tbg) else len(tbg)
        if len(sbg & tbg) < max_bg - 2 * budget:
            return 0.0
        d = _bounded_levenshtein(s, t, budget)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


# --------------------------------------------------------------------------
# Chasing the ceiling: rapidfuzz (C++/SIMD edit distance, MIT). Its
# Levenshtein.distance matches our ins/del/sub=1 semantics exactly, and
# score_cutoff gives a built-in early-exit equivalent to our budget question.
# Two variants: plain (C++ DP every pair) and cascade (our cheap Python
# prefilters, then rapidfuzz only for survivors).
# --------------------------------------------------------------------------

def rapidfuzz_plain(mapping_mod) -> Callable:
    """Swap the DP for rapidfuzz's C++ Levenshtein with score_cutoff=budget."""
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    from rapidfuzz.distance import Levenshtein

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        if len(t) < 3 or abs(len(s) - len(t)) > 2:
            return 0.0
        budget = max(1, len(t) // 4)
        d = Levenshtein.distance(s, t, score_cutoff=budget)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


def rapidfuzz_cascade(mapping_mod) -> Callable:
    """Our cheap memoized prefilters (len -> presence popcount -> bigram) then
    rapidfuzz's C++ DP only for survivors. Best of both worlds."""
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    from rapidfuzz.distance import Levenshtein
    sig: dict[str, tuple[int, frozenset]] = {}

    def _sig(s: str):
        hit = sig.get(s)
        if hit is None:
            bg = frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset((s,))
            hit = (_mask(s), bg)
            sig[s] = hit
        return hit

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in set(tgt.tokens):
                return 0.0
        s = "".join(sorted(s_name))
        t = "".join(sorted(tgt.tokens))
        lt = len(t)
        if lt < 3 or abs(len(s) - lt) > 2:
            return 0.0
        budget = max(1, lt // 4)
        sm, sbg = _sig(s)
        tm, tbg = _sig(t)
        xor = sm ^ tm
        if xor and (bin(xor).count("1") + 1) // 2 > budget:
            return 0.0
        max_bg = len(sbg) if len(sbg) > len(tbg) else len(tbg)
        if len(sbg & tbg) < max_bg - 2 * budget:
            return 0.0
        d = Levenshtein.distance(s, t, score_cutoff=budget)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f


# --------------------------------------------------------------------------
# Cycle 1: eliminate the repeated "".join(sorted(tokens)). The target joined
# string is invariant per target; the source joined per source. Both are
# recomputed on every pair in the baseline. Cache both (target cached by id on a
# side dict; source memoized by token tuple), so the width^2 loop does lookups.
# --------------------------------------------------------------------------

def cascade_joincache(mapping_mod) -> Callable:
    FMIN, FMAX, BLOCK = _preamble(mapping_mod)
    sig: dict[str, tuple[int, frozenset]] = {}
    joined_cache: dict[tuple, str] = {}
    tgt_cache: dict[int, tuple] = {}   # id(tgt) -> (t_joined, lt, budget, tbg, tm, tok_set)

    def _sig(s: str):
        hit = sig.get(s)
        if hit is None:
            bg = frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset((s,))
            hit = (_mask(s), bg)
            sig[s] = hit
        return hit

    def _src_joined(s_name_frozen):
        j = joined_cache.get(s_name_frozen)
        if j is None:
            j = "".join(sorted(s_name_frozen))
            joined_cache[s_name_frozen] = j
        return j

    def _tgt(tgt):
        # Key by the target's token TUPLE (value-based), NOT id(tgt): object ids
        # are reused after GC, so an id() key can return a stale entry for a
        # different target (an unsound bug the correctness gate caught). The
        # token tuple uniquely and stably identifies the target's match-relevant
        # state.
        k = tuple(tgt.tokens)
        v = tgt_cache.get(k)
        if v is None:
            t = "".join(sorted(tgt.tokens))
            lt = len(t)
            budget = max(1, lt // 4)
            tm, tbg = _sig(t)
            v = (t, lt, budget, tbg, tm, set(tgt.tokens))
            tgt_cache[k] = v
        return v

    def f(s_name, tgt):
        if not s_name or not tgt.tokens:
            return 0.0
        t, lt, budget, tbg, tm, ttok = _tgt(tgt)
        if lt < 3:
            return 0.0
        if len(s_name) == 1:
            (only,) = tuple(s_name)
            if only in BLOCK and only not in ttok:
                return 0.0
        s = _src_joined(frozenset(s_name))
        if abs(len(s) - lt) > 2:
            return 0.0
        sm, sbg = _sig(s)
        xor = sm ^ tm
        if xor and (bin(xor).count("1") + 1) // 2 > budget:
            return 0.0
        max_bg = len(sbg) if len(sbg) > len(tbg) else len(tbg)
        if len(sbg & tbg) < max_bg - 2 * budget:
            return 0.0
        d = _bounded_levenshtein(s, t, budget)
        if d == 0 or d > budget:
            return 0.0
        return FMIN + (1.0 - (d - 1) / budget) * (FMAX - FMIN)

    return f
