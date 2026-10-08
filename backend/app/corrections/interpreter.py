"""Freeform-feedback interpreter.

Turns prose/email feedback into the constrained correction operations defined in
schema.py. Two implementations behind one interface:

    RuleInterpreter     offline, deterministic keyword/heuristic parser (default)
    BedrockInterpreter  optional, uses Bedrock tool-use to propose the SAME ops

Neither writes final state or invents values: they only PROPOSE operations from
the fixed schema, which are then validated and applied as a correction round by
the deterministic engine. This honors the reference spec: the LLM is an
interpreter, not the parser and not an executor, and the pipeline runs fully
without it (§41, §42, §50, §90).
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from ..config import settings
from .schema import sanitize_tool_name, tool_spec, validate_op


class FeedbackInterpreter(Protocol):
    name: str

    def interpret(self, feedback: str, context: dict) -> list[dict]: ...


def _valid_targets(context: dict) -> list[str]:
    """Enumerate the exact resolvable targets from the project manifest/template
    so the interpreter proposes ops only against real units. Anything outside
    this set is rejected as off-target (never applied)."""
    targets: list[str] = []
    for f in context.get("fields", []):
        targets.append(f"{f['section']}.{f['key']}")
    for sb in context.get("section_bodies", {}):
        targets.append(f"{sb}.body")
    # Graphic targets only for sections that actually require a graphic.
    for sec in context.get("graphic_sections", context.get("sections", [])):
        targets.append(f"{sec}.graphic")
    # Table target for the section that owns the manifest table.
    if context.get("table_section"):
        targets.append(f"{context['table_section']}.table")
    targets += ["furniture.classification", "furniture.footer", "furniture.header"]
    return sorted(set(targets))


# --------------------------------------------------------------------------
# Offline rule interpreter (default; no network)
# --------------------------------------------------------------------------

class RuleInterpreter:
    name = "rule-based (offline)"

    _VERSION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")

    def interpret(self, feedback: str, context: dict) -> list[dict]:
        ops: list[dict] = []
        low = feedback.lower()
        targets = _valid_targets(context)

        # Very small heuristic set — this is a fallback, not the smart path.
        # "should be X, not Y" -> replace with X on a best-guess target.
        m = re.search(r"should be\s+(.+?)(?:,| not | instead|\.|$)", low)
        if m:
            val = m.group(1).strip()
            target = self._guess_target(low, targets)
            if target:
                ops.append({
                    "id": "rule_1", "operation": "replace_field", "target": target,
                    "new_value": val, "reason": "rule-based: 'should be' phrase",
                })
        return ops

    def _guess_target(self, low: str, targets: list[str]) -> str | None:
        for t in targets:
            leaf = t.split(".")[-1]
            if leaf.replace("_", " ") in low or leaf in low:
                return t
        return None


# --------------------------------------------------------------------------
# Bedrock interpreter (optional; tool-use)
# --------------------------------------------------------------------------

class BedrockInterpreter:
    name = "bedrock"

    def __init__(self) -> None:
        import boto3  # lazy so offline installs need not have it

        self._client = boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        self._model = settings.BEDROCK_MODEL

    def interpret(self, feedback: str, context: dict) -> list[dict]:
        targets = _valid_targets(context)
        system = (
            "You convert reviewer feedback about a report into structured "
            "correction operations. Only propose operations from the provided "
            "tool. Only use values explicitly stated in the feedback. If a value "
            "is not stated, use flag_needs_review. Never invent values. "
            "Valid targets: " + ", ".join(targets)
        )
        resp = self._client.converse(
            modelId=self._model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": feedback}]}],
            toolConfig={
                "tools": [tool_spec()],
                "toolChoice": {"tool": {"name": "propose_corrections"}},
            },
            inferenceConfig={"maxTokens": 1024, "temperature": 0},
        )
        return self._extract_ops(resp)

    @staticmethod
    def _extract_ops(resp: dict) -> list[dict]:
        ops: list[dict] = []
        for block in resp.get("output", {}).get("message", {}).get("content", []):
            tool = block.get("toolUse")
            if tool and sanitize_tool_name(tool.get("name")) == "propose_corrections":
                payload = tool.get("input", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                ops.extend(payload.get("operations", []))
        return ops


def get_interpreter() -> FeedbackInterpreter:
    """Select the interpreter by config, with graceful fallback to offline."""
    if settings.BEDROCK_ENABLED:
        try:
            return BedrockInterpreter()
        except Exception:  # boto3 missing / no creds -> fall back
            return RuleInterpreter()
    return RuleInterpreter()


def interpret_feedback(feedback: str, context: dict, round_index: int = 0,
                       author: str = "interpreter") -> dict:
    """Interpret freeform feedback into validated correction ops (a proposed
    round). Returns proposed ops + which were accepted/rejected by validation.
    Nothing is applied here; the caller decides to run it as a round."""
    interp = get_interpreter()
    raw_ops = interp.interpret(feedback, context)
    valid_targets = set(_valid_targets(context))

    accepted: list[dict] = []
    rejected: list[dict] = []
    for i, op in enumerate(raw_ops):
        op.setdefault("id", f"interp_{round_index}_{i}")
        op["round"] = round_index
        op["author"] = author
        ok, reason = validate_op(op)
        if ok and not _target_resolvable(op.get("target", ""), valid_targets):
            ok, reason = False, f"off-target: '{op.get('target')}' is not a resolvable unit"
        (accepted if ok else rejected).append({**op, "_validation": reason})

    return {
        "interpreter": interp.name,
        "valid_targets": sorted(valid_targets),
        "proposed": raw_ops,
        "accepted": accepted,
        "rejected": rejected,
    }


def _target_resolvable(target: str, valid_targets: set[str]) -> bool:
    """A target is resolvable if it is in the enumerated set exactly, or (for
    table-cell ops) its section.table prefix is a known table target."""
    if target in valid_targets:
        return True
    # Table cell targets look like "section.table[row][col]".
    base = target.split("[", 1)[0]
    return base in valid_targets
