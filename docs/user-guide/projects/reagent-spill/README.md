# Clinical Lab Reagent Spill Safety Event — walkthrough

A step-by-step walkthrough of sample project **#3**, a clinical-laboratory
safety event. You upload the real source documents from `sample_docs/project/3/`;
the app builds the project and reconciles it. Compare your results against the
values quoted here — they are the real engine output.

This is a **correction** project. Nothing is hand-authored: you upload the
documents, the app derives the structure, fields, a flawed first attempt, and
grounded corrections, and the **Correction Pipeline** does the work.

---

## The scenario

A reagent spill occurred on the Bay 2 chemistry analyzer. You upload the lab
event report, the safety review, and three figures. The pipeline fills what the
sources support, flags what they don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Clinical Lab Reagent Spill Safety Event`. Click **Create project** — you are
moved to the **Ingestion** tab to add its files.

---

## Step 2 — Ingestion: upload the real documents

Open the **Ingestion** tab and drop these files from `sample_docs/project/3/`
into the **Original corpus** area. Uploading them is what builds the project —
there is no JSON to assemble.

| Upload | Role |
|---|---|
| `corpus/lab_event_2026-07-03.txt` | source document (lab event report) |
| `corpus/safety_review_2026-07-14.md` | source document (safety review) |
| `corpus/figures/spill_containment_zone.png` | figure |
| `corpus/figures/sample_tray_layout.png` | figure |
| `corpus/figures/reagent_seal_cross_section.png` | figure |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Add your
documents"** to **"Project ready"**. The Correction Pipeline and Report & Export
tabs then unlock.

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab, then set **Draft**, **Single pass**,
**Source fidelity: JSON**.

---

## Step 4 — Read the corrected report (Draft mode)

The report is titled **"CLINICAL LABORATORY SAFETY EVENT REPORT"**, with sections
derived from the safety review (Scope, Findings, Contributing Factors,
Recommendation). Draft mode produces **16 units: 5 corrected, 8 filled, 2 needs
review, 1 unchanged** (the on-screen status row is the authoritative tally; the
**Corrected intermediate JSON** box shows the total, **16**).

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `Date` | **corrected** (green) | 2026-07-03 | pulled from the sources |
| `System` | **corrected** (green) | Automated Chemistry Analyzer Bay 2 | pulled from the sources |
| `Author` | **corrected** (green) | R. Delgado, Laboratory Safety Officer | pulled from the sources |
| `Reviewer Sign-off` | **needs review** (amber) | *blank* | required, no source settles it |
| section bodies | **filled** (blue) | source-grounded prose | built from the corpus |

Confirm the guardrails: every corrected/filled value carries a **Source:** line;
`Reviewer Sign-off` is left **needs review** for a human. Figures are placed next
to the text that references them — `spill_containment_zone.png` in **Scope**, and
`sample_tray_layout.png` + `reagent_seal_cross_section.png` together in
**Contributing Factors** — with the real images rendered inline. The
corrective-actions table appears in **Recommendation**, filled from the review's
"Corrective Action Assignments" block.

**Set and Undo:** **Set** a value for `Reviewer Sign-off` (it becomes filled);
**Undo** reverts it so you can set it again. Resolving one unit never disturbs
another.

---

## Step 5 — Compare Template mode

Switch to **Template**: **16 units — 12 filled, 2 corrected, 2 needs review.**
The same values appear mostly as **filled** rather than **corrected**;
`Reviewer Sign-off` stays **needs review**.

---

## Step 6 — Export

**Report & Export** shows the corrected report with a Draft/Template toggle and a
**Rebuild** button. Export as JSON / Markdown / PDF (always) or DOCX/PPTX
(offered based on what you uploaded). Real figures embedded; Markdown is a ZIP
with a `figures/` folder; filenames carry the project name + a UTC timestamp.

---

## What this project proves

Same guardrails as every correction case: you upload real documents and the app
builds the project; literal source-grounded fills with provenance; no
fabrication; unsupported fields sent to **needs review** (Set/Undo); figures
placed with the text that references them; and a table derived from the source.
