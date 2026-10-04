"""Per-model capability profiles (size-aware generation settings).

Why this exists: the scenario contract is a large JSON document, and the output
token budget is a HARD CAP on everything the model emits -- including the
internal reasoning block that reasoning models (GPT-OSS) produce before the
answer. On the Bedrock Converse API a small reasoning model can spend its whole
budget "thinking" and then truncate the JSON, which forces a fallback. (AWS
documents this failure mode for reasoning models on Converse.) A single
hardcoded max_tokens therefore punishes exactly the models that reason the most.

The fix is to resolve the generation budget PER MODEL from a capability profile,
not to scatter `if "20b" in model_id` checks through the generator. The prompt
STRATEGY stays abstract and size-agnostic; only the capacity (token budget) and
a recommended default prompt vary by model family. Adding a new model is one
row here -- no change to the generation hot path.

Matching is by substring against the model id (most specific first), with a
conservative default so an unknown model still runs.
"""
from __future__ import annotations

from dataclasses import dataclass

# Baseline budget: enough for a full ScenarioSpec from a non-reasoning model.
# Reasoning models get more headroom because reasoning tokens are counted
# against (and bounded by) the same cap. These tiers are informed by the eval
# harness: small reasoning models (gpt-oss *20b*) routinely hit an 8192 cap and
# truncate; the 120b reasoning models are comfortable but a higher cap is free
# (maxTokens is a ceiling, not a target -- a model that finishes early is not
# billed for unused budget).
_DEFAULT_MAX_TOKENS = 8192
_REASONING_LARGE_MAX_TOKENS = 12288   # 120b-class reasoning models
_REASONING_SMALL_MAX_TOKENS = 16384   # 20b-class reasoning models (most starved)


@dataclass(frozen=True)
class ModelProfile:
    """Size-aware generation settings for one model (family-level).

    family         coarse family label for grouping/reporting
    is_reasoning   emits an internal reasoning block before the answer
    max_tokens     output budget (bounds reasoning + answer on Converse)
    default_prompt the prompt strategy to prefer for this model when the caller
                   does not pick one. Earned from the eval harness, not asserted;
                   until the data says otherwise this is a conservative default.
    note           short human explanation for reports
    """
    family: str
    is_reasoning: bool
    max_tokens: int
    default_prompt: str
    note: str = ""


# Most-specific substrings FIRST (first match wins). GPT-OSS is a reasoning
# family; Nemotron here is treated as non-reasoning for budget purposes (it did
# not exhibit the reasoning-truncation pattern in the eval).
_PROFILES: list[tuple[str, ModelProfile]] = [
    ("gpt-oss-safeguard-20b", ModelProfile(
        "gpt-oss-safeguard", True, _REASONING_SMALL_MAX_TOKENS, "reasoning_suppressed",
        "small reasoning model; needs the most output headroom")),
    ("gpt-oss-safeguard-120b", ModelProfile(
        "gpt-oss-safeguard", True, _REASONING_LARGE_MAX_TOKENS, "baseline",
        "large reasoning model; comfortable with headroom")),
    ("gpt-oss-20b", ModelProfile(
        "gpt-oss", True, _REASONING_SMALL_MAX_TOKENS, "reasoning_suppressed",
        "small reasoning model; routinely truncates at 8192 -> extra headroom")),
    ("gpt-oss-120b", ModelProfile(
        "gpt-oss", True, _REASONING_LARGE_MAX_TOKENS, "baseline",
        "large reasoning model; rarely truncates")),
    ("gpt-oss", ModelProfile(   # fallback for other gpt-oss variants
        "gpt-oss", True, _REASONING_LARGE_MAX_TOKENS, "baseline",
        "gpt-oss family default")),
    ("nemotron-super-3-120b", ModelProfile(
        "nemotron", False, _DEFAULT_MAX_TOKENS, "baseline",
        "large non-reasoning model; fits the contract at the default budget")),
    ("nemotron-nano-3-30b", ModelProfile(
        "nemotron", False, _DEFAULT_MAX_TOKENS, "baseline", "nemotron nano")),
    ("nemotron-nano-12b", ModelProfile(
        "nemotron", False, _DEFAULT_MAX_TOKENS, "baseline", "nemotron nano")),
    ("nemotron-nano-9b", ModelProfile(
        "nemotron", False, _DEFAULT_MAX_TOKENS, "baseline", "nemotron nano")),
    ("nemotron", ModelProfile(  # fallback for other nemotron variants
        "nemotron", False, _DEFAULT_MAX_TOKENS, "baseline", "nemotron family default")),
]

# Conservative default for a model we have never seen: assume it may reason, and
# give it large-reasoning headroom so it is not starved on the first contact.
_UNKNOWN = ModelProfile(
    "unknown", True, _REASONING_LARGE_MAX_TOKENS, "baseline",
    "unprofiled model; conservative reasoning-sized budget")


def profile_for(model_id: str) -> ModelProfile:
    """Resolve the capability profile for a model id (substring match)."""
    mid = (model_id or "").lower()
    for sub, prof in _PROFILES:
        if sub in mid:
            return prof
    return _UNKNOWN


# --------------------------------------------------------------------------
# Earned auto-pick (recommended model), derived from the model evaluation.
# --------------------------------------------------------------------------

# The ordered preference below is EARNED from MODEL_EVAL.md, not asserted:
# gpt-oss-120b had the lowest fallback rate (8%), was the cheapest usable model,
# and was fast. The others follow by measured viability. The reason string is
# surfaced in the UI so the pick is transparent, and the user can always
# override. Matching is by substring so it works across exact ids / profiles.
_RECOMMENDATION_ORDER: list[tuple[str, str]] = [
    ("gpt-oss-120b", "lowest fallback (8%), cheapest usable, fast (model eval)"),
    ("nemotron-super-3-120b", "richer output, less fabrication, but ~2x cost"),
    ("gpt-oss-safeguard-20b", "usable small model (29% fallback)"),
    ("nemotron-nano-3-30b", "only model with any valid-first-try in the eval"),
]


def recommend_model(available_ids: list[str]) -> dict:
    """Pick a default model from those actually AVAILABLE, with a transparent
    reason. Returns {model, reason, basis}. If none of the ranked models are
    available, returns the first available id with a generic reason, or empty if
    the list is empty (offline). The caller treats this as a DEFAULT the user can
    override, never a lock-in."""
    avail = [m for m in (available_ids or []) if m]
    for sub, reason in _RECOMMENDATION_ORDER:
        for mid in avail:
            if sub in mid.lower():
                return {"model": mid, "reason": reason,
                        "basis": "earned from the model evaluation (MODEL_EVAL.md)"}
    if avail:
        return {"model": avail[0], "reason": "first available approved model",
                "basis": "no eval ranking matched the available models"}
    return {"model": "", "reason": "offline deterministic generator",
            "basis": "no live models available"}


def max_tokens_for(model_id: str) -> int:
    return profile_for(model_id).max_tokens


def default_prompt_for(model_id: str) -> str:
    return profile_for(model_id).default_prompt
