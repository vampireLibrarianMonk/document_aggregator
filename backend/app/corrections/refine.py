"""Correction refinement: the command-center bake-off winner, integrated.

The deterministic reconcile engine is the always-on baseline. Before it runs,
the loaded corrections pass through two refinements proven in the bake-off
(see docs/testing/bakeoff-command-center.md):

1. `ground_graphic_relabels` — deterministic corpus grounding of figure-relabel
   intent. A reviewer email may say "the Timeline chart is wrong, it should be
   the packet-loss-versus-temperature figure" WITHOUT naming the file. The
   correct filename is in the corpus graphics manifest, not the prose, so we
   resolve the reviewer's words to the exact manifest filename and emit a
   grounded relabel op. The filename comes verbatim from the corpus — nothing is
   invented; if no single confident match exists we emit nothing.

2. `refine_corrections` — the Option-B orchestration. The structured corrections
   always flow through. When Bedrock is enabled AND raw reviewer feedback is
   present, the feedback interpreter proposes additional grounded operations on
   top (validated + target-checked); it never replaces the deterministic engine
   and can only ADD ops that pass validation. Offline, this step is a no-op and
   the pipeline runs on the structured corrections alone.

Both steps preserve the hard invariants: no fabrication, genuine conflicts are
never collapsed (disagreeing corrections on one target are passed through
untouched so the engine still sees the conflict), and the deterministic path is
unchanged when no refinement applies.
"""

from __future__ import annotations

import re

from ..config import settings

# Figure-relabel intent detection + grounding vocabulary (from the bake-off).
_RELABEL_INTENT = ("figure", "chart", "image", "graphic", "diagram", "plot")
_RELABEL_CUES = (
    "wrong",
    "should be",
    "should ",
    "incorrect",
    "swap",
    "replace",
    "not the right",
    "mislabel",
    "wrong reference",
    "wrong image",
)
# Generic English/review filler only — NOT caption-specific words, so matching
# stays honest (we don't hand-tune tokens toward any particular caption).
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "vs",
    "versus",
    "and",
    "to",
    "in",
    "for",
    "is",
    "it",
    "that",
    "this",
    "with",
    "by",
    "on",
    "at",
    "as",
    "or",
    "be",
    "its",
    "into",
    "from",
    "subject",
    "reviewer",
    "please",
    "should",
    "swap",
    "not",
    "now",
    "there",
    "what",
    "one",
    "correct",
    "right",
    "wrong",
    "reference",
    "referenced",
    "section",
    "figure",
    "chart",
    "image",
    "graphic",
    "diagram",
    "plot",
    "png",
}
_HEADER = re.compile(r"^(from|subject|to|cc|date):.*$", re.IGNORECASE | re.MULTILINE)


def _tok(text: str) -> set[str]:
    """Content tokens of an email/feedback body (headers + filler stripped)."""
    body = _HEADER.sub("", text or "")
    return {w for w in re.split(r"[^a-z0-9]+", body.lower()) if w and len(w) > 1 and w not in _STOP}


def ground_graphic_relabels(
    corrections: list[dict], *, graphics: list[dict], template: dict, feedback_texts: list[str]
) -> list[dict]:
    """Resolve prose figure-relabel intent to the exact corpus filename and emit
    grounded relabel ops. Deterministic, offline, no fabrication.

    Returns the original corrections plus any grounded relabel ops. Idempotent:
    a target that already has a resolved (.png) relabel is left alone; feedback
    that already names a .png is handled by the normal structured path.
    """
    if not graphics or not feedback_texts:
        return corrections

    sections = [s.get("key") for s in template.get("required_sections", [])]
    heading_tok = {
        s.get("key"): _tok(str(s.get("heading", "")))
        for s in template.get("required_sections", [])
        if s.get("key")
    }

    have = {
        c.get("target")
        for c in corrections
        if c.get("operation") in ("relabel_graphic", "relocate_graphic")
        and str(c.get("new_value", "")).endswith(".png")
    }

    added: list[dict] = []
    cid = len(corrections) + 1
    for text in feedback_texts:
        low = (text or "").lower()
        if not any(w in low for w in _RELABEL_INTENT):
            continue
        if not any(cue in low for cue in _RELABEL_CUES):
            continue
        if re.search(r"\b[a-z0-9_]+\.png\b", low):
            continue  # a literal filename is present; structured path handles it
        prose = _tok(text)
        if not prose:
            continue
        # Resolve the referenced section: slug spelled out, then heading words,
        # else leave unset and score all graphics globally (dominance guard
        # keeps it honest).
        sec = next((k for k in sections if k and k.replace("_", " ") in low), None)
        if sec is None:
            sec = next((k for k, ht in heading_tok.items() if ht and len(prose & ht) >= 1), None)
        candidates = (
            [g for g in graphics if g.get("belongs_in_section") == sec]
            if sec is not None
            else list(graphics)
        )
        if not candidates:
            continue
        best, best_n, runner_n, best_sec = None, 0, 0, None
        for g in candidates:
            gtok = (
                _tok(g.get("caption", ""))
                | _tok(g.get("title", ""))
                | _tok(str(g.get("name", "")).replace(".png", "").replace("_", " "))
            )
            if not gtok:
                continue
            n = len(prose & gtok)
            if n > best_n:
                best, runner_n, best_n = g, best_n, n
                best_sec = g.get("belongs_in_section")
            elif n > runner_n:
                runner_n = n
        # Confident, dominant match of >=2 shared content words, else skip.
        if best is None or best_n < 2 or best_n <= runner_n:
            continue
        sec = sec or best_sec
        if sec is None:
            continue
        target = f"{sec}.graphic"
        if target in have:
            continue
        added.append(
            {
                "id": f"gfxgnd_{cid}",
                "round": 0,
                "kind": "email",
                "author": "reviewer",
                "subject": "wrong figure (grounded)",
                "target": target,
                "operation": "relabel_graphic",
                "old_value": "",
                "new_value": best["name"],
                "body": f"Resolved figure reference to corpus graphic "
                f"'{best['name']}' (grounded from the corpus, not invented).",
            }
        )
        have.add(target)
        cid += 1
    return corrections + added


