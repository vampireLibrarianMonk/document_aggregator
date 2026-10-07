# Injection Molding Line Defect Event — walkthrough

A step-by-step walkthrough of sample project **#4**, a manufacturing-quality
defect event. The source material lives in the repo under
`sample_docs/project/4/`; compare your results against it and the expected
values quoted here.

This is a **correction** project (source corpus + flawed first-draft report +
reviewer comments). All values below are the real engine output.

---

## The scenario

Injection molding Line 3 produced out-of-spec housings. The first-attempt report
carries seeded value, figure, table, and furniture defects plus a genuine
reviewer **conflict** on severity. The pipeline fixes what the sources support,
flags what they don't, and invents nothing.

---

## Step 1 — Open the project

- **Samples tab (recommended, if enabled):** **Samples** → *Injection Molding
  Line Defect Event* → **Use this sample** → lands on the Correction Pipeline
  tab, populated.
- **From the repo:** create a project named `Injection Molding Line Defect
  Event` and inspect `sample_docs/project/4/` (corpus:
  `qc_report_2026-08-09.txt`, `rca_review_2026-08-19.md`; flawed draft + blank
  template; reviewer `corrections/`).

**What you should see:** four stage boxes, each with a count.

---

## Step 2 — Choose the mode

Set **Draft**, **Single pass**, **Source fidelity: JSON**.

---

## Step 3 — Read the corrected report (Draft mode)

Draft mode produces **17 units**: 3 unchanged, 4 filled, 8 corrected, 1 needs
review, 1 conflict.

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `identifiers.site` | **unchanged** (grey) | Injection Molding Line 3 | already correct |
| `identifiers.incident_date` | **unchanged** (grey) | 2026-08-09 | already correct |
| `contributing_factors.firmware` | **filled** (blue) | 2.0.5 | affected controller firmware (draft said 2.1.1) |
| `description.duration` | **filled** (blue) | five hour | corrected (draft said "two hour") |
| `identifiers.severity` | **conflict** (red) | *blank* | reviewers disagree; "Cosmetic" was challenged |
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
