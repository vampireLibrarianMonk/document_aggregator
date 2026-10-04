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
from dataclasses import dataclass

from ..config import settings
from .generator import ScenarioBrief
from .metrics import RunMetrics, estimate_usd
from .model_adapters import adapter_for
from .model_profiles import profile_for
from .prompts import get_prompt_strategy
from .rule_generator import RuleScenarioGenerator
from .schema import ScenarioSpec, salvage_spec, validate_spec


class ModelNotApprovedError(RuntimeError):
    pass


@dataclass
class GenerationResult:
    """A generated spec plus the measured score+cost metrics for the run."""
    spec: ScenarioSpec
    metrics: RunMetrics


def _richness(spec: ScenarioSpec, m: RunMetrics) -> None:
    """Fill the richness flags: did the scenario demonstrate the hard cases?"""
    # Conflict = 2+ corrections on one target with differing new_value.
    by_target: dict[str, set] = {}
    for c in spec.corrections:
        by_target.setdefault(c.target, set()).add(str(c.new_value))
    m.has_conflict = any(len(v) > 1 for v in by_target.values())
    # Graphic defects = relabel ops + graphics not placed in the draft section.
    m.graphic_defects = sum(1 for c in spec.corrections if c.operation == "relabel_graphic")
    # needs_review is produced when a declared field has no corpus value; proxy:
    # a field with extract "none" (no corpus-derivable value) exists.
    m.has_needs_review = any(f.extract == "none" for f in spec.fields)


def get_scenario_model() -> str:
    """Resolve + enforce the approved scenario-generation model id."""
    model = settings.BEDROCK_SCENARIO_MODEL
    if not model:
        raise ModelNotApprovedError("BEDROCK_SCENARIO_MODEL is not set")
    if not is_model_approved(model):
        raise ModelNotApprovedError(
            f"model '{model}' is not on the scenario allowlist {_allowlist()} "
            "(only Nemotron / GPT-OSS are approved)"
        )
    return model


def _allowlist() -> list[str]:
    return [a.strip().lower() for a in settings.BEDROCK_SCENARIO_MODEL_ALLOWLIST.split(",") if a.strip()]


def is_model_approved(model_id: str) -> bool:
    """True if the model id matches an allowlisted family (substring match)."""
    mid = (model_id or "").lower()
    return bool(mid) and any(a in mid for a in _allowlist())


def list_approved_models() -> dict:
    """List the live, approved foundation models available in this account/region
    for scenario generation. Returns {available, default, allowlist, models:[...]}.
    Gracefully degrades: if Bedrock/creds are unavailable, `available` is False
    and `models` is empty (the UI then only offers the offline generator)."""
    default = settings.BEDROCK_SCENARIO_MODEL
    allow = _allowlist()
    out = {
        "available": False,
        "bedrock_enabled": settings.BEDROCK_ENABLED,
        "default": default,
        "allowlist": allow,
        "region": settings.BEDROCK_REGION,
        "models": [],
    }
    try:
        import boto3

        bd = boto3.client("bedrock", region_name=settings.BEDROCK_REGION)
        summaries = bd.list_foundation_models().get("modelSummaries", [])
        models = []
        for m in summaries:
            mid = m.get("modelId", "")
            if not is_model_approved(mid + m.get("modelName", "")):
                continue
            if "ON_DEMAND" not in (m.get("inferenceTypesSupported") or []):
                continue
            family = "nemotron" if "nemotron" in mid.lower() else (
                "gpt-oss" if "gpt-oss" in mid.lower() else "other")
            models.append({
                "id": mid,
                "name": m.get("modelName", mid),
                "family": family,
                "is_default": mid == default,
            })
        models.sort(key=lambda x: (x["family"], x["id"]))
        out["available"] = True
        out["models"] = models
        # Earned auto-pick: recommend a default from what is actually available,
        # with a transparent reason the UI can show. User-overridable.
        from .model_profiles import recommend_model
        rec = recommend_model([m["id"] for m in models])
        out["recommended"] = rec
        for m in models:
            m["recommended"] = (m["id"] == rec.get("model"))
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


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


