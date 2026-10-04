"""Offline, deterministic project generator.

Given a brief (domain + doc type + optional title), it assembles a complete,
corpus-grounded ProjectSpec in the proven incident-report shape: six sections,
discrete fields, three figures (relabel / move / insert defects), one table, a
severity conflict, a classification needs_review, and furniture defects. The
corpus text it writes CONTAINS every corrected value, so the no-fabrication
validator passes and the generated project is as traceable as a hand-authored
one.

This is the alpha-loop target and the air-gap fallback. It is fully
deterministic given the same brief, so a generated project is reproducible.
"""
from __future__ import annotations

from .generator import ProjectBrief
from .schema import (
    CorpusDoc,
    CorrectionSpec,
    CrossRef,
    DraftSection,
    FieldSpec,
    GraphicSpec,
    ProjectSpec,
    SectionBodySpec,
    SectionSpec,
    TableColumnSpec,
    TableSpec,
)


class RuleProjectGenerator:
    name = "rule-based (offline)"

    def generate(self, brief: ProjectBrief) -> ProjectSpec:
        domain = brief.domain or "systems reliability"
        subject = brief.title or f"{domain.title()} Event"
        slug = _slug(subject)

        # Correct values (ground truth) and the WRONG draft values the engine
        # must correct. Everything correct appears in the corpus text below.
        correct = {
            "site": "West Campus Facility",
            "event_date": "2026-10-02",
            "firmware": "5.1.3",
            "duration": "three hour",
        }
        wrong = {
            "firmware": "5.1.1",
            "duration": "one hour",
            "severity": "Low",
        }

        field_doc = f"field_report_{correct['event_date']}.txt"
        rca_doc = "root_cause_notes.md"

        corpus = [
            CorpusDoc(name=field_doc, text=_field_report(subject, correct)),
            CorpusDoc(name=rca_doc, text=_rca_notes(correct)),
        ]

        graphics = [
            GraphicSpec(graphic_id="gfx_overview", name=f"{slug}_overview.png",
                        caption=f"{subject} overview", source_doc=field_doc,
                        belongs_in_section="description"),
            GraphicSpec(graphic_id="gfx_trend", name=f"{slug}_trend.png",
                        caption="Measured trend over the event window", source_doc=field_doc,
                        belongs_in_section="timeline"),
            GraphicSpec(graphic_id="gfx_cause", name=f"{slug}_cause_diagram.png",
                        caption="Root-cause diagram", source_doc=rca_doc,
                        belongs_in_section="contributing_factors"),
        ]

        required_sections = [
            SectionSpec(key="identifiers", heading="1. Identifiers"),
            SectionSpec(key="description", heading="2. Description", requires_graphic="gfx_overview"),
            SectionSpec(key="timeline", heading="3. Timeline", requires_graphic="gfx_trend"),
            SectionSpec(key="contributing_factors", heading="4. Contributing Factors",
                        requires_graphic="gfx_cause"),
            SectionSpec(key="corrective_actions", heading="5. Corrective Actions",
                        requires_table="corrective_actions_table"),
            SectionSpec(key="approvals", heading="6. Approvals"),
        ]

        fields = [
            FieldSpec(key="site", label="Site", section="identifiers", extract="line", hint="Site:"),
            FieldSpec(key="event_date", label="Event Date", section="identifiers",
                      extract="date", hint="Date:"),
            FieldSpec(key="severity", label="Severity", section="identifiers", extract="none"),
            FieldSpec(key="firmware", label="Firmware Version", section="contributing_factors",
                      extract="version", hint="was running"),
            FieldSpec(key="duration", label="Event Duration", section="description",
                      extract="duration", hint="window"),
        ]

        section_bodies = [
            SectionBodySpec(section="description",
                            query="event summary what happened measured deviation",
                            block_anchor="Summary"),
            SectionBodySpec(section="timeline",
                            query="observations alarm fired threshold reached",
                            block_anchor="Observations"),
            SectionBodySpec(section="contributing_factors",
                            query="preliminary assessment suspected contributing factor",
                            block_anchor="Assessment"),
        ]

        table = TableSpec(
            key="corrective_actions_table", section="corrective_actions",
            title="Table {n}: Corrective Actions",
            query="corrective action assignments owner due date",
            row_marker="Action:",
            columns=[
                TableColumnSpec(name="Action", corpus_label="Action",
                                cell_query="corrective action to take"),
                TableColumnSpec(name="Owner", corpus_label="Owner",
                                cell_query="who owns the action"),
                TableColumnSpec(name="Due Date", corpus_label="Due",
                                cell_query="due date for the action"),
                TableColumnSpec(name="Verification"),  # no corpus source -> needs_review cells
            ],
        )

        # The flawed draft: wrong values, a mislabeled/misplaced/missing figure,
        # a bad table, empty footer.
        draft_sections = [
            DraftSection(key="identifiers", heading="1. Identifiers", fields={
                "site": correct["site"], "event_date": correct["event_date"],
                "severity": wrong["severity"],
            }),
            DraftSection(key="description", heading="2. Description",
                         body=(f"At {correct['site']} the system showed a measured deviation over a "
                               f"{wrong['duration']} window. See Figure 2 for the overview."),
                         graphics=[{"ref_name": f"{slug}_cause_diagram.png",
                                    "caption": "Root-cause diagram", "title": "Root-cause diagram",
                                    "title_centered": True, "width": 640}]),
            DraftSection(key="timeline", heading="3. Timeline",
                         body="Threshold alarm fired, then the deviation began; recovery followed intervention.",
                         graphics=[{"ref_name": "figure1.png", "caption": "", "title": "",
                                    "title_centered": False, "width": 320}]),
            DraftSection(key="contributing_factors", heading="4. Contributing Factors",
                         body=f"Firmware {wrong['firmware']} was running on the affected unit."),
            DraftSection(key="corrective_actions", heading="5. Corrective Actions", body="",
                         table={"title": "", "font": "Comic Sans MS", "header_style": "body-text",
                                "columns": ["Owner", "Action", "Due Date"], "rows": []}),
            DraftSection(key="approvals", heading="6. Approvals",
                         fields={"reviewer": "", "signoff_date": ""}),
        ]

        cross_references = [
            CrossRef(id="xref_1", in_section="description", text="Figure 2",
                     points_to_graphic="gfx_overview"),
        ]

        corrections = [
            CorrectionSpec(id="corr_firmware", kind="email", author="reliability.lead@example.com",
                           subject="firmware version is wrong",
                           target="contributing_factors.firmware", operation="replace",
                           old_value=wrong["firmware"], new_value=correct["firmware"],
                           body=f"The affected version is firmware {correct['firmware']}, "
                                f"not {wrong['firmware']}."),
            CorrectionSpec(id="corr_duration", kind="comment", author="qa_reviewer",
                           subject="duration wrong and section blank",
                           target="description.duration", operation="replace",
                           old_value=wrong["duration"], new_value=correct["duration"],
                           body=f"The duration is wrong; it was a {correct['duration']} window. "
                                "Also the Corrective Actions section is blank."),
            CorrectionSpec(id="corr_sev_high", kind="email", author="regional.director@example.com",
                           subject="unacceptable", target="identifiers.severity", operation="replace",
                           old_value=wrong["severity"], new_value="High",
                           body="Severity 'Low' is unacceptable for this event. This should be High."),
            CorrectionSpec(id="corr_sev_med", kind="comment", author="qa_reviewer",
                           subject="severity assessment", target="identifiers.severity",
                           operation="replace", old_value=wrong["severity"], new_value="Medium",
                           body="Per the severity matrix a recoverable event is Medium at most, not High."),
            CorrectionSpec(id="corr_figure", kind="comment", author="qa_reviewer",
                           subject="wrong figure", target="timeline.graphic",
                           operation="relabel_graphic", old_value="figure1.png",
                           new_value=f"{slug}_trend.png",
                           body="The Timeline chart is the wrong image reference; it should be the trend figure."),
        ]

        seeded_defects = [
            f"1. Value: firmware is {wrong['firmware']} but should be {correct['firmware']}.",
            f"2. Value: duration says '{wrong['duration']}' but should be '{correct['duration']}'.",
            "3. Missing content: the Corrective Actions text and table rows are empty.",
            "4. Value: severity 'Low' is challenged and the correct value is unknown (needs review or conflict).",
            f"5. Figure: the Timeline figure is named 'figure1.png' but should be '{slug}_trend.png' (relabel).",
            f"6. Figure: '{slug}_cause_diagram.png' is placed in Description but belongs in Contributing Factors (move).",
            f"7. Figure: '{slug}_overview.png' is missing from Description entirely (insert).",
            "8. Table: column order is Owner, Action, Due Date but should lead with Action.",
            "9. Table: the required 'Verification' column is missing.",
            "10. Table: font is Comic Sans with a plain header; the template requires Arial with a table-header style.",
            "11. Page elements: the 'Figure 2' cross-reference points to a figure not placed there (dangling).",
            "12. Page elements: the Timeline figure has no caption, but the template requires one.",
            "13. Page elements: figure numbers must be renumbered in order.",
            "14. Page elements: the Corrective Actions table has no title, but the template requires one.",
            "15. Page elements: the footer is empty, so page numbers and the classification marking are missing.",
            "16. Conflict: two reviewers assign different severities (High vs Medium).",
        ]

        return ProjectSpec(
            title=subject, domain=domain,
            required_sections=required_sections, fields=fields,
            section_bodies=section_bodies, table=table,
            corpus=corpus, graphics=graphics,
            draft_title=subject, draft_sections=draft_sections,
            draft_header=subject, draft_footer="", draft_page_numbers=False,
            draft_classification="",
            cross_references=cross_references, corrections=corrections,
            seeded_defects=seeded_defects,
        )


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "event"


