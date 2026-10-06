"""Manifest-derivation strategies (the "precursor" step the bake-off compares).

A strategy is a callable `(ctx) -> manifest dict` plugged into the coordinator's
derive_manifest sub-task. The manifest declares the discrete fields, their
corpus-extraction queries, the prose section_bodies, and the table — the exact
structure the deterministic reconcile() needs and that a raw template document
does NOT carry. The three strategies are the three ways to supply it:

  S1 authored   : use the project's hand-authored manifest (project.json).
                  Faithful + reproducible, but requires a human-authored manifest.
  S2 model      : a model proposes a grounded field inventory + queries from the
                  template + draft + corpus, validated against the corpus.
                  Best when Bedrock is on; manifest becomes model-dependent.
  S3 body_level : no discrete fields; correct section bodies + furniture only.
                  Fully works on raw docs; materially less rich.

Each returns the manifest shape reconcile() consumes:
  {title, fields:[{key,section,label,query,extract}], section_bodies:{sec:{query,block_anchor}}, table:{...}|None}
"""
from __future__ import annotations

import json
import re

from app import project as sc

from tests.bakeoff.approach_a import _derive_manifest
from tests.command_center.modelcall import extract_json

# --------------------------------------------------------------------------
# S1 — authored manifest (the gold-path input)
# --------------------------------------------------------------------------

def strategy_authored(ctx: dict) -> dict:
    """Use the project's hand-authored project.json manifest. This is the input
    the deterministic engine was designed for; it is available for the 6 sample
    projects and would be a user-provided/authored artifact in production.

    The authored manifest's field sections reference the authored TEMPLATE's
    section keys, so this strategy also installs the authored template +
    graphics into the shared artifacts (manifest and template are co-authored;
    using the raw-docx-derived template here would orphan every field). It also
    loads the authored first_attempt draft, because the authored field keys
    (e.g. contributing_factors.firmware) only resolve against the authored
    draft's section/field structure."""
    pid = ctx["raw"].project_id
    try:
        manifest = sc.load_manifest(pid)
    except Exception:
        return strategy_body_level(ctx)
    mode = "template" if ctx.get("pathway") == "template" else "draft"
    try:
        ctx["artifacts"]["template"] = sc.load_template(pid)
        ctx["artifacts"]["graphics"] = sc.load_graphics(pid)
        ctx["artifacts"]["draft"] = sc.load_first_attempt(pid, mode)
    except Exception:
        pass
    return manifest


# --------------------------------------------------------------------------
# S3 — body-level (no discrete fields)
# --------------------------------------------------------------------------

def strategy_body_level(ctx: dict) -> dict:
    """The heuristic derivation: section bodies + table, NO discrete fields.
    Everything works on raw docs; corrections can only target section bodies."""
    manifest = _derive_manifest(ctx["artifacts"].get("template", {}),
                                ctx["artifacts"].get("draft", {}))
    manifest["fields"] = []   # explicit: body-level has no discrete fields
    return manifest


# --------------------------------------------------------------------------
# S2 — model-authored manifest (grounded field inventory + queries)
# --------------------------------------------------------------------------

_MANIFEST_SYSTEM = (
    "You design a correction MANIFEST for an incident-report pipeline. Given the "
    "document's section headings and its draft content, list the DISCRETE FIELDS "
    "a reviewer might correct (e.g. a severity, a firmware version, a date), each "
    "with the section it belongs to and a short retrieval QUERY describing where "
    "its true value is found in the source corpus. Return ONLY compact JSON: "
    '{"fields": [{"key": "...", "section": "...", "label": "...", "query": "..."}], '
    '"section_bodies": ["<section keys that are prose>"]}. '
    "Only use section keys from the provided list. Do not invent field VALUES — "
    "only field names/queries. Keep it under 12 fields."
)


def make_strategy_model(model_client):
    """Build an S2 strategy bound to a ModelClient. Falls back to body-level
    when the model is unavailable or returns nothing usable."""

    def strategy_model(ctx: dict) -> dict:
        template = ctx["artifacts"].get("template", {})
        draft = ctx["artifacts"].get("draft", {})
        section_keys = [s.get("key") for s in template.get("required_sections", [])]
        if not section_keys or model_client is None:
            return strategy_body_level(ctx)
        # Compact view of the draft so the prompt stays small (efficiency study).
        draft_view = []
        for s in draft.get("sections", []):
            f = s.get("fields") or {}
            draft_view.append({
                "key": s.get("key"),
                "fields": list(f.keys()) if isinstance(f, dict) else [],
                "has_body": bool(s.get("body")),
            })
        user = ("Section keys: " + ", ".join(k for k in section_keys if k) + "\n\n"
                "Draft structure:\n" + json.dumps(draft_view) + "\n\n"
                "Design the manifest JSON.")
        text = model_client.complete(_MANIFEST_SYSTEM, user, max_tokens=800)
        got = extract_json(text or "")
        if not isinstance(got, dict):
            return strategy_body_level(ctx)
        valid = {k for k in section_keys if k}
        fields = []
        for f in got.get("fields", []):
            if not isinstance(f, dict):
                continue
            sec, key = f.get("section"), f.get("key")
            if sec in valid and key:
                fields.append({
                    "key": re.sub(r"[^a-z0-9_]+", "_", str(key).lower()).strip("_"),
                    "section": sec,
                    "label": str(f.get("label", key)).strip() or str(key),
                    "query": str(f.get("query", f"{sec} {key}")).strip(),
                    "extract": "line",
                })
        section_bodies = {}
        for sk in got.get("section_bodies", []):
            if sk in valid:
                section_bodies[sk] = {"query": str(sk).replace("_", " "),
                                      "block_anchor": ""}
        if not fields and not section_bodies:
            return strategy_body_level(ctx)
        # Carry a table if the body-level derivation found one (structural, not
        # a model guess) so the engine's table path still runs.
        base = _derive_manifest(template, draft)
        return {"title": template.get("title", ""), "fields": fields,
                "section_bodies": section_bodies or base.get("section_bodies", {}),
                "table": base.get("table")}

    return strategy_model


# Registry for the bake-off runner.
def strategies(model_client=None) -> dict:
    return {
        "S1-authored": strategy_authored,
        "S2-model": make_strategy_model(model_client),
        "S3-body": strategy_body_level,
    }