def _interp_context(manifest: dict, template: dict) -> dict:
    return {
        "fields": manifest.get("fields", []),
        "section_bodies": manifest.get("section_bodies", {}),
        "sections": [s["key"] for s in template.get("required_sections", [])],
        "graphic_sections": [
            s["key"] for s in template.get("required_sections", []) if s.get("requires_graphic")
        ],
        "table_section": (manifest.get("table") or {}).get("section"),
    }


def refine_corrections(
    corrections: list[dict],
    *,
    manifest: dict,
    template: dict,
    graphics: list[dict],
    feedback_texts: list[str],
    corpus: dict[str, str] | None = None,
) -> list[dict]:
    """Apply the bake-off winner's refinements on top of the structured
    corrections, then return the merged list for the deterministic engine.

    Order: (1) always ground prose figure-relabels against the corpus; (2) when
    Bedrock is enabled and feedback is present, let the interpreter propose
    additional validated ops and merge the accepted ones. Never removes or
    rewrites an existing correction, so genuine conflicts survive untouched.

    `corpus` is used only to apply the bake-off's grounding gate to
    MODEL-proposed ops: a model value that appears nowhere in the corpus or the
    reviewer feedback is dropped, so the model can never introduce an ungrounded
    value even for a field the corpus does not cover. Structured/human
    corrections keep their existing trust (a reviewer may legitimately supply a
    value the corpus lacks).
    """
    out = ground_graphic_relabels(
        corrections, graphics=graphics, template=template, feedback_texts=feedback_texts
    )

    if not settings.BEDROCK_ENABLED or not feedback_texts:
        return out

    # Model refinement ON TOP (Option B). Import locally so offline installs that
    # never enable Bedrock don't pay the import.
    from .interpreter import get_interpreter, interpret_feedback
    from .schema import to_engine_correction

    # Only augment when a real MODEL interpreter is active. If Bedrock is enabled
    # but unreachable, get_interpreter() falls back to the crude offline rule
    # heuristic; we deliberately do NOT let that inject corrections, so an
    # unreachable model degrades to exactly the deterministic result rather than
    # to surprising low-quality guesses.
    if getattr(get_interpreter(), "name", "") == "rule-based (offline)":
        return out

    ctx = _interp_context(manifest, template)
    grounding = _norm_blob((corpus or {}).values(), feedback_texts)
    existing_targets = {c.get("target") for c in out}
    extra: list[dict] = []
    for text in feedback_texts:
        try:
            result = interpret_feedback(text, ctx, round_index=0, author="interpreter")
        except Exception:
            continue  # model unavailable mid-flight -> deterministic result stands
        for op in result.get("accepted", []):
            eng = to_engine_correction(op)
            # Only ADD ops for targets the structured corrections didn't already
            # cover, so the model augments rather than overrides, and a genuine
            # conflict (two structured corrections on one target) is never
            # silently resolved by a model proposal.
            if eng.get("target") in existing_targets:
                continue
            # No-fabrication gate for model ops (the bake-off's _ground_and_validate):
            # drop a model-proposed value that is grounded nowhere. Relabel/flag
            # ops carry a corpus-manifest name or no value and are exempt.
            nv = eng.get("new_value")
            if eng.get("operation") == "replace" and nv:
                if _norm_one(nv) not in grounding:
                    continue
            extra.append(eng)
            existing_targets.add(eng.get("target"))
    return out + extra


def _norm_one(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_blob(corpus_values, feedback_texts: list[str]) -> str:
    parts = [str(v) for v in corpus_values] + list(feedback_texts or [])
    return _norm_one("\n".join(parts))
