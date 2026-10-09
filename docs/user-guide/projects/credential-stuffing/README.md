# Customer Portal Credential-Stuffing Incident — walkthrough

A step-by-step walkthrough of sample project **#2**, an IT-security incident.
The source material lives in the repo under `sample_docs/project/2/`; compare
your on-screen results against it and the expected values quoted here.

This is a **correction** project (source corpus + flawed first-draft report +
reviewer comments), so the **Correction Pipeline** tab does the work. All values
below are the real engine output.

---

## The scenario

The SOC detected credential-stuffing against the customer portal's web tier.
The first-attempt report carries seeded value, figure, table, and furniture
defects plus one genuine reviewer **conflict** on severity. The pipeline fixes
what the sources support, flags what they don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Customer Portal Credential-Stuffing Incident`. Click **Create project** — you
are moved to the **Ingestion** tab to add its files. The scenario files live in
the repo under `sample_docs/project/2/`.

---

## Step 2 — Ingestion: upload the project files

Open the **Ingestion** tab. Uploading the project's files is what populates the
Correction Pipeline. There are four upload areas; the readiness banner requires
**the manifest + corpus + corrections** and at least one of {**template**,
**first draft**} — the two valid pathways. Drop the manifest (`project.json`) and
the figure manifest (`corpus/graphics.json`) into **Original corpus** alongside
the source docs; the app routes each file to its role by name.

Upload the files from `sample_docs/project/2/`:

| Area | Upload | Role |
|---|---|---|
| Original corpus | `project.json` | project manifest |
| Original corpus | `corpus/soc_alert_2026-05-11.txt`, `corpus/incident_review_2026-05-20.md` | source documents (required) |
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

Draft mode produces **17 units**: 2 unchanged, 4 filled, 9 corrected, 1 needs
review, 1 conflict.

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `identifiers.site` | **corrected** (green) | Customer Portal (web-tier) | draft had the wrong site label |
| `identifiers.incident_date` | **unchanged** (grey) | 2026-05-11 | already correct |
| `contributing_factors.auth_version` | **filled** (blue) | 3.4.0 | affected auth version (draft said 3.5.2) |
| `description.duration` | **filled** (blue) | six hour | corrected from the review (draft said "two hour") |
| `identifiers.severity` | **conflict** (red) | *blank* | the draft's "Informational" was challenged; the two candidates shown are **High** and **Medium** |
| section bodies | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Confirm the guardrails: every corrected value carries a **Source:** line; the
`severity` row shows *conflict / unresolved — choose a candidate below* and
fills in nothing; the discipline findings below list the figure/table/furniture
defects (missing caption, missing table title, wrong table style/column order,
empty footer / missing page numbers + classification).

---

## Step 5 — Compare Template mode

Switch to **Template**: **16 units — 11 filled, 3 corrected, 1 needs review, 1
conflict**. The same values appear as **filled** (populating a blank template)
rather than **corrected** (repairing a wrong draft). `severity` stays a
**conflict**.

---

## Step 6 — Export (optional)

**Report & Export** → export the corrected report. The export is exactly the
assembled corrected content.

---

## What this project proves

Same guardrails as every correction case: literal source-grounded fills, no
fabrication, disagreements preserved as a `conflict`, unsupported fields sent to
review, and template formatting rules checked.
