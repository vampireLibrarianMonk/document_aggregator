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

## Step 1 — Open the project

- **Samples tab (recommended, if enabled):** **Samples** → *Aircraft Hydraulic
  Decay Maintenance Event* → **Use this sample** → lands on the Correction
  Pipeline tab, populated.
- **From the repo:** create a project named `Aircraft Hydraulic Decay
  Maintenance Event` and inspect `sample_docs/project/5/` (corpus:
  `maintenance_log_2026-09-06.txt`, `reliability_review_2026-09-16.md`; flawed
  draft + blank template; reviewer `corrections/`).

**What you should see:** four stage boxes, each with a count.

---

## Step 2 — Choose the mode

Set **Draft**, **Single pass**, **Source fidelity: JSON**.

---

## Step 3 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 1 unchanged, 4 filled, **10 corrected**, 1
needs review, 1 conflict (the most corrected of the six cases).

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `identifiers.site` | **corrected** (green) | Regional Jet Tail 512 Hydraulic System B | draft had the wrong label |
| `identifiers.incident_date` | **corrected** (green) | 2026-09-16 | draft date was wrong |
| `contributing_factors.software_version` | **filled** (blue) | B2.3 | affected actuator software (draft said B2.5) |
| `description.duration` | **filled** (blue) | seven hour | corrected (draft said "three hour") |
| `identifiers.severity` | **conflict** (red) | *blank* | reviewers disagree; "Routine" was challenged |
| section bodies | **corrected** (green) | source-grounded prose | rebuilt from the corpus |

Confirm the guardrails: every corrected value has a **Source:** line; `severity`
stays an unresolved **conflict**; the discipline findings list the
figure/table/furniture defects.

---

## Step 4 — Compare Template mode

Switch to **Template**: **16 units — 11 filled, 3 corrected, 1 needs review, 1
conflict**. The same values appear as **filled** rather than **corrected**;
`severity` stays a **conflict**.

---

## Step 5 — Export (optional)

**Report & Export** → export the corrected report.

---

## What this project proves

Literal source-grounded fills, no fabrication, disagreements preserved as a
`conflict`, unsupported fields sent to review, template formatting rules checked.
