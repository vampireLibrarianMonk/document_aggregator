"""The precision-editing techniques, pitted against each other.

Common contract:
    technique(draft_units: dict[key->str], intents: list[dict], ctx) -> dict[key->str]

Each returns a NEW unit map. All four honour two invariants: a genuine CONFLICT
intent is never resolved (left unchanged), and a value is only emitted if it is
grounded (present in ctx['grounding']). They differ in HOW they apply an edit,
which is what the scaling scorecard exposes:

  T1 anchored span-replace   replace the WHOLE target unit with the corrected
                             value. Exact, bounded, but coarse for prose (loses
                             any draft text that should have stayed).
  T2 diff-constrained        compute a minimal token diff between the draft unit
                             and the corrected value and apply ONLY that diff,
                             strictly within the target unit. Rejects anything
                             touching another unit -> 0 unintended by design.
  T3 model refinement        gpt-oss-120b applies the correction in prose. High
                             fluency, but a model regenerating a paragraph can
                             drift neighbouring text -> watch unintended as size
                             grows. Grounded + conflict-guarded.
  T4 micro (edit-tagger)     GECToR "tag, not rewrite" paradigm, implemented
                             deterministically: token-level KEEP/DELETE/REPLACE/
                             APPEND between draft-unit tokens and the corrected
                             tokens, applied to the target unit only. Keeps
                             untouched tokens by construction -> structurally
                             bounded, no model, no network, no training.
"""
from __future__ import annotations

import difflib
import re


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _sentences(text: str) -> list[str]:
    """Split into sentences, keeping the trailing period on each."""
    parts = re.findall(r"[^.!?]*[.!?]", text)
    return [p.strip() for p in parts if p.strip()] or ([text] if text.strip() else [])


def _splice_sentence(paragraph: str, new_sentence: str) -> str:
    """Locate the sentence in `paragraph` most similar to `new_sentence` and
    replace ONLY it, leaving every other sentence verbatim. This is the
    'locate-then-replace at sentence boundary' precision edit: the surrounding
    context survives untouched. If the unit is a single sentence (a field or a
    one-liner), this reduces to a straight replace."""
    sents = _sentences(paragraph)
    if len(sents) <= 1:
        return new_sentence
    best_i, best_r = 0, -1.0
    for i, s in enumerate(sents):
        r = difflib.SequenceMatcher(a=_norm(s), b=_norm(new_sentence)).ratio()
        if r > best_r:
            best_i, best_r = i, r
    out = list(sents)
    out[best_i] = new_sentence.strip()
    return " ".join(out)


def _grounded(value: str, ctx: dict) -> bool:
    g = ctx.get("grounding", "")
    v = _norm(value)
    return bool(v) and (v in _norm(g))


def _apply_guards(target: str, intent: dict, ctx: dict):
    """Return (should_edit, reason). Shared pre-checks every technique honours:
    never resolve a conflict, never emit an ungrounded value."""
    if intent.get("conflict"):
        return False, "conflict-preserved"
    nv = intent.get("new_value")
    if nv in (None, ""):
        return False, "no-value"
    # relabel/value corrections carry a concrete grounded value; prose bodies
    # carry the corrected sentence as new_value too (generator sets it).
    if not _grounded(nv, ctx):
        return False, "ungrounded"
    return True, ""


# --------------------------------------------------------------------------
# T1 — anchored span-replace
# --------------------------------------------------------------------------

def t1_anchored_replace(draft_units: dict[str, str], intents: list[dict], ctx: dict) -> dict[str, str]:
    out = dict(draft_units)
    for intent in intents:
        target = intent["target"]
        if target not in out:
            continue
        ok, _ = _apply_guards(target, intent, ctx)
        if not ok:
            continue
        out[target] = str(intent["new_value"])
    return out


# --------------------------------------------------------------------------
# T2 — diff-constrained edit (minimal diff, strictly in-span)
# --------------------------------------------------------------------------

def t2_diff_constrained(draft_units: dict[str, str], intents: list[dict], ctx: dict) -> dict[str, str]:
    out = dict(draft_units)
    for intent in intents:
        target = intent["target"]
        if target not in out:
            continue
        ok, _ = _apply_guards(target, intent, ctx)
        if not ok:
            continue
        before = out[target]
        after = str(intent["new_value"])
        # Locate the stale sentence and splice in the corrected one, confined to
        # THIS unit. Writing only out[target] means no other unit can change
        # (0 unintended by construction); the splice preserves the surrounding
        # sentences a naive whole-unit replace would drop.
        out[target] = _splice_sentence(before, after)
    return out