def _finalize(spec: ScenarioSpec, user: str, adapter, client, max_tokens: int,
              m: RunMetrics, system: str):
    """Turn a parsed model spec into a VALID spec, or None if unrecoverable,
    recording score metrics (valid_first_try, repair_rounds, salvage_dropped,
    fabrication_rejections) along the way:
      1. valid as-is -> use it (valid_first_try);
      2. salvageable (drop bad corrections / repoint graphics) -> use the salvage;
      3. one model repair round, then salvage again;
      4. still invalid -> None (caller falls back to the deterministic generator)."""
    if not validate_spec(spec):
        m.valid_first_try = True
        return spec
    salvaged = salvage_spec(spec)
    if not validate_spec(salvaged):
        m.salvage_dropped += getattr(salvaged, "_salvage_dropped", 0)
        m.fabrication_rejections += getattr(salvaged, "_salvage_fabrication", 0)
        return salvaged
    # Model repair round.
    m.repair_rounds += 1
    problems = validate_spec(spec)
    raw2, usage2 = adapter.complete_with_usage(
        client, system,
        user + "\n\nYour previous output was rejected for:\n- "
        + "\n- ".join(problems) + "\nReturn corrected JSON only.",
        max_tokens=max_tokens,
    )
    m.input_tokens += usage2.input_tokens
    m.output_tokens += usage2.output_tokens
    m.latency_ms += usage2.latency_ms
    spec2 = ScenarioSpec(**_coerce_spec_dict(_extract_json(raw2)))
    if not validate_spec(spec2):
        return spec2
    spec2 = salvage_spec(spec2)
    m.salvage_dropped += getattr(spec2, "_salvage_dropped", 0)
    m.fabrication_rejections += getattr(spec2, "_salvage_fabrication", 0)
    return spec2 if not validate_spec(spec2) else None


class BedrockScenarioGenerator:
    name = "bedrock"

    def __init__(self, model_id: str | None = None,
                 prompt_strategy: str | None = None) -> None:
        import boto3  # lazy; offline installs need not have creds

        if model_id:
            if not is_model_approved(model_id):
                raise ModelNotApprovedError(
                    f"model '{model_id}' is not on the scenario allowlist {_allowlist()}")
            self.model_id = model_id
        else:
            self.model_id = get_scenario_model()  # enforces allowlist (may raise)
        # Size-aware capability profile: token budget + recommended default
        # prompt resolved from the model family, so no model is starved and the
        # strategy stays abstract. An explicit prompt_strategy always wins; only
        # when the caller does not pick one do we use the model's default.
        self._profile = profile_for(self.model_id)
        self._prompt = get_prompt_strategy(prompt_strategy or self._profile.default_prompt)
        self._client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        self._adapter = adapter_for(self.model_id)
        self._fallback = RuleScenarioGenerator()

    def generate(self, brief: ScenarioBrief) -> ScenarioSpec:
        return self.generate_with_metrics(brief).spec

    def generate_with_metrics(self, brief: ScenarioBrief) -> GenerationResult:
        user = self._build_prompt(brief)
        # Output budget is resolved PER MODEL from its capability profile. The
        # cap bounds reasoning + answer on Converse, so reasoning models get
        # more headroom to avoid truncating the JSON (which would force a
        # fallback). A model that finishes early is not billed for the ceiling.
        max_tokens = self._profile.max_tokens
        m = RunMetrics(model=self.model_id, prompt=self._prompt.name)
        try:
            raw, usage = self._adapter.complete_with_usage(
                self._client, self._prompt.system, user, max_tokens=max_tokens)
            m.input_tokens += usage.input_tokens
            m.output_tokens += usage.output_tokens
            m.latency_ms += usage.latency_ms
            spec = ScenarioSpec(**_coerce_spec_dict(_extract_json(raw)))
            spec = _finalize(spec, user, self._adapter, self._client, max_tokens, m,
                             self._prompt.system)
            if spec is not None:
                _richness(spec, m)
                m.est_usd = estimate_usd(self.model_id, m.input_tokens, m.output_tokens)
                return GenerationResult(spec=spec, metrics=m)
        except Exception:
            pass
        # Never break: fall back to the deterministic generator.
        m.fell_back = True
        m.est_usd = estimate_usd(self.model_id, m.input_tokens, m.output_tokens)
        fallback_spec = self._fallback.generate(brief)
        _richness(fallback_spec, m)
        return GenerationResult(spec=fallback_spec, metrics=m)

    def _build_prompt(self, brief: ScenarioBrief) -> str:
        # The user message is identical across strategies; the strategy's own
        # builder is the single source of truth.
        return self._prompt.build_user(brief)
