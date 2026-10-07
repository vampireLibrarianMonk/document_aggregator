# TGX-9 Telemetry Gateway Incident — walkthrough

A complete, step-by-step walkthrough of running the **TGX-9** sample (sample
project **#1**) through the app, from an empty start to a corrected report you
can read and verify. The source material lives in the repo under
`sample_docs/project/1/` — compare your on-screen results against it and against
the expected values quoted here.

This is a **correction** project: it ships with a source corpus, a
completed-but-flawed first-draft report, and reviewer comments, so the
**Correction Pipeline** tab does the work. Each step says exactly what to do and
what you should see. All values below are the real engine output.

---

## The scenario

A TGX-9 telemetry gateway at the North Ridge relay station dropped inbound
sensor packets under thermal stress. The first-attempt report carries **16
seeded defects** across four classes — value errors, figure problems, table
problems, and page-element (furniture) problems — plus one genuine reviewer
**conflict**. The pipeline fixes what the sources support, flags what they
don't, and refuses to invent anything.

---

## Step 1 — Open the project

There are two ways in.

**A. Instantiate the sample (recommended).** If your administrator has enabled
samples (`SAMPLES_ENABLED=true`), open the **Samples** tab, find *TGX-9
Telemetry Gateway Incident*, and click **Use this sample**. The platform copies
it into a new project of your own and drops you on the **Correction Pipeline**
tab with all four components populated.

**B. Create + inspect from the repo.** Otherwise, create a project on **New
Project** (name it `TGX-9 Telemetry Gateway Incident`) and inspect the shipped
material under `sample_docs/project/1/`:
- **corpus** — `field_report_2026-03-02.txt`, `root_cause_notes_2026-03-15.md`,
  plus named figures under `corpus/figures/`.
- **first attempt** — `first_attempt/incident_report_draft.json` (the flawed
  draft) and `template/incident_report_template.json` (the blank template).
- **corrections** — `corrections/comments.json` and the email exports.

**What you should see:** the project is active in the top selector, and the
Correction Pipeline tab shows four stage boxes — **Original corpus**, **First
attempt**, **Comments / emails**, **Corrected intermediate JSON** — each with a
count.

---

## Step 2 — Choose the mode

At the top of the Correction Pipeline tab, set:

- **Draft vs Template:** start with **Draft** (fix the completed-but-flawed
  report). You'll try **Template** in Step 4.
- **Single pass vs Rounds:** **Single pass**.
- **Source fidelity:** **JSON** (the clean baseline).

**What you should see:** the corrected report renders below the stage boxes, one
row per unit, each with a status tag and a short **Source:** line.

---

## Step 3 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 3 unchanged, 4 filled, 8 corrected, 1 needs
review, 1 conflict. Here is what to look for and verify.

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `identifiers.site` | **unchanged** (grey) | North Ridge Relay Station | the draft already had it right |
| `identifiers.incident_date` | **unchanged** (grey) | 2026-03-02 | already correct |
| `contributing_factors.firmware` | **filled** (blue) | 4.2.1 | corrected from the reliability-lead email (draft said 4.2.0) |
| `description.duration` | **filled** (blue) | four hour | corrected from the QA comment (draft said "two hour") |
| `identifiers.severity` | **conflict** (red) | *blank* | two reviewers disagree (High vs Medium) — the tool refuses to pick |
| section bodies (description, timeline, contributing_factors) | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Things to confirm (the guardrails):
- **No fabrication.** The firmware and duration are literal values from the
  email/comment, each with a **Source:** line you can hover.
- **Conflict preserved.** `severity` shows *conflict / unresolved — choose a
  candidate below* and lists both **High** (regional director) and **Medium**
  (QA reviewer). The tool fills in nothing; you choose.
- **Formatting/placement findings.** Below the content, the discipline findings
  list the figure, table, and furniture defects (missing caption, missing table
  title, wrong table font/column order, empty footer / missing page numbers +
  classification). These come from comparing the draft against the template's
  own rules.

---

## Step 4 — Compare Template mode

Switch **Draft → Template**. Template mode fills the *blank* report template from
the sources instead of correcting a draft.

**What you should see:** **16 units**, now **11 filled, 3 corrected, 1 needs
review, 1 conflict**. The same values appear (`firmware` 4.2.1, `duration` four
hour, `site` North Ridge Relay Station), but as **filled** rather than
**corrected** — because there was no prior wrong value to replace, only an empty
field to populate. `severity` is still a **conflict**: a disagreement doesn't go
away just because the starting point was blank.

This contrast is the lesson: *draft* judges and repairs an existing attempt;
*template* builds from the sources. Same engine, same provenance, same
no-fabrication rule.

---

## Step 5 — Export (optional)

Open **Report & Export** and export the corrected report (JSON / Markdown / DOCX
/ PPTX / PDF). The export is the assembled corrected content — nothing is added
beyond what you saw on screen.

---

## What this project proves

- **Values are literal fills from source spans**, never computed or invented.
- **Disagreements become a `conflict`** with all candidates preserved; the tool
  never auto-resolves one.
- **Required-but-unsupported fields** (here, the challenged severity) surface for
  a human instead of a guess.
- **Formatting/placement rules** are learned from the template and checked, so
  figure/table/furniture defects are reported, not silently passed.
