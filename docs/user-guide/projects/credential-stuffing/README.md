# Customer Portal Credential-Stuffing Incident — walkthrough

A step-by-step walkthrough of sample project **#2**, an IT-security incident.
You upload the real source documents from `sample_docs/project/2/`; the app
builds the project and reconciles it. Compare your on-screen results against the
values quoted here — they are the real engine output.

This is a **correction** project. Nothing is hand-authored: you upload the
documents, the app derives the structure, fields, a flawed first attempt, and
grounded corrections, and the **Correction Pipeline** does the work.

---

## The scenario

The SOC detected credential-stuffing against the customer portal's web tier. You
upload the SOC alert, the incident review, and three figures. The pipeline fills
what the sources support, flags what they don't, and invents nothing.

---

## Step 1 — Create the project

Open the app (it starts empty on **New Project**) and create a project named
`Customer Portal Credential-Stuffing Incident`. Click **Create project** — you
are moved to the **Ingestion** tab to add its files.

---

## Step 2 — Ingestion: upload the real documents

Open the **Ingestion** tab and drop these files from `sample_docs/project/2/`
into the **Original corpus** area. Uploading them is what builds the project —
there is no JSON to assemble.

| Upload | Role |
|---|---|
| `corpus/soc_alert_2026-05-11.txt` | source document (SOC alert) |
| `corpus/incident_review_2026-05-20.md` | source document (incident review) |
| `corpus/figures/auth_flow_diagram.png` | figure |
| `corpus/figures/botnet_geo_distribution.png` | figure |
| `corpus/figures/login_volume_timeline.png` | figure |

**What you should see:** each file advances **ingest → parse → chunk → embed →
index** to **completed**, and the readiness banner flips from **"Add your
documents"** to **"Project ready"** once the app has built the project. The
Correction Pipeline and Report & Export tabs then unlock.

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab, then set **Draft**, **Single pass**,
**Source fidelity: JSON**.

---

## Step 4 — Read the corrected report (Draft mode)

The report is titled **"Customer Portal Breach Attempt — Incident Review"**, with
sections derived from the review (Scope, Findings, Contributing Factors,
Recommendation, Remediation Assignments). Draft mode produces **15 units: 5
corrected, 7 filled, 2 needs review, 1 unchanged** (the on-screen status row is
the authoritative tally; the **Corrected intermediate JSON** box shows the total,
**15**).

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `Date` | **corrected** (green) | 2026-05-20 | pulled from the review |
| `System` | **corrected** (green) | Customer Portal (web-tier) | pulled from the review |
| `Author` | **corrected** (green) | Security Engineering | pulled from the review |
| `Reviewer Sign-off` | **needs review** (amber) | *blank* | required, no source settles it |
| section bodies | **filled** (blue) | source-grounded prose | built from the corpus |

Confirm the guardrails: every corrected/filled value carries a **Source:** line;
the `Reviewer Sign-off` row is left **needs review** for a human (no guess).
Figures are placed next to the text that references them — `botnet_geo_distribution.png`
in **Scope**, and `auth_flow_diagram.png` + `login_volume_timeline.png` together
in **Findings** — and the real images render inline.

**Set and Undo:** use the inline control to **Set** the `Reviewer Sign-off`
value; it becomes **filled** with your entry. Click **Undo** to revert it and set
it again. Resolving one unit never disturbs another.

---

## Step 5 — Compare Template mode

Switch to **Template**: **15 units — 11 filled, 2 corrected, 2 needs review.**
The same values appear mostly as **filled** (populating a blank template) rather
than **corrected** (repairing a draft). `Reviewer Sign-off` stays **needs
review**.

---

## Step 6 — Export

**Report & Export** shows the corrected report with a Draft/Template toggle and a
**Rebuild** button that re-runs the correction. Export as JSON / Markdown / PDF
(always available) or DOCX/PPTX (offered based on what you uploaded). The real
figures are embedded; the Markdown export is a ZIP with a `figures/` folder;
filenames carry the project name + a UTC timestamp.

---

## What this project proves

Same guardrails as every correction case: you upload real documents and the app
builds the project; values are literal source-grounded fills with provenance, no
fabrication; unsupported fields are sent to **needs review** (Set/Undo), and
figures travel with the text that references them.
