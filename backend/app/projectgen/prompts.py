"""Named prompt strategies for project generation.

The model is only an AUTHOR: it emits a ProjectSpec as strict JSON, which the
pipeline then validates, salvages, and persists as fixed data. A "prompt
strategy" is a (system contract, user-prompt builder) pair. Every strategy MUST
produce the SAME ProjectSpec contract -- the strategies differ only in HOW they
ask for it, so we can measure whether phrasing lifts the weaker/smaller models
without changing what counts as a valid result.

Strategies:
  baseline              The original contract. One terse spec of keys + hard rules.
  strict_schema         Baseline plus a tightened, numbered restatement of the
                        rules models most often break (targets, grounding,
                        graphic source_doc), and an explicit "JSON only" framing.
  few_shot              Baseline plus one tiny, correct, worked example of the
                        trickiest pieces (a grounded correction, a conflict, a
                        needs_review field), to anchor the format.
  reasoning_suppressed  Baseline with an instruction to keep any internal
                        reasoning minimal and emit the JSON object directly.
                        Aimed at reasoning models (GPT-OSS) that otherwise burn
                        the token budget on a long reasoning block and truncate.

Keep the baseline text identical to the historical _SPEC_CONTRACT so existing
behavior and tests are unchanged when strategy="baseline".
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .generator import ProjectBrief

# --- Baseline (verbatim historical _SPEC_CONTRACT) --------------------------

_BASELINE_CONTRACT = """You generate a document-correction PROJECT as strict JSON.

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

# --- Strict-schema addendum -------------------------------------------------
# Restates, as an imperative checklist, the rules the validator rejects most
# often. No new keys; just friction on the known failure modes.
_STRICT_ADDENDUM = """

OUTPUT DISCIPLINE (read before answering):
  - Emit the JSON object and NOTHING else: no prose, no markdown code fence, no
    trailing commentary. The first character of your reply is '{'.
  - Before you answer, silently check EACH correction:
      * Is `target` one of the exact forms in rule 3? If it names a graphic by
        id or filename, it is WRONG; use "<section>.graphic".
      * Does `new_value` appear verbatim in a corpus document OR in this
        correction's own `body`? If not, it is FABRICATION; fix or remove it.
      * Is every graphic `source_doc` equal to a corpus document `name` that
        ends in .txt or .md? A title or a .png filename is WRONG.
  - Confirm the two REQUIRED richness units exist: at least one conflict (rule 4)
    and at least one needs_review field declared with extract "none" (rule 5).
"""

# --- Few-shot addendum ------------------------------------------------------
# One compact, correct worked fragment anchoring the trickiest shapes. It is
# illustrative only; the model still authors its own domain content.
_FEW_SHOT_ADDENDUM = """

WORKED FRAGMENT (format illustration only; invent your own domain content):
  A grounded replace correction whose new_value is verbatim in the corpus:
    corpus: [{"name": "field_log.txt",
              "text": "... measured flow rate was 4.2 L/min ..."}]
    corrections: [
      {"id": "c1", "kind": "value", "author": "A. Reviewer", "subject": "flow",
       "target": "results.flow_rate", "operation": "replace",
       "old_value": "2.0 L/min", "new_value": "4.2 L/min",
       "body": "Draft used the setpoint, not the measured value."}
    ]
  A CONFLICT is two corrections on the SAME target with different new_value:
      "target": "summary.severity"  -> new_value "High"   (author X)
      "target": "summary.severity"  -> new_value "Medium" (author Y)
  A needs_review field has NO corpus value and is declared extract "none":
    fields: [{"key": "approver", "label": "Approved by",
              "section": "signoff", "extract": "none"}]
Follow the real contract above for every other key.
"""

# --- Reasoning-suppressed addendum ------------------------------------------
# For models that emit a long internal-reasoning block before the answer and
# then truncate the JSON. Ask them to keep thinking minimal and answer directly.
_REASONING_SUPPRESSED_ADDENDUM = """

ANSWER DIRECTLY. Do not write out a long chain of reasoning before the JSON.
Spend your output budget on the JSON object itself, not on planning prose. Plan
briefly if you must, but the bulk of your response MUST be the ProjectSpec JSON
object, complete and not truncated. The first character of the JSON is '{'.
"""


def _build_user(brief: ProjectBrief) -> str:
    """Shared user-message builder (identical across strategies)."""
    if brief.freeform:
        return (
            "Build a project from this description, using your best judgment "
            "to choose sections, fields, figures, a table, and realistic "
            f"defects:\n\n{brief.freeform}\n\n"
            "Return the ProjectSpec JSON only."
        )
    return (
        f"Build a '{brief.doc_type}' project for the domain "
        f"'{brief.domain}'"
        + (f", titled '{brief.title}'" if brief.title else "")
        + ". Return the ProjectSpec JSON only."
    )


@dataclass(frozen=True)
class PromptStrategy:
    """A named (system-contract, user-builder) pair.

    All strategies share the same user builder and the same validated output
    contract; they differ only in the system contract text.
    """
    name: str
    system: str
    build_user: Callable[[ProjectBrief], str]
    description: str = ""


PROMPT_STRATEGIES: dict[str, PromptStrategy] = {
    "baseline": PromptStrategy(
        name="baseline",
        system=_BASELINE_CONTRACT,
        build_user=_build_user,
        description="Original terse contract (keys + hard rules).",
    ),
    "strict_schema": PromptStrategy(
        name="strict_schema",
        system=_BASELINE_CONTRACT + _STRICT_ADDENDUM,
        build_user=_build_user,
        description="Baseline plus an imperative pre-answer checklist on the "
                    "most-broken rules and a JSON-only framing.",
    ),
    "few_shot": PromptStrategy(
        name="few_shot",
        system=_BASELINE_CONTRACT + _FEW_SHOT_ADDENDUM,
        build_user=_build_user,
        description="Baseline plus one correct worked fragment for the "
                    "trickiest shapes (grounded correction, conflict, "
                    "needs_review).",
    ),
    "reasoning_suppressed": PromptStrategy(
        name="reasoning_suppressed",
        system=_BASELINE_CONTRACT + _REASONING_SUPPRESSED_ADDENDUM,
        build_user=_build_user,
        description="Baseline plus an instruction to minimize the reasoning "
                    "block and emit JSON directly (for reasoning models that "
                    "truncate).",
    ),
}

DEFAULT_PROMPT = "baseline"


def get_prompt_strategy(name: str | None) -> PromptStrategy:
    """Resolve a strategy by name; unknown/empty -> the default (baseline).

    Kept forgiving on purpose: an unrecognized strategy should degrade to the
    known-good baseline rather than break generation.
    """
    if not name:
        return PROMPT_STRATEGIES[DEFAULT_PROMPT]
    return PROMPT_STRATEGIES.get(name, PROMPT_STRATEGIES[DEFAULT_PROMPT])


def list_prompt_strategies() -> list[str]:
    return list(PROMPT_STRATEGIES.keys())
