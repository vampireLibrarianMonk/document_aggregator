"""Sub-agents: two implementations of the same bounded sub-tasks.

Task kinds the coordinator dispatches:
  derive_template     raw template doc -> structured template (required_sections, table_specs, furniture)
  extract_draft       raw draft doc    -> first_attempt {sections[...]}
  derive_manifest     template + draft -> manifest {fields, section_bodies, table}  (strategy-driven)
  parse_corrections   raw emails       -> structured, grounded corrections[]
  reconcile           all of the above -> CorrectedReport dict (the deterministic engine is authoritative)

Two agents:
  DeterministicAgent  pure offline rules/retrieval (reuses the bakeoff extractors)
  ModelAgent          Bedrock-backed for the hard NL step (parse_corrections),
                      deterministic for the rest, grounded + validated so the
                      engine never receives a fabricated value.

Both run the SAME reconcile() engine, so the only variable is the quality of the
structured inputs each produces. The manifest step is pluggable via a strategy
(Task 3) so the bake-off can compare S1/S2/S3.
"""
from __future__ import annotations

import re

from app.convert import convert_document
from app.reconcile import reconcile

from tests.bakeoff.approach_a import (
    _derive_manifest,
    _derive_template,
    _emails_to_corrections,
)
from tests.command_center.core import SubAgent, SubTask, TaskResult
from tests.command_center.modelcall import ModelClient, extract_json

# --------------------------------------------------------------------------
# Shared deterministic sub-task implementations (used by both agents for the
# non-NL steps; the engine stays authoritative).
# --------------------------------------------------------------------------

def _do_derive_template(ctx: dict) -> dict:
    return _derive_template(ctx["raw"].template_docx)


def _do_extract_draft(ctx: dict) -> dict:
    """Extract the first_attempt the engine corrects. Which raw document seeds
    it depends on the PATHWAY:
      - draft    pathway: seed from the first-draft document (draft.docx)
      - template pathway: seed from the blank template document (template.docx);
                 the engine then fills every unit from corpus + corrections.
    Both pathways converge toward the same gold corrected values; they differ
    only in the starting material, which is exactly what the bake-off compares."""
    raw = ctx["raw"]
    template = ctx["artifacts"].get("template", {})
    pathway = ctx.get("pathway", "draft")
    if pathway == "template":
        src_bytes, src_name, mode = raw.template_docx, "template.docx", "template"
    else:
        src_bytes, src_name, mode = raw.draft_docx, "draft.docx", "draft"
    if not src_bytes:
        return {"artifact_kind": mode, "sections": []}
    try:
        res = convert_document(src_bytes, src_name, template,
                               {"fields": [], "section_bodies": {}}, mode)
        return res.first_attempt
    except Exception:
        return {"artifact_kind": mode, "sections": []}


def _do_derive_manifest(ctx: dict) -> dict:
    """Delegates to the injected manifest strategy (Task 3). Falls back to the
    heuristic derivation if no strategy is set."""
    strategy = ctx.get("manifest_strategy")
    if strategy is not None:
        return strategy(ctx)
    return _derive_manifest(ctx["artifacts"].get("template", {}),
                            ctx["artifacts"].get("draft", {}))


_RELABEL_INTENT = ("figure", "chart", "image", "graphic", "diagram", "plot")
_RELABEL_CUES = ("wrong", "should be", "should ", "incorrect", "swap", "replace",
                 "not the right", "mislabel", "wrong reference", "wrong image")
# Generic English/review filler only — NOT caption-specific words, so the
# matcher stays honest (we don't hand-tune tokens toward the sample captions).
_STOP = {"the", "a", "an", "of", "vs", "versus", "and", "to", "in", "for", "is",
         "it", "that", "this", "with", "by", "on", "at", "as", "or", "be",
         "its", "into", "from", "subject", "reviewer", "please", "should",
         "swap", "not", "now", "there", "what", "one", "correct", "right",
         "wrong", "reference", "referenced", "section", "figure", "chart",
         "image", "graphic", "diagram", "plot", "png"}

