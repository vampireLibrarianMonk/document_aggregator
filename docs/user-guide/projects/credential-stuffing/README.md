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

## Step 1 — Open the project

- **Samples tab (recommended, if enabled):** open **Samples**, find *Customer
  Portal Credential-Stuffing Incident*, click **Use this sample** → you land on
  the **Correction Pipeline** tab, fully populated.
- **From the repo:** create a project on **New Project** named `Customer Portal
  Credential-Stuffing Incident` and inspect `sample_docs/project/2/`
  (corpus: `soc_alert_2026-05-11.txt`, `incident_review_2026-05-20.md`; the
  flawed draft and the blank template under `first_attempt/` and `template/`;
  reviewer `corrections/`).

**What you should see:** four stage boxes — Original corpus, First attempt,
Comments / emails, Corrected intermediate JSON — each with a count.

---

## Step 2 — Ingestion: upload the document pathways

Open the **Ingestion** tab. There are four upload areas, and the readiness
banner requires **corpus** + **corrections** + at least one of
{**template**, **first draft**} — the two valid pathways. (If you used **Use
this sample** in Step 1, these are already loaded; read this to see what the
sample provided, then go to Step 3.)

Upload the files from `sample_docs/project/2/`:

| Area | Upload | Pathway |
|---|---|---|
| Original corpus | `corpus/soc_alert_2026-05-11.txt`, `corpus/incident_review_2026-05-20.md` | — (required) |
| Template | `template/` incident template JSON | **template pathway** |
| First draft | `first_attempt/` draft JSON | **first-draft pathway** |
| Corrections | `corrections/comments.json` | — (required) |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Inputs
incomplete"** to **"Inputs ready"** once corpus + corrections + one of
template/first-draft are present. The Correction Pipeline and Report & Export
tabs then unlock. (The **template** file is the blank structure; the
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
| `identifiers.severity` | **conflict** (red) | *blank* | reviewers disagree; "Informational" was challenged |
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
