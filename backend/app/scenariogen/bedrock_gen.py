"""Bedrock scenario generator (optional).

Asks an APPROVED model (Nemotron or GPT-OSS) to emit a complete ScenarioSpec as
strict JSON, then parses + validates it. On ANY failure (model not approved,
boto3/creds missing, bad JSON, invalid spec) it falls back to the deterministic
RuleScenarioGenerator, so the feature never breaks the system and the air-gap
posture holds. The model AUTHORS a scenario; it never touches the correction
path, and its output is validated before anything is persisted.
"""
from __future__ import annotations

import json
import re

from ..config import settings
from .generator import ScenarioBrief
from .model_adapters import adapter_for
from .rule_generator import RuleScenarioGenerator
from .schema import ScenarioSpec, salvage_spec, validate_spec


class ModelNotApprovedError(RuntimeError):
    pass


def get_scenario_model() -> str:
    """Resolve + enforce the approved scenario-generation model id."""
    model = settings.BEDROCK_SCENARIO_MODEL
    if not model:
        raise ModelNotApprovedError("BEDROCK_SCENARIO_MODEL is not set")
    allow = [a.strip().lower() for a in settings.BEDROCK_SCENARIO_MODEL_ALLOWLIST.split(",") if a.strip()]
    if not any(a in model.lower() for a in allow):
        raise ModelNotApprovedError(
            f"model '{model}' is not on the scenario allowlist {allow} "
            "(only Nemotron / GPT-OSS are approved)"
        )
    return model


_SPEC_CONTRACT = """You generate a document-correction SCENARIO as strict JSON.

Return ONE JSON object with these keys (no prose, no markdown fence):
  title (str), domain (str),
  required_sections: [{key, heading, requires_graphic?, requires_table?}],
  fields: [{key, label, section, extract, hint?}]  (extract in token|line|version|date|duration|none),
  section_bodies: [{section, query, block_anchor?}],
  table: {key, section, title, columns:[{name, corpus_label?, cell_query?}], font, header_style, query, row_marker} | null,
  corpus: [{name, text}]   (name ends .txt or .md; text is the GROUND TRUTH),
  graphics: [{graphic_id, name, caption, source_doc, belongs_in_section}]  (name ends .png),
  draft_title, draft_header, draft_footer, draft_classification (str), draft_page_numbers (bool),
  draft_sections: [{key, heading, fields?, body?, graphics?, table?}],
  cross_references: [{id, in_section, text, points_to_graphic?}],
  corrections: [{id, kind, author, subject, target, operation, old_value, new_value, body}],
  seeded_defects: [str]

HARD RULES (these are validated and your output is rejected if violated):
  1. NO FABRICATION. Every corrected value a correction asserts (new_value) MUST
     appear verbatim in the corpus text you write, OR be stated in that
     correction's own body. The draft carries the WRONG values; the corpus
     carries the RIGHT ones.
  2. Correction `operation` is one of: replace, relabel_graphic, flag.
  3. Correction `target` must be a real unit, written EXACTLY as:
       "<section>.<field_key>"   (field_key must be one you declared in fields)
       "<section>.graphic"       (the LITERAL word 'graphic', for figure fixes)
       "<section>.body"          (for prose), or
       "furniture.classification" / "furniture.footer" / "furniture.header".
     Do NOT target a graphic by its id/name (use "<section>.graphic").
  3b. Every graphic's `source_doc` MUST be the `name` of one of the corpus
     documents you wrote (a .txt/.md), NOT a title and NOT the image filename.
  4. Include at least one CONFLICT: two corrections on the same target with
     different new_value (e.g. a severity disagreement).
  5. Include at least one needs_review unit: a field required by the template
     for which the corpus has NO value (declare it with extract "none").
  6. Graphics: one correctly placed, one mislabeled (draft uses a wrong
     ref_name), one placed in the wrong section, one missing from the draft.
  7. Keep it realistic and self-consistent for the given domain and document type.
"""