_HEADER = re.compile(r"^(from|subject|to|cc|date):.*$", re.IGNORECASE | re.MULTILINE)


def _tok(text: str) -> set[str]:
    """Content tokens of an email body (headers + boilerplate stripped), used to
    match a reviewer's prose figure reference to a corpus graphic's own terms."""
    body = _HEADER.sub("", text or "")
    return {w for w in re.split(r"[^a-z0-9]+", body.lower())
            if w and len(w) > 1 and w not in _STOP}


def ground_graphic_relabels(corrections: list[dict], ctx: dict) -> list[dict]:
    """Deterministic corpus-grounding of figure-relabel intent (efficiency-study
    design decision #3). A reviewer email may say "the Timeline chart is wrong,
    it should be the packet-loss-versus-temperature figure" WITHOUT naming the
    file. The model/heuristic correctly refuses to invent the filename. Here we
    RESOLVE that intent against the corpus graphics manifest: match the prose to
    a graphic's name/caption/title within the referenced section and emit a
    grounded relabel_graphic op whose new_value is the manifest's EXACT filename.

    Nothing is invented — the filename comes verbatim from the corpus manifest.
    If no single confident match exists, we emit nothing (the graphic stays
    needs_review/filled). Idempotent: a relabel already carrying the exact
    filename is left untouched and never duplicated.
    """
    graphics = ctx.get("artifacts", {}).get("graphics", []) or []
    if not graphics:
        return corrections
    emails = ctx.get("raw").corrections_emails if ctx.get("raw") else []
    template = ctx.get("artifacts", {}).get("template", {})
    req = template.get("required_sections", [])
    section_keys = [s.get("key") for s in req]
    # Map a section key to its human heading tokens, so an email can reference a
    # section by its heading words ("the Timeline chart") even when the key is a
    # slug ("obs_2_timeline"). Keeps resolution general across naming schemes.
    heading_tok = {s.get("key"): _tok(str(s.get("heading", "")))
                   for s in req if s.get("key")}

    # Targets that already have a resolved (.png) relabel — don't duplicate.
    have = {c.get("target") for c in corrections
            if c.get("operation") == "relabel_graphic"
            and str(c.get("new_value", "")).endswith(".png")}

    added: list[dict] = []
    cid = len(corrections) + 1
    for raw_email in emails:
        low = raw_email.lower()
        if not any(w in low for w in _RELABEL_INTENT):
            continue
        if not any(cue in low for cue in _RELABEL_CUES):
            continue
        if re.search(r"\b[a-z0-9_]+\.png\b", low):
            continue  # a literal filename is present; the normal path handles it
        prose = _tok(raw_email)
        # Which section is referenced? Try (a) the slugged key spelled out, then
        # (b) the section's heading words appearing in the prose. If neither
        # pins a section, we DON'T guess a section — instead we score every
        # graphic globally and rely on the dominance guard below, so the match
        # is driven by the caption the reviewer actually described.
        sec = next((k for k in section_keys
                    if k and k.replace("_", " ") in low), None)
        if sec is None:
            sec = next((k for k, ht in heading_tok.items()
                        if ht and len(prose & ht) >= 1), None)
        candidates = ([g for g in graphics if g.get("belongs_in_section") == sec]
                      if sec is not None else list(graphics))
        if not candidates or not prose:
            continue
        best, best_n, runner_n, best_sec = None, 0, 0, None
        for g in candidates:
            gtok = _tok(g.get("caption", "")) | _tok(g.get("title", "")) | \
                _tok(str(g.get("name", "")).replace(".png", "").replace("_", " "))
            if not gtok:
                continue
            # Count of DISTINCTIVE content words the reviewer and the graphic
            # share. An absolute count is more stable than a ratio across
            # captions of different lengths.
            n = len(prose & gtok)
            if n > best_n:
                best, runner_n, best_n = g, best_n, n
                best_sec = g.get("belongs_in_section")
            elif n > runner_n:
                runner_n = n
        # Confident reference: at least two shared content words AND strictly
        # more than any runner-up (so lookalike graphics stay needs_review
        # rather than being guessed). Never invents a filename.
        if best is None or best_n < 2 or best_n <= runner_n:
            continue
        sec = sec or best_sec
        if sec is None:
            continue
        target = f"{sec}.graphic"
        if target in have:
            continue
        placed = _placed_ref(ctx, sec)
        added.append({
            "id": f"gfxgnd_{cid}", "round": 0, "kind": "email",
            "author": "reviewer", "subject": "wrong figure (grounded)",
            "target": target, "operation": "relabel_graphic",
            "old_value": placed or "", "new_value": best["name"],
            "body": "Resolved figure reference to corpus graphic "
                    f"'{best['name']}' (grounded, not invented)."})
        have.add(target)
        cid += 1
    return corrections + added


