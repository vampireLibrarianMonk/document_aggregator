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
from typing import Protocol


class ModelAdapter(Protocol):
    """Invoke a model with a system + user prompt and return the raw text."""
    model_id: str

    def complete(self, client, system: str, user: str, max_tokens: int) -> str: ...


class ConverseAdapter:
    """Uses the Bedrock Converse API (works for Nemotron and GPT-OSS text output,
    and most chat models). We ask for strict JSON in the prompt rather than
    relying on tool-use, so it is portable across approved families."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def complete(self, client, system: str, user: str, max_tokens: int) -> str:
        resp = client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )
        parts = resp.get("output", {}).get("message", {}).get("content", [])
        return "".join(p.get("text", "") for p in parts)


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


def adapter_for(model_id: str) -> ModelAdapter:
    """Pick an adapter. Converse is the default and works for the approved
    families; the invoke_model adapter is available if a deployment needs it."""
    return ConverseAdapter(model_id)
