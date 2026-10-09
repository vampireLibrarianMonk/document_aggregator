# Aircraft Hydraulic Decay Maintenance Event — walkthrough

A step-by-step walkthrough of sample project **#5**, an aviation-maintenance
reliability event. The source material lives in the repo under
`sample_docs/project/5/`; compare your results against it and the expected
values quoted here.

This is a **correction** project (source corpus + flawed first-draft report +
reviewer comments). All values below are the real engine output.

---

## The scenario

Hydraulic System B on regional-jet tail 512 showed a pressure-decay
discrepancy. The first-attempt report carries seeded value, figure, table, and
furniture defects plus a genuine reviewer **conflict** on severity — and more
corrected fields than the other cases. The pipeline fixes what the sources
support, flags what they don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Aircraft Hydraulic Decay Maintenance Event`. Click **Create project** — you are
moved to the **Ingestion** tab to add its files. The scenario files live in the
repo under `sample_docs/project/5/`.

---

## Step 2 — Ingestion: upload the project files

Open the **Ingestion** tab. Uploading the project's files is what populates the
Correction Pipeline. The readiness banner requires **the manifest + corpus +
corrections** and at least one of {**template**, **first draft**}. Drop the
manifest (`project.json`) and figure manifest (`corpus/graphics.json`) into
**Original corpus** alongside the source docs; the app routes each file by name.

Upload the files from `sample_docs/project/5/`:

| Area | Upload | Role |
|---|---|---|
| Original corpus | `project.json` | project manifest |
| Original corpus | `corpus/maintenance_log_2026-09-06.txt`, `corpus/reliability_review_2026-09-16.md` | source documents (required) |
| Original corpus | `corpus/graphics.json` | named-figure manifest |
| Template | `template/incident_report_template.json` | **template pathway** |
| First draft | `first_attempt/incident_report_draft.json` | **first-draft pathway** |
| Corrections | `corrections/comments.json` | reviewer feedback (required) |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Inputs
incomplete"** to **"Inputs ready"** once the manifest + corpus + corrections +
one of template/first-draft are present. The Correction Pipeline and Report &
Export tabs then unlock. (The **template** file is the blank structure; the
**first-draft** file is the flawed attempt — both ship so you can compare Draft
and Template modes.)

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab, then set **Draft**, **Single pass**,
**Source fidelity: JSON**.

---

## Step 4 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 1 unchanged, 4 filled, **10 corrected**, 1
needs review, 1 conflict (the most corrected of the six cases).

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `identifiers.site` | **corrected** (green) | Regional Jet Tail 512 Hydraulic System B | draft had the wrong label |
| `identifiers.incident_date` | **corrected** (green) | 2026-09-16 | draft date was wrong |
| `contributing_factors.software_version` | **filled** (blue) | B2.3 | affected actuator software (draft said B2.5) |
| `description.duration` | **filled** (blue) | seven hour | corrected (draft said "three hour") |
| `identifiers.severity` | **conflict** (red) | *blank* | the draft's "Routine" was challenged; the two candidates shown are **Critical** and **Major** |
| section bodies | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Confirm the guardrails: every corrected value has a **Source:** line; `severity`
stays an unresolved **conflict**; the discipline findings list the
figure/table/furniture defects.

---

## Step 5 — Compare Template mode

Switch to **Template**: **16 units — 11 filled, 3 corrected, 1 needs review, 1
conflict**. The same values appear as **filled** rather than **corrected**;
`severity` stays a **conflict**.

---

## Step 6 — Export (optional)

**Report & Export** → export the corrected report.

---

## What this project proves

Literal source-grounded fills, no fabrication, disagreements preserved as a
`conflict`, unsupported fields sent to review, template formatting rules checked.