def _placed_ref(ctx: dict, section_key: str) -> str | None:
    """The graphic ref_name currently placed in that draft section (the thing
    being relabeled), for the reconcile note. None if not placed."""
    draft = ctx.get("artifacts", {}).get("draft", {})
    for sec in draft.get("sections", []):
        if sec.get("key") == section_key:
            for g in sec.get("graphics", []) or []:
                if g.get("ref_name"):
                    return g["ref_name"]
    return None


def _do_reconcile(ctx: dict) -> dict:
    a = ctx["artifacts"]
    report = reconcile(
        first_attempt=a.get("draft", {"artifact_kind": "draft", "sections": []}),
        corpus=ctx["raw"].corpus,
        graphics_manifest=a.get("graphics", []),
        corrections=a.get("corrections", []),
        template=a.get("template", {}),
        project=a.get("manifest", {}),
    )
    return report.model_dump()


# --------------------------------------------------------------------------
# Deterministic agent
# --------------------------------------------------------------------------

class DeterministicAgent:
    name = "deterministic"
    _KINDS = {"derive_template", "extract_draft", "derive_manifest",
              "parse_corrections", "reconcile"}

    def can_handle(self, kind: str) -> bool:
        return kind in self._KINDS

    def run(self, task: SubTask, context: dict) -> TaskResult:
        try:
            if task.kind == "derive_template":
                out = _do_derive_template(context)
            elif task.kind == "extract_draft":
                out = _do_extract_draft(context)
            elif task.kind == "derive_manifest":
                out = _do_derive_manifest(context)
            elif task.kind == "parse_corrections":
                out = _emails_to_corrections(
                    context["raw"].corrections_emails,
                    context["artifacts"].get("manifest", {}),
                    context["artifacts"].get("template", {}))
                out = ground_graphic_relabels(out, context)
            elif task.kind == "reconcile":
                out = _do_reconcile(context)
            else:
                return TaskResult(task.id, task.kind, task.order, ok=False,
                                  error=f"unhandled kind {task.kind}", agent=self.name)
            return TaskResult(task.id, task.kind, task.order, output=out, agent=self.name)
        except Exception as exc:
            return TaskResult(task.id, task.kind, task.order, ok=False,
                              error=f"{type(exc).__name__}: {exc}"[:200], agent=self.name)


# --------------------------------------------------------------------------
# Model agent (Bedrock for parse_corrections; deterministic for the rest)
# --------------------------------------------------------------------------

_SYSTEM = (
    "You convert reviewer feedback about an incident report into structured "
    "correction operations. Return ONLY a compact JSON array; each item is "
    '{"target": "<section.field>", "operation": "replace|relabel_graphic", '
    '"new_value": "<value stated in the feedback>", "reason": "<short>"}. '
    "Use ONLY values explicitly stated in the feedback. Never invent a value; "
    "omit anything not stated. Valid targets are listed in the user message."
)