def _field_report(subject: str, correct: dict) -> str:
    return f"""{subject.upper()} FIELD REPORT

Date: {correct['event_date']}
Site: {correct['site']}
Author: Field Engineering

Summary
On {correct['event_date']} the monitored system at {correct['site']} showed a
measured deviation over a {correct['duration']} window. The deviation began after
a threshold alarm and recovered following manual intervention.

Observations
- A threshold alarm fired shortly before the deviation began.
- Firmware version {correct['firmware']} was running on the affected unit.
- Environmental conditions drifted outside the specified band during the event.
- No downstream systems were affected.

Figures
- {_slug(subject)}_overview.png: Overview of the affected system.
- {_slug(subject)}_trend.png: Measured trend over the event window.

Preliminary Assessment
The event correlates with the environmental drift and the threshold alarm.
Firmware {correct['firmware']} behavior is a suspected contributing factor.
"""


def _rca_notes(correct: dict) -> str:
    return f"""# Root Cause Analysis Notes

Author: Reliability Engineering

## Findings

- Firmware {correct['firmware']} handles the deviation condition too slowly.
- The same signature appeared under sustained environmental drift.

## Corrective Action Assignments

- Action: Upgrade affected units to the validated firmware revision.
  Owner: Reliability Engineering. Due: 2026-10-20.
- Action: Add environmental drift alerting to the monitoring baseline.
  Owner: Site Operations. Due: 2026-10-25.

## Figures

- {_slug('')}cause_diagram.png: Root-cause diagram of the contributing factors.
"""
