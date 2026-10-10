# Aircraft Hydraulic Decay Maintenance Event — walkthrough

A step-by-step walkthrough of sample project **#5**, an aviation-maintenance
reliability event. You upload the real source documents from
`sample_docs/project/5/`; the app builds the project and reconciles it. Compare
your results against the values quoted here — they are the real engine output.

This is a **correction** project. Nothing is hand-authored: you upload the
documents, the app derives the structure, fields, a flawed first attempt, and
grounded corrections, and the **Correction Pipeline** does the work.

---

## The scenario

Hydraulic System B on regional-jet tail 512 showed pressure decay. You upload
the maintenance log, the reliability review, and three figures. The pipeline
fills what the sources support, flags what they don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Aircraft Hydraulic Decay Maintenance Event`. Click **Create project** — you are
moved to the **Ingestion** tab to add its files.

---

## Step 2 — Ingestion: upload the real documents

Open the **Ingestion** tab and drop these files from `sample_docs/project/5/`
into the **Original corpus** area. Uploading them is what builds the project —
there is no JSON to assemble.

| Upload | Role |
|---|---|
| `corpus/maintenance_log_2026-09-06.txt` | source document (maintenance log) |
| `corpus/reliability_review_2026-09-16.md` | source document (reliability review) |
| `corpus/figures/hydraulic_schematic_system_b.png` | figure |
| `corpus/figures/pressure_decay_plot.png` | figure |
| `corpus/figures/seal_wear_comparison.png` | figure |

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

The report is titled **"AIRCRAFT MAINTENANCE DISCREPANCY REPORT"**, with sections
derived from the review (Scope, Findings, Contributing Factors, Recommendation).
Draft mode produces **16 units: 5 corrected, 8 filled, 2 needs review, 1
unchanged** (the on-screen status row is the authoritative tally; the **Corrected
intermediate JSON** box shows the total, **16**).

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `Date` | **corrected** (green) | 2026-09-06 | pulled from the sources |
| `System` | **corrected** (green) | Regional Jet Tail 512 Hydraulic System B | pulled from the sources |
| `Author` | **corrected** (green) | M. Farooqi, Line Maintenance Technician | pulled from the sources |
| `Reviewer Sign-off` | **needs review** (amber) | *blank* | required, no source settles it |
| section bodies | **filled** (blue) | source-grounded prose | built from the corpus |

Confirm the guardrails: every corrected/filled value carries a **Source:** line;
`Reviewer Sign-off` is left **needs review**. Figures are placed next to the text
that references them — `hydraulic_schematic_system_b.png` + `pressure_decay_plot.png`
together in **Scope**, and `seal_wear_comparison.png` in **Findings** — with the
real images rendered inline. The corrective-actions table appears in
**Recommendation**, filled from the review's "Corrective Action Assignments"
block.

**Set and Undo:** **Set** a value for `Reviewer Sign-off`; **Undo** reverts it so
you can set it again. Resolving one unit never disturbs another.

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