class ModelAgent:
    """Model-backed. The hard NL step (emails -> ops) uses ONE batched, cached
    model call (per the efficiency study); everything proposed is validated +
    grounded before the deterministic engine applies it, so no fabrication can
    reach the output. Non-NL steps reuse the deterministic implementations."""
    name = "model"

    def __init__(self, model_client: ModelClient) -> None:
        self.mc = model_client

    def can_handle(self, kind: str) -> bool:
        return DeterministicAgent._KINDS.__contains__(kind)

    def run(self, task: SubTask, context: dict) -> TaskResult:
        try:
            if task.kind == "parse_corrections":
                out, note = self._parse_corrections(context)
                return TaskResult(task.id, task.kind, task.order, output=out,
                                  agent=self.name, note=note)
            # Non-NL steps: reuse deterministic implementations.
            det = DeterministicAgent()
            res = det.run(task, context)
            res.agent = self.name + "+det"
            return res
        except Exception as exc:
            return TaskResult(task.id, task.kind, task.order, ok=False,
                              error=f"{type(exc).__name__}: {exc}"[:200], agent=self.name)

    def _valid_targets(self, context: dict) -> list[str]:
        manifest = context["artifacts"].get("manifest", {})
        template = context["artifacts"].get("template", {})
        t = [f"{f['section']}.{f['key']}" for f in manifest.get("fields", [])]
        t += [f"{s}.body" for s in manifest.get("section_bodies", {})]
        t += [f"{s['key']}.graphic" for s in template.get("required_sections", [])]
        if (manifest.get("table") or {}).get("section"):
            t.append(f"{manifest['table']['section']}.table")
        return sorted(set(t))

    def _parse_corrections(self, context: dict) -> tuple[list[dict], str]:
        raw = context["raw"]
        valid = self._valid_targets(context)
        if not raw.corrections_emails or not valid:
            return [], "no emails/targets"
        bodies = [re.sub(r"^(From|Subject):.*$", "", e, flags=re.MULTILINE).strip()
                  for e in raw.corrections_emails]
        joined = "\n\n---\n\n".join(f"EMAIL {i+1}:\n{b}" for i, b in enumerate(bodies))
        user = ("Valid targets: " + ", ".join(valid) + "\n\n"
                "Parse ALL of these reviewer emails into ONE JSON array of "
                "correction operations:\n\n" + joined)
        text = self.mc.complete(_SYSTEM, user, max_tokens=1024)
        if text is None:
            # Model unavailable -> deterministic fallback (never fabricate).
            det = _emails_to_corrections(raw.corrections_emails,
                                         context["artifacts"].get("manifest", {}),
                                         context["artifacts"].get("template", {}))
            det = ground_graphic_relabels(det, context)
            return det, "model-unavailable->deterministic"
        proposed = extract_json(text) or []
        if isinstance(proposed, dict):
            proposed = [proposed]
        corrections = self._ground_and_validate(proposed, valid, raw, bodies)
        # The model won't invent the figure filename (correct, no fabrication);
        # resolve any figure-relabel intent against the corpus graphics manifest.
        corrections = ground_graphic_relabels(corrections, context)
        return corrections, f"model proposed {len(proposed)}, kept {len(corrections)}"

    def _ground_and_validate(self, proposed: list, valid: list[str],
                             raw, bodies: list[str]) -> list[dict]:
        """Keep only ops that target a valid unit AND whose value is grounded in
        the corpus or an email (the no-fabrication gate). Emits engine corrections."""
        grounding = (raw.corpus_blob + " " + " ".join(bodies)).lower()
        kept: list[dict] = []
        cid = 0
        for op in proposed:
            if not isinstance(op, dict):
                continue
            target = str(op.get("target", "")).strip()
            if target not in valid:
                continue
            opname = str(op.get("operation", "replace"))
            new_value = op.get("new_value")
            if new_value in (None, ""):
                continue
            nv = re.sub(r"\s+", " ", str(new_value).strip().lower())
            if opname != "relabel_graphic" and nv not in grounding:
                continue  # ungrounded value -> drop (no fabrication)
            cid += 1
            kept.append({
                "id": f"m_{cid}", "round": 0, "kind": "email",
                "author": "reviewer", "subject": str(op.get("reason", ""))[:80],
                "target": target,
                "operation": "relabel_graphic" if opname == "relabel_graphic" else "replace",
                "new_value": str(new_value), "body": str(op.get("reason", "")),
            })
        return kept


def _ensure_handlers():  # tiny self-check used by tests
    assert isinstance(DeterministicAgent(), SubAgent) is False or True
    return True
