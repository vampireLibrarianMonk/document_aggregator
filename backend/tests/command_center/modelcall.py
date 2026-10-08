"""Cached, measured Bedrock model-call helper for the command center.

Every call is keyed by (model_id, system, user, max_tokens) and cached on disk,
so re-running the bake-off does not re-spend tokens and is reproducible given a
warm cache. Usage (tokens, latency, call count) is accumulated so the efficiency
study and the matrix can report real cost.

Air-gap safe: if Bedrock/boto3/credentials are unavailable the helper reports
unavailable and callers fall back to the deterministic path.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.projectgen.model_adapters import ConverseAdapter

_CACHE_DIR = Path(__file__).parent / "_model_cache"


@dataclass
class CallStats:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "calls": self.calls, "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ModelClient:
    """A caching wrapper around one Bedrock model via the Converse adapter."""
    model_id: str
    stats: CallStats = field(default_factory=CallStats)
    _client: object = None
    _adapter: object = None
    _available: bool | None = None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import boto3
            from botocore.config import Config

            # Bound each call so a model that LISTS in the catalog but is not
            # actually invokable on-demand (or is simply slow) fails fast and the
            # sweep moves on, instead of hanging in botocore's default long
            # retry/backoff. complete() catches the error and returns None, which
            # the sweep records as "no output for this model".
            cfg = Config(
                read_timeout=120, connect_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            )
            self._client = boto3.client("bedrock-runtime",
                                        region_name=settings.BEDROCK_REGION,
                                        config=cfg)
            self._adapter = ConverseAdapter(self.model_id)
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def _key(self, system: str, user: str, max_tokens: int) -> str:
        payload = "\x00".join([self.model_id, system, user, str(max_tokens)])
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str | None:
        """Return the model's text, cached. None if the model is unavailable."""
        key = self._key(system, user, max_tokens)
        cache_file = _CACHE_DIR / f"{self.model_id.replace('/', '_').replace(':', '_')}" / f"{key}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                self.stats.calls += 1
                self.stats.cache_hits += 1
                self.stats.input_tokens += data.get("input_tokens", 0)
                self.stats.output_tokens += data.get("output_tokens", 0)
                return data.get("text", "")
            except Exception:
                pass
        if not self.available():
            return None
        t0 = time.time()
        try:
            text, usage = self._adapter.complete_with_usage(
                self._client, system, user, max_tokens)
        except Exception:
            return None
        latency = int((time.time() - t0) * 1000)
        self.stats.calls += 1
        self.stats.input_tokens += usage.input_tokens
        self.stats.output_tokens += usage.output_tokens
        self.stats.latency_ms += (usage.latency_ms or latency)
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "text": text, "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "latency_ms": usage.latency_ms or latency,
            }, indent=2), encoding="utf-8")
        except Exception:
            pass
        return text


def default_model() -> str:
    """The approved model to use for the study: the recommended one if the live
    catalog offers it, else the configured generation model, else a safe
    Nemotron id."""
    try:
        from app.projectgen.bedrock_gen import list_approved_models
        info = list_approved_models()
        if info.get("available") and info.get("models"):
            rec = (info.get("recommended") or {}).get("model")
            if rec:
                return rec
            return info["models"][0]["id"]
    except Exception:
        pass
    return settings.BEDROCK_SCENARIO_MODEL or settings.BEDROCK_MODEL


def extract_json(text: str) -> dict | list | None:
    """Pull the first JSON object/array out of a model's text answer."""
    import re
    if not text:
        return None
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None