def _coerce_spec_dict(data: dict) -> dict:
    """Normalize common, semantically-harmless shape deviations that real models
    produce, so good output is not rejected on formatting alone:
      - keys with a trailing '?' (models echo the optional-marker from the
        contract), e.g. 'requires_graphic?' -> 'requires_graphic';
      - boolean/empty 'requires_graphic'/'requires_table' -> dropped (None);
      - draft_sections[].fields as a [{key,value}] list -> a {key: value} dict.
    Anything deeper than cosmetics still falls to schema validation.
    """
    if not isinstance(data, dict):
        return data

    def strip_q(d: dict) -> dict:
        return {(k[:-1] if k.endswith("?") else k): v for k, v in d.items()}

    data = strip_q(data)

    for s in data.get("required_sections", []) or []:
        if isinstance(s, dict):
            s_ = strip_q(s)
            for key in ("requires_graphic", "requires_table"):
                v = s_.get(key)
                if isinstance(v, bool) or v == "" or v is False:
                    s_.pop(key, None)
            s.clear()
            s.update(s_)

    for ds in data.get("draft_sections", []) or []:
        if isinstance(ds, dict):
            ds_ = strip_q(ds)
            f = ds_.get("fields")
            if isinstance(f, list):  # [{key,value}] -> {key: value}
                ds_["fields"] = {
                    item.get("key"): item.get("value")
                    for item in f if isinstance(item, dict) and item.get("key")
                }
            ds.clear()
            ds.update(ds_)

    # Normalize nested '?' keys in a few list-of-dict fields.
    for listkey in ("fields", "corrections", "graphics", "cross_references"):
        for item in data.get(listkey, []) or []:
            if isinstance(item, dict):
                item2 = strip_q(item)
                item.clear()
                item.update(item2)

    return data


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model completion (tolerating a stray
    markdown fence or leading prose)."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    # Fast path.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Find the outermost {...}.
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(t[start:end + 1])
    raise ValueError("no JSON object found in model output")


def _finalize(spec: ScenarioSpec, user: str, adapter, client, max_tokens: int):
    """Turn a parsed model spec into a VALID spec, or None if unrecoverable:
      1. valid as-is -> use it;
      2. salvageable (drop bad corrections / repoint graphics) -> use the salvage;
      3. one model repair round, then salvage again;
      4. still invalid -> None (caller falls back to the deterministic generator).
    A salvaged spec keeps the model's good work and is guaranteed to honor the
    engine contract and the no-fabrication rule (bad items are removed, not kept)."""
    if not validate_spec(spec):
        return spec
    salvaged = salvage_spec(spec)
    if not validate_spec(salvaged):
        return salvaged
    problems = validate_spec(spec)
    raw2 = adapter.complete(
        client, _SPEC_CONTRACT,
        user + "\n\nYour previous output was rejected for:\n- "
        + "\n- ".join(problems) + "\nReturn corrected JSON only.",
        max_tokens=max_tokens,
    )
    spec2 = ScenarioSpec(**_coerce_spec_dict(_extract_json(raw2)))
    if not validate_spec(spec2):
        return spec2
    spec2 = salvage_spec(spec2)
    return spec2 if not validate_spec(spec2) else None


class BedrockScenarioGenerator:
    name = "bedrock"

    def __init__(self) -> None:
        import boto3  # lazy; offline installs need not have creds

        self.model_id = get_scenario_model()  # enforces allowlist (may raise)
        self._client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        self._adapter = adapter_for(self.model_id)
        self._fallback = RuleScenarioGenerator()

    def generate(self, brief: ScenarioBrief) -> ScenarioSpec:
        user = self._build_prompt(brief)
        # Reasoning models (e.g. GPT-OSS) spend tokens on a reasoning block
        # before the answer, and a full ScenarioSpec is a large JSON doc, so
        # give generous headroom.
        max_tokens = 8192
        try:
            raw = self._adapter.complete(self._client, _SPEC_CONTRACT, user, max_tokens=max_tokens)
            spec = ScenarioSpec(**_coerce_spec_dict(_extract_json(raw)))
            spec = _finalize(spec, user, self._adapter, self._client, max_tokens)
            if spec is not None:
                return spec
        except Exception:
            pass
        # Never break: fall back to the deterministic generator.
        return self._fallback.generate(brief)

    def _build_prompt(self, brief: ScenarioBrief) -> str:
        if brief.freeform:
            return (
                "Build a scenario from this description, using your best judgment "
                "to choose sections, fields, figures, a table, and realistic "
                f"defects:\n\n{brief.freeform}\n\n"
                "Return the ScenarioSpec JSON only."
            )
        return (
            f"Build a '{brief.doc_type}' scenario for the domain "
            f"'{brief.domain}'"
            + (f", titled '{brief.title}'" if brief.title else "")
            + ". Return the ScenarioSpec JSON only."
        )
