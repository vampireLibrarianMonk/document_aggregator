"""Deterministic generator of structured incident-report projects at a chosen
size, each with KNOWN localized edits and a KNOWN gold.

A generated project mirrors the bundled sample shape exactly, so the existing
engine (app.reconcile.reconcile / app.project.run_reconciliation) and the
bake-off harness consume it unchanged:

  project.json                              manifest: fields, section_bodies, table
  corpus/<doc>.txt + corpus/graphics.json   grounded truth values + figures
  template/incident_report_template.json    required_sections, table_specs, furniture
  first_attempt/incident_report_draft.json  the draft (seeded with wrong values)
  corrections/comments.json                 localized corrections -> truth

Everything is produced from a seed, so values are known and the gold corrected
report is fully determined. The generator also returns an EDIT LEDGER: for each
seeded defect, the exact target unit, the wrong draft value, and the correct
value — the ground truth the alpha loop scores precision/recall against.

Scaling knob: GrowthSpec.size multiplies the number of body sections (each with
its own prose paragraph + discrete field) and figures, so a run can sweep from
a handful of units to many dozens and watch accuracy / unintended-change move.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

# Deterministic vocabularies — no model, no network. Values are distinctive so
# retrieval + grounding have something unambiguous to find.
_SITES = ["North Ridge", "West Vale", "East Harbor", "South Mesa", "Clearwater",
          "Granite Pass", "Blue Delta", "Iron Summit", "Cedar Flats", "Red Canyon"]
_COMPONENTS = ["TGX-9 gateway", "LRX-4 relay", "VPU-7 controller", "SBX-2 bridge",
               "MCU-5 node", "DAX-8 collector", "PNU-3 router", "KLX-6 switch"]
_TOPICS = ["packet loss", "login volume", "pressure decay", "thermal drift",
           "signal jitter", "flow imbalance", "voltage sag", "latency spike",
           "buffer overrun", "clock skew", "cache miss", "queue backlog"]
_SEVERITIES = ["Low", "Medium", "High"]


@dataclass
class GrowthSpec:
    """One point on the growth curve."""
    project_id: str
    size: int = 1          # body sections / fields scale with this (>=1)
    seed: int = 0

    @property
    def n_body(self) -> int:
        return 2 + self.size            # prose body sections
    @property
    def n_fields(self) -> int:
        return 3 + self.size            # discrete fields across the doc
    @property
    def n_figures(self) -> int:
        return 1 + self.size // 2       # figures (each in its own section)
    @property
    def n_table_rows(self) -> int:
        return 2 + self.size            # rows in the growing table
    @property
    def n_table_cols(self) -> int:
        return 3 + self.size // 2       # columns (incl. one uncovered column)


@dataclass
class Edit:
    """One seeded localized edit (ground truth for scoring)."""
    kind: str                   # value | fluency | relabel_graphic | conflict
    target: str                 # section.field | section.body | section.graphic
    draft_value: str            # what the draft wrongly says
    correct_value: str | None   # the grounded truth (None for a genuine conflict)
    intent: str                 # IteraTeR-style label


@dataclass
class GeneratedProject:
    project_id: str
    size: int
    manifest: dict
    corpus: dict[str, str]
    graphics: list[dict]
    template: dict
    draft: dict
    corrections: list[dict]
    edits: list[Edit] = field(default_factory=list)   # the ground-truth ledger


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def generate_project(spec: GrowthSpec) -> GeneratedProject:
    rng = random.Random(spec.seed if spec.seed else int(spec.project_id or "0") + 1000)
    site = rng.choice(_SITES)
    component = rng.choice(_COMPONENTS)
    topics = rng.sample(_TOPICS, min(spec.n_body, len(_TOPICS)))
    incident_date = f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    firmware_true = f"{rng.randint(2, 6)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}"
    duration_true = rng.choice(["two", "three", "four", "five", "six"]) + " hour"

    # ---- body sections (prose) + their discrete field ----
    body_sections = []
    for i, topic in enumerate(topics):
        key = f"obs_{i}_{_slug(topic)}"
        body_sections.append({"key": key, "topic": topic,
                              "metric": rng.randint(10, 95)})

    # ---- figures (one per figure-section) ----
    figures = []
    for i in range(spec.n_figures):
        topic = topics[i % len(topics)]
        name = f"{_slug(topic)}_fig{i}.png"
        figures.append({
            "graphic_id": f"gfx_{i}_{_slug(topic)}",
            "name": name,
            "title": f"{topic.title()} Trend {i}",
            "caption": f"{topic} over the event window {i}",
            "source_doc": "field_report.txt",
            "belongs_in_section": body_sections[i % len(body_sections)]["key"],
            "file": f"figures/{name}", "width": 640, "height": 360, "align": "center",
        })

    # ---- a growing TABLE: columns scale with size; the LAST column is left
    # uncovered by the corpus on purpose, so per-cell [needs_review] (the
    # no-fabrication floor) is exercised at scale, exactly like the real
    # projects' "Verification" column. ----
    table_section_key = "corrective_actions"
    base_cols = ["Action", "Owner", "Due Date"]
    extra_cols = [f"Metric {i}" for i in range(max(0, spec.n_table_cols - len(base_cols) - 1))]
    uncovered_col = "Verification"            # declared by template, never in corpus
    table_columns = base_cols + extra_cols + [uncovered_col]
    covered_cols = base_cols + extra_cols      # everything the corpus supplies
    # The corpus rows (grounded truth for the covered columns).
    owners = ["Reliability Engineering", "Site Operations", "Network Team",
              "Firmware Group", "Facilities", "QA Lab", "Carrier Liaison"]
    table_rows_truth = []
    for r in range(spec.n_table_rows):
        row = {
            "Action": f"Remediate the {_slug(topics[r % len(topics)]).replace('_', ' ')} "
                      f"finding step {r}",
            "Owner": owners[r % len(owners)],
            "Due Date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        }
        for ec in extra_cols:
            row[ec] = str(rng.randint(10, 99))
        table_rows_truth.append(row)

    # ================= CORPUS (the grounded truth) =================
    corpus_lines = [
        f"{component.upper()} FIELD INCIDENT REPORT",
        f"Date: {incident_date}",
        f"Site: {site} Relay Station",
        "Author: Field Engineering",
        "",
        "Summary",
        f"On {incident_date} the {component} at {site} experienced a "
        f"{duration_true} degradation event. Firmware version {firmware_true} "
        "was running on the affected unit.",
        "",
        "Observations",
    ]
    for bs in body_sections:
        corpus_lines.append(
            f"- {bs['topic'].title()} reached {bs['metric']} percent during the "
            f"{bs['topic']} event in the {bs['topic']} subsystem.")
    # Corpus rows for the table, each led by the row_marker so the retrieval
    # extractor can segment them. Only the COVERED columns are present.
    corpus_lines += ["", "Corrective Actions"]
    for row in table_rows_truth:
        parts = [f"Action: {row['Action']}"]
        parts += [f"{c}: {row[c]}" for c in covered_cols if c != "Action"]
        corpus_lines.append(". ".join(parts) + ".")
    corpus = {"field_report.txt": "\n".join(corpus_lines)}

    # ================= MANIFEST (project.json) =================
    fields = [
        {"key": "site", "label": "Site", "section": "identifiers",
         "extract": "line", "hint": "Site:"},
        {"key": "incident_date", "label": "Incident Date", "section": "identifiers",
         "extract": "date", "hint": "Date:"},
        {"key": "severity", "label": "Severity", "section": "identifiers",
         "extract": "none"},
        {"key": "firmware", "label": "Firmware Version", "section": body_sections[0]["key"],
         "extract": "version", "hint": "firmware version"},
    ]
    # extra discrete fields to reach n_fields: one metric field per body section
    for bs in body_sections[: max(0, spec.n_fields - len(fields))]:
        fields.append({"key": f"{bs['key']}_metric", "label": f"{bs['topic'].title()} Metric",
                       "section": bs["key"], "extract": "none",
                       "hint": bs["topic"]})

    section_bodies = {bs["key"]: {"query": f"{bs['topic']} observations event window",
                                  "block_anchor": "Observations"}
                      for bs in body_sections}

    # Table manifest: cell_queries + column_source for the COVERED columns only;
    # the uncovered column intentionally has no source so it lands needs_review.
    cell_queries = {c: f"{c.lower()} for the corrective action" for c in covered_cols}
    column_source = {c: c for c in covered_cols}  # template col -> corpus label
    table_manifest = {
        "key": "corrective_actions_table",
        "section": table_section_key,
        "query": "corrective action remediation owner due date",
        "row_marker": "Action:",
        "cell_queries": cell_queries,
        "column_source": column_source,
    }

    manifest = {
        "id": spec.project_id,
        "title": f"{component} {site} Incident (size {spec.size})",
        "domain": "synthetic growth corpus",
        "corpus_docs": ["field_report.txt"],
        "fields": fields,
        "section_bodies": section_bodies,
        "table": table_manifest,
    }

    # ================= TEMPLATE =================
    required_sections = [
        {"key": "identifiers", "heading": "1. Identifiers",
         "fields": ["site", "incident_date", "severity"]},
    ]
    for n, bs in enumerate(body_sections):
        sec = {"key": bs["key"], "heading": f"{n + 2}. {bs['topic'].title()}"}
        fig = next((g for g in figures if g["belongs_in_section"] == bs["key"]), None)
        if fig:
            sec["requires_graphic"] = fig["graphic_id"]
        required_sections.append(sec)
    # The table section, last.
    required_sections.append({
        "key": table_section_key,
        "heading": f"{len(body_sections) + 2}. Corrective Actions",
        "requires_table": "corrective_actions_table"})

    template = {
        "artifact_kind": "template",
        "title": "Standard Incident Report Template",
        "build_discipline": {"profile": "gov_standard"},
        "required_sections": required_sections,
        "table_specs": {
            "corrective_actions_table": {
                "title_required": True,
                "title": "Table {n}: Corrective Actions",
                "columns": table_columns,
                "font": "Arial",
                "header_style": "table-header",
            }
        },
        "furniture": {
            "header": {"required": True, "must_contain": ["report_title"]},
            "footer": {"required": True, "must_contain": ["page_number", "classification"]},
            "figures": {"caption_required": True, "numbering": "sequential",
                        "caption_format": "Figure {n}: {caption}"},
            "tables": {"title_required": True, "title_position": "above",
                       "numbering": "sequential"},
            "cross_references": {"must_resolve": True},
        },
    }

    # ================= DRAFT (seeded with wrong values) + EDIT LEDGER =======
    edits: list[Edit] = []
    draft_sections = [{
        "key": "identifiers", "heading": "1. Identifiers",
        "fields": {"site": f"{site} Relay Station", "incident_date": incident_date,
                   "severity": "Low"},
    }]
    # Seed a firmware value defect on the first body section's firmware field.
    firmware_draft = f"{firmware_true.rsplit('.', 1)[0]}.{(int(firmware_true.rsplit('.', 1)[1]) + 9) % 10}"
    def _para(topic: str, mid: str) -> str:
        # A three-sentence paragraph. Only the MIDDLE sentence is ever the edit
        # target, so a precision technique must change that one sentence and
        # leave the surrounding two intact (whole-unit replace cannot).
        return (f"The {topic} subsystem was monitored throughout the event. "
                f"{mid} "
                f"Operators logged the {topic} readings for the post-incident review.")

    for n, bs in enumerate(body_sections):
        mid = f"The {bs['topic']} subsystem showed anomalies during the window."
        sec = {"key": bs["key"], "heading": f"{n + 2}. {bs['topic'].title()}",
               "body": _para(bs["topic"], mid), "graphics": []}
        # The firmware is a DISCRETE field on the first body section (so it is a
        # flattenable unit the precision-edit techniques can target directly).
        if n == 0:
            sec["fields"] = {"firmware": firmware_draft}
        fig = next((g for g in figures if g["belongs_in_section"] == bs["key"]), None)
        if fig:
            # Seed a figure RELABEL defect: draft uses a placeholder name.
            placeholder = f"figure{n}.png"
            sec["graphics"] = [{"ref_name": placeholder, "caption": "",
                                "title": "", "title_centered": False, "width": 320}]
            edits.append(Edit("relabel_graphic", f"{bs['key']}.graphic",
                              placeholder, fig["name"], "value"))
        draft_sections.append(sec)

    # The draft TABLE: empty rows (engine fills from corpus -> status 'filled'),
    # with a scrambled column order + wrong font to seed formatting defects.
    scrambled_cols = ([table_columns[1], table_columns[0]] + table_columns[2:-1]
                      if len(table_columns) >= 2 else list(table_columns))
    draft_sections.append({
        "key": table_section_key,
        "heading": f"{len(body_sections) + 2}. Corrective Actions",
        "body": "",
        "table": {"title": "", "font": "Comic Sans MS", "header_style": "body-text",
                  "columns": scrambled_cols, "rows": []},
    })
    # table edit: fill rows from corpus + fix columns/font/header + the uncovered
    # column lands needs_review per cell (recorded so scoring expects it).
    edits.append(Edit("table_fill", f"{table_section_key}.table",
                      "empty / scrambled columns / Comic Sans",
                      f"{len(table_rows_truth)} rows, cols={table_columns}, "
                      f"uncovered={uncovered_col}", "value"))

    # firmware value edit (field + reflected in body[0])
    edits.append(Edit("value", f"{body_sections[0]['key']}.firmware",
                      firmware_draft, firmware_true, "value"))
    # a prose-body fluency edit on body section 1 (if present). The correct
    # result changes ONLY the middle sentence; the surrounding two sentences
    # must survive verbatim. A whole-unit replace that emits only the corrected
    # sentence will fail to match this gold; a span/diff/tagger will match.
    fluency_new_sentence = None
    if len(body_sections) > 1:
        btopic = body_sections[1]["topic"]
        bkey = body_sections[1]["key"]
        fluency_new_sentence = (f"The {btopic} subsystem showed clear anomalies "
                                "throughout the event window.")
        correct_body = _para(btopic, fluency_new_sentence)
        edits.append(Edit("fluency", f"{bkey}.body",
                          draft_sections[2]["body"], correct_body, "clarity"))
    # a genuine severity CONFLICT (two reviewers disagree) -> correct_value None
    edits.append(Edit("conflict", "identifiers.severity", "Low", None, "meaning-changed"))

    draft = {
        "artifact_kind": "draft",
        "title": manifest["title"],
        "sections": draft_sections,
        "furniture": {"header": {"text": manifest["title"]}, "footer": {"text": ""},
                      "page_numbers": False, "classification": ""},
        "cross_references": [],
    }

    # ================= CORRECTIONS (localized -> truth) =================
    corrections: list[dict] = []
    cid = 0
    for e in edits:
        cid += 1
        if e.kind == "relabel_graphic":
            # Reference the figure by its DESCRIPTION (as a real reviewer would),
            # not its filename or the raw section slug — this forces the
            # grounding step to resolve prose -> exact corpus filename.
            fig = next((g for g in figures
                        if f"{g['belongs_in_section']}.graphic" == e.target), None)
            desc = (fig["caption"] if fig else
                    e.correct_value.replace(".png", "").replace("_", " "))
            sec_topic = e.target.split(".")[0].split("_", 2)[-1].replace("_", " ")
            corrections.append({
                "id": f"c{cid}", "kind": "email", "author": "reviewer",
                "subject": "wrong figure",
                "target": e.target, "operation": "relabel_graphic",
                "old_value": e.draft_value, "new_value": e.correct_value,
                "body": f"The chart in the {sec_topic} section is wrong. "
                        f"It should be the {desc} figure."})
        elif e.kind == "value":
            corrections.append({
                "id": f"c{cid}", "kind": "email", "author": "reviewer",
                "subject": "value fix", "target": e.target, "operation": "replace",
                "old_value": e.draft_value, "new_value": e.correct_value,
                "body": f"The firmware version is wrong; it is {e.correct_value}."})
        elif e.kind == "fluency":
            # The reviewer states the corrected SENTENCE, not the whole
            # paragraph. A precision technique must splice this one sentence in
            # and keep the surrounding sentences; a naive whole-unit replace
            # that writes only this sentence loses the rest (lower match).
            corrections.append({
                "id": f"c{cid}", "kind": "comment", "author": "reviewer",
                "subject": "clarity", "target": e.target, "operation": "replace",
                "old_value": e.draft_value,
                "new_value": fluency_new_sentence or e.correct_value,
                "body": "Please revise the anomaly sentence to: "
                        f"{fluency_new_sentence or ''}"})
        elif e.kind == "conflict":
            # Two emails disagree -> engine must preserve as conflict.
            corrections.append({
                "id": f"c{cid}a", "kind": "email", "author": "director",
                "subject": "severity", "target": e.target, "operation": "replace",
                "old_value": "Low", "new_value": "High",
                "body": "This should be High severity."})
            corrections.append({
                "id": f"c{cid}b", "kind": "comment", "author": "qa",
                "subject": "severity", "target": e.target, "operation": "replace",
                "old_value": "Low", "new_value": "Medium",
                "body": "Per the matrix this is Medium at most."})

    return GeneratedProject(
        project_id=spec.project_id, size=spec.size, manifest=manifest,
        corpus=corpus, graphics=figures, template=template, draft=draft,
        corrections=corrections, edits=edits)


def write_project(gp: GeneratedProject, data_root: Path) -> Path:
    """Write a generated project into DATA_DIR/projects/<id>/data/ so the engine
    + harness resolve it exactly like a real project. Returns that dir."""
    base = data_root / "projects" / gp.project_id / "data"
    (base / "corpus").mkdir(parents=True, exist_ok=True)
    (base / "template").mkdir(parents=True, exist_ok=True)
    (base / "first_attempt").mkdir(parents=True, exist_ok=True)
    (base / "corrections" / "emails").mkdir(parents=True, exist_ok=True)

    _dump(base / "project.json", gp.manifest)
    for name, text in gp.corpus.items():
        (base / "corpus" / name).write_text(text, encoding="utf-8")
    _dump(base / "corpus" / "graphics.json", {"graphics": gp.graphics})
    _dump(base / "template" / "incident_report_template.json", gp.template)
    _dump(base / "first_attempt" / "incident_report_draft.json", gp.draft)
    _dump(base / "corrections" / "comments.json", {"corrections": gp.corrections})
    # Also emit the raw reviewer emails (what a user actually uploads); the
    # command-center email-parsing path reads these, exactly like the samples.
    for i, c in enumerate(gp.corrections, start=1):
        txt = (f"From: {c.get('author', 'reviewer')}\n"
               f"Subject: {c.get('subject', '')}\n\n{c.get('body', '')}\n")
        (base / "corrections" / "emails" / f"email_{i}.txt").write_text(
            txt, encoding="utf-8")
    # The ground-truth edit ledger (for scoring; not consumed by the engine).
    _dump(base / "edit_ledger.json",
          {"project_id": gp.project_id, "size": gp.size,
           "edits": [vars(e) for e in gp.edits]})
    return base


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
