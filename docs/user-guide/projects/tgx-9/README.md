# TGX-9 Telemetry Gateway Incident — walkthrough

A complete, step-by-step walkthrough of the **TGX-9** sample (sample project
**#1**) through the app, from an empty start to a corrected report you can read,
verify, and export. The source documents live in the repo under
`sample_docs/project/1/` — you upload those, and the app builds the rest.

This is a **correction** project. You upload the real source documents; the app
derives everything the **Correction Pipeline** needs from them (the structure,
the fields, a flawed first-attempt draft, and grounded corrections) and
reconciles it into one corrected, source-grounded report. Nothing is
hand-authored and nothing is invented — every value is pulled from your
documents. All numbers below are the real engine output.

---

## The scenario

A TGX-9 telemetry gateway at the North Ridge relay station dropped inbound
sensor packets under thermal stress. You upload the field report, the root-cause
notes, and the three figures. The app reads those documents, builds the project,
and the pipeline fills what the sources support, flags what they don't, and
refuses to invent anything.

---

## Step 1 — Create the project

Open the app (it starts empty on the **New Project** tab) and create the
project:

- **Project name:**

  ```
  TGX-9 Telemetry Gateway Incident
  ```

Click **Create project**. The app selects it in the top selector and moves you
to the **Ingestion** tab. It has no documents yet — you add them next.

---

## Step 2 — Ingestion: upload the real documents

The **Ingestion** tab is where you add the project's source documents. Uploading
them is what builds the project and populates the Correction Pipeline — there is
no JSON to assemble. Drop these files from `sample_docs/project/1/` into the
**Original corpus** area:

| Upload | Role |
|---|---|
| `corpus/field_report_2026-03-02.txt` | source document (the field report) |
| `corpus/root_cause_notes_2026-03-15.md` | source document (the root-cause notes) |
| `corpus/figures/packet_loss_vs_temp.png` | figure |
| `corpus/figures/site_network_topology.png` | figure |
| `corpus/figures/cabinet_thermal_layout.png` | figure |

> Optional: you can also drop the raw reviewer emails
> (`corrections/emails/email_*.txt`) into the **Corrections** area. They are not
> required — the app derives the corrections from the corpus itself.

**What you should see:** each file appears as a row and advances through
**ingest → parse → chunk → embed → index** to **completed** (embeddings run
locally with all-MiniLM-L6-v2). The readiness banner starts as **"Add your
documents"** and flips to **"Project ready"** once the app has built the project
from your corpus. When it is ready, the **Correction Pipeline** and **Report &
Export** tabs unlock.

> How the app builds it: your `.md`/`.txt` notes define the document's structure
> (its headings become the report's sections); labelled lines like `Date:` and
> `Site:` become fields; the `Figures` section and the figure images become the
> placed figures; and the `Corrective Action Assignments` block becomes a table.

---

## Step 3 — Choose the mode

Open the **Correction Pipeline** tab. You should see four stage boxes —
**Original corpus**, **First attempt**, **Comments / emails**, **Corrected
intermediate JSON** — each with a count. At the top, set:

- **Draft vs Template:** start with **Draft** (correct the completed-but-flawed
  attempt the app built). You'll try **Template** in Step 5.
- **Single pass vs Rounds:** **Single pass**.
- **Source fidelity:** **JSON** (the clean baseline).

**What you should see:** the corrected report renders below the stage boxes, one
row per unit, each with a status tag and a short **Source:** line.

---

## Step 4 — Read the corrected report (Draft mode)

The report is titled **"TGX-9 Root Cause Analysis Notes"** and has four sections
derived from the notes: **Scope**, **Findings**, **Contributing Factors**,
**Recommendation**. The status row above the report reads **16 units — 5
corrected, 8 filled, 2 needs review, 1 unchanged** (the **Corrected intermediate
JSON** box shows the total, **16**). Here is what to look for and verify.

| Unit | Status | Value you should see | Why |
|---|---|---|---|
| `Date` | **corrected** (green) | 2026-03-02 | pulled from the field report |
| `Site` | **corrected** (green) | North Ridge Relay Station | pulled from the field report |
| `Author` | **corrected** (green) | J. Okafor, Field Engineering | pulled from the field report (kept whole) |
| `Reviewer Sign-off` | **needs review** (amber) | *blank* | required, but no source settles it — a human supplies it |
| section bodies (Scope, Findings, Contributing Factors, Recommendation) | **filled** (blue) | source-grounded prose | built from the corpus |

> The status counts total every reconciled unit — the identifier fields and
> section bodies you see as rows, plus the structural units the engine tracks
> behind each section. The on-screen status row is the authoritative tally;
> expect **16** total for this project.

Things to confirm (the guardrails):
- **No fabrication.** Every corrected/filled value has a **Source:** line you can
  hover; the value comes verbatim from your documents.
- **Gaps surface, not guesses.** `Reviewer Sign-off` is **needs review** — the
  app leaves it for you rather than inventing a signatory.
- **Figures are placed next to the text that references them.** The report shows
  all **three** figures: `packet_loss_vs_temp.png` and `site_network_topology.png`
  in **Scope**, and `cabinet_thermal_layout.png` in **Recommendation**. The real
  images render inline (not a placeholder box).
- **The corrective-actions table** appears in **Recommendation**, filled from the
  notes' "Corrective Action Assignments" block.

### Set and Undo

For any **needs review** unit, use the inline control to **Set** a value (type
it and confirm). The unit becomes **filled** with your value and a **Source:**
line crediting your resolution. Made a mistake? Click **Undo** on that unit to
revert it to the engine's result so you can set it again. Setting one unit never
disturbs another — each decision sticks.

---

## Step 5 — Compare Template mode

Switch **Draft → Template**. Template mode fills the *blank* report template from
the sources instead of correcting a draft.

**What you should see:** **16 units — 12 filled, 2 corrected, 2 needs review.**
The same values appear (`Date`, `Site`, `Author`, the section bodies), but mostly
as **filled** rather than **corrected** — because there was no prior wrong value
to replace, only an empty field to populate. `Reviewer Sign-off` is still **needs
review**: a missing authority doesn't resolve itself just because the starting
point was blank.

This contrast is the lesson: *draft* repairs an existing attempt; *template*
builds from the sources. Same engine, same provenance, same no-fabrication rule.

---

## Step 6 — Export

Open **Report & Export**. It shows the **corrected report** (the Correction
Pipeline's output), with a **Draft/Template** toggle and a **Rebuild** button
that re-runs the correction (reflecting any values you Set). Export in any
offered format:

- **JSON** / **Markdown** / **PDF** — always available.
- **DOCX** / **PPTX** — offered based on what you uploaded.
- The **real figures are embedded** in the DOCX/PDF/PPTX. The **Markdown** export
  downloads as a **ZIP** containing the `.md` plus a `figures/` folder, so the
  images render like a GitHub README when unpacked.
- Filenames carry the project name and a UTC timestamp, e.g.
  `tgx-9-telemetry-gateway-incident_20260312T143015Z.docx`.

The export is exactly the corrected content you saw on screen — nothing added.

---

## What this project proves

- **You upload real documents; the app builds the project.** No hand-authored
  JSON.
- **Values are literal fills from source spans**, never computed or invented,
  each with a hoverable **Source:** line.
- **Required-but-unsupported fields** (here, the reviewer sign-off) surface as
  **needs review** for a human instead of a guess — and you can **Set** then
  **Undo** them.
- **Figures travel with their text** — placed in the section that references
  them, rendered inline, and embedded in the exports.
