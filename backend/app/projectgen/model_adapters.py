"""Bedrock model adapters.

Different model families on Bedrock use different invocation conventions.
Rather than assume Converse tool-use is universally supported (it is not for all
open-weight models), each adapter knows how to (a) build its request and (b)
pull the raw text completion out of the response. The generator then parses
strict JSON from that text. This keeps model support to a small, testable seam.

Approved families (per the project's model policy): NVIDIA Nemotron and
OpenAI GPT-OSS. The allowlist is enforced in bedrock_gen.get_scenario_model().
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Usage:
    """Measured cost signals from a single model call."""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class ModelAdapter(Protocol):
    """Invoke a model with a system + user prompt. `complete` returns just the
    text; `complete_with_usage` also returns measured token/latency cost."""
    model_id: str

    def complete(self, client, system: str, user: str, max_tokens: int) -> str: ...

    def complete_with_usage(
        self, client, system: str, user: str, max_tokens: int
    ) -> tuple[str, Usage]: ...


class ConverseAdapter:
    """Uses the Bedrock Converse API (works for Nemotron and GPT-OSS text output,
    and most chat models). We ask for strict JSON in the prompt rather than
    relying on tool-use, so it is portable across approved families."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def complete(self, client, system: str, user: str, max_tokens: int) -> str:
        text, _ = self.complete_with_usage(client, system, user, max_tokens)
        return text

    def complete_with_usage(
        self, client, system: str, user: str, max_tokens: int
    ) -> tuple[str, Usage]:
        resp = client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        # GPT-OSS emits a reasoningContent block before the text block; take only
        # the text parts (skipping reasoning) so we get the actual answer.
        parts = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(p.get("text", "") for p in parts)
        usage = resp.get("usage", {}) or {}
        metrics = resp.get("metrics", {}) or {}
        return text, Usage(
            input_tokens=int(usage.get("inputTokens", 0) or 0),
            output_tokens=int(usage.get("outputTokens", 0) or 0),
            latency_ms=int(metrics.get("latencyMs", 0) or 0),
        )


class InvokeModelAdapter:
    """Fallback for models exposed via invoke_model with an OpenAI-style
    messages payload (some GPT-OSS deployments). Kept minimal; the generator
    will try Converse first and this on failure."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def complete(self, client, system: str, user: str, max_tokens: int) -> str:
        body = json.dumps({
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        })
        resp = client.invoke_model(modelId=self.model_id, body=body)
        payload = json.loads(resp["body"].read())
        # Try a few common response shapes.
        for path in (
            lambda d: d["choices"][0]["message"]["content"],
            lambda d: d["choices"][0]["text"],
            lambda d: d["output"]["text"],
            lambda d: d["generation"],
        ):
            try:
                return path(payload)
            except (KeyError, IndexError, TypeError):
                continue
        return json.dumps(payload)  # last resort: hand back raw for debugging

    def complete_with_usage(
        self, client, system: str, user: str, max_tokens: int
    ) -> tuple[str, Usage]:
        # invoke_model usage shapes vary by model; return text with zero usage.
        return self.complete(client, system, user, max_tokens), Usage()


def adapter_for(model_id: str) -> ModelAdapter:
    """Pick an adapter. Converse is the default and works for the approved
    families; the invoke_model adapter is available if a deployment needs it."""
    return ConverseAdapter(model_id)
