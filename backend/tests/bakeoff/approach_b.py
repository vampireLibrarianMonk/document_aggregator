"""Approach B — governor/model structuring.

A model (Bedrock, when available) reads each raw reviewer email and produces
GROUNDED, VALIDATED correction operations against the real resolvable targets,
using the existing tool-use interpreter (which structurally cannot propose a
value outside the fixed schema and rejects ungrounded/off-target ops). Those
ops are then applied by the deterministic reconcile() engine — the engine stays
authoritative, so no fabrication can slip through. Offline, it degrades to the
rule interpreter (same path, weaker extraction).

This is the "governor does the real structuring" path: the model handles the
hard natural-language -> structured-op step the deterministic parser struggles
with, while grounding + application remain deterministic.
"""
from __future__ import annotations

from app.convert import convert_document  # noqa: E402
from app.corrections.interpreter import (
    BedrockInterpreter,
    RuleInterpreter,
    _valid_targets,
)
from app.corrections.schema import to_engine_correction, validate_op
from app.reconcile import reconcile

from .approach_a import _derive_manifest, _derive_template, _parse_email, parse_docx  # noqa: F401
from .harness import RawInputs


def _context(manifest: dict, template: dict) -> dict:
    return {
        "fields": manifest.get("fields", []),
        "section_bodies": manifest.get("section_bodies", {}),
        "sections": [s["key"] for s in template.get("required_sections", [])],
        "graphic_sections": [s["key"] for s in template.get("required_sections", [])],
        "table_section": (manifest.get("table") or {}).get("section"),
    }


def run(raw: RawInputs, *, use_model: bool) -> tuple[dict, bool]:
    """Returns (report, used_model). use_model picks the Bedrock interpreter;
    False (or any failure) uses the offline rule interpreter."""
    template = _derive_template(raw.template_docx)
    draft_fa: dict = {"artifact_kind": "draft", "sections": []}
    if raw.draft_docx:
        try:
            res = convert_document(raw.draft_docx, "draft.docx", template,
                                   {"fields": [], "section_bodies": {}}, "draft")
            draft_fa = res.first_attempt
        except Exception:
            pass
    manifest = _derive_manifest(template, draft_fa)
    ctx = _context(manifest, template)
    valid = set(_valid_targets(ctx))

    interp = None
    used_model = False
    if use_model:
        try:
            interp = BedrockInterpreter()
            used_model = True
        except Exception:
            interp = RuleInterpreter()
    else:
        interp = RuleInterpreter()

    corrections: list[dict] = []
    rnd = 0
    for raw_email in raw.corrections_emails:
        e = _parse_email(raw_email)
        try:
            ops = interp.interpret(e["body"], ctx)
        except Exception:
            ops = RuleInterpreter().interpret(e["body"], ctx)
            used_model = False
        for i, op in enumerate(ops):
            op.setdefault("id", f"b_{rnd}_{i}")
            op["round"] = 0
            op["author"] = e["author"]
            ok, _reason = validate_op(op)
            tgt = op.get("target", "")
            base = tgt.split("[", 1)[0]
            if not ok or (tgt not in valid and base not in valid):
                continue  # drop invalid / off-target (no fabrication)
            corrections.append(to_engine_correction(op))
        rnd += 1

    report = reconcile(
        first_attempt=draft_fa,
        corpus=raw.corpus,
        graphics_manifest=[],
        corrections=corrections,
        template=template,
        project=manifest,
    )
    return report.model_dump(), used_model