# --------------------------------------------------------------------------
# T4 — micro-model edit-tagger (GECToR tag-not-rewrite, deterministic)
# --------------------------------------------------------------------------

def t4_edit_tagger(draft_units: dict[str, str], intents: list[dict], ctx: dict) -> dict[str, str]:
    out = dict(draft_units)
    for intent in intents:
        target = intent["target"]
        if target not in out:
            continue
        ok, _ = _apply_guards(target, intent, ctx)
        if not ok:
            continue
        before = out[target]
        after = str(intent["new_value"])
        sents = _sentences(before)
        if len(sents) <= 1:
            # Single-unit (field/one-liner): tag the whole unit.
            out[target] = _tag_and_apply(before, after)
        else:
            # Multi-sentence: locate the target sentence, apply token-level tags
            # to THAT sentence only, keep the rest verbatim (tag-not-rewrite).
            best_i, best_r = 0, -1.0
            for i, s in enumerate(sents):
                r = difflib.SequenceMatcher(a=_norm(s), b=_norm(after)).ratio()
                if r > best_r:
                    best_i, best_r = i, r
            sents[best_i] = _tag_and_apply(sents[best_i], after)
            out[target] = " ".join(sents)
    return out


# Token-level edit tags, GECToR-style.
_KEEP, _DELETE, _REPLACE, _APPEND = "KEEP", "DELETE", "REPLACE", "APPEND"


def tag_tokens(before: str, after: str) -> list[tuple[str, str]]:
    """Produce the token-level edit-tag sequence that turns `before` into
    `after` (the micro-model's output representation). Exposed for inspection /
    the alpha loop's audit."""
    bt, at = before.split(), after.split()
    sm = difflib.SequenceMatcher(a=bt, b=at)
    tags: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            tags += [(_KEEP, t) for t in bt[i1:i2]]
        elif tag == "delete":
            tags += [(_DELETE, t) for t in bt[i1:i2]]
        elif tag == "replace":
            tags += [(_REPLACE, t) for t in at[j1:j2]]
        else:  # insert
            tags += [(_APPEND, t) for t in at[j1:j2]]
    return tags


def _tag_and_apply(before: str, after: str) -> str:
    """Apply KEEP/DELETE/REPLACE/APPEND tags to the token stream. Tokens tagged
    KEEP are preserved verbatim (never regenerated); only DELETE/REPLACE/APPEND
    change text. This is the structural no-over-correction property."""
    result: list[str] = []
    for op, tok in tag_tokens(before, after):
        if op == _KEEP:
            result.append(tok)
        elif op in (_REPLACE, _APPEND):
            result.append(tok)
        # DELETE -> drop
    return " ".join(result)


# --------------------------------------------------------------------------
# T3 — model refinement (gpt-oss-120b)
# --------------------------------------------------------------------------

_MODEL_SYSTEM = (
    "You apply ONE reviewer correction to ONE unit of a document. Return ONLY "
    "the corrected text of that unit — no preamble, no quotes, no explanation. "
    "Change exactly what the correction asks and keep everything else identical. "
    "Use only the value the correction states; never invent facts."
)


def make_t3_model(model_client):
    def t3_model(draft_units: dict[str, str], intents: list[dict], ctx: dict) -> dict[str, str]:
        out = dict(draft_units)
        if model_client is None:
            return out   # unavailable -> no-op (recorded as skipped upstream)
        for intent in intents:
            target = intent["target"]
            if target not in out:
                continue
            ok, _ = _apply_guards(target, intent, ctx)
            if not ok:
                continue
            before = out[target]
            instruction = (intent.get("bodies") or [""])[0] or \
                f"Set this to: {intent['new_value']}"
            user = (f"UNIT (current text):\n{before}\n\n"
                    f"CORRECTION: {instruction}\n"
                    f"Stated value: {intent['new_value']}\n\n"
                    "Return the corrected unit text only.")
            text = model_client.complete(_MODEL_SYSTEM, user, max_tokens=256)
            if not text:
                continue
            cand = text.strip().strip('"').strip()
            # Grounding gate: the corrected value must still be present.
            if _norm(str(intent["new_value"])) in _norm(cand) or \
               _norm(cand) == _norm(str(intent["new_value"])):
                out[target] = cand
            else:
                # Model drifted off the stated value -> fall back to the exact
                # grounded replace rather than accept an ungrounded rewrite.
                out[target] = str(intent["new_value"])
        return out
    return t3_model


def techniques(model_client=None) -> dict:
    return {
        "T1-anchored": t1_anchored_replace,
        "T2-diff": t2_diff_constrained,
        "T3-model": make_t3_model(model_client),
        "T4-micro": t4_edit_tagger,
    }
