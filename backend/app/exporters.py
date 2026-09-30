"""Exporters: render the intermediate report JSON into deliverable formats.

JSON and Markdown are always available (stdlib). DOCX/PPTX/PDF require optional
libraries and raise a clear error if the library is missing, rather than
failing opaquely. Rendering is a formatting step over already-assembled
content — nothing is generated here.
"""
from __future__ import annotations

import io
import json


def export_json(report: dict) -> tuple[bytes, str, str]:
    data = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
    return data, "application/json", "report.json"


def export_markdown(report: dict) -> tuple[bytes, str, str]:
    lines: list[str] = []
    proj = report.get("project", {})
    lines.append(f"# Aggregated Report — {proj.get('name', '')}")
    lines.append("")
    s = report.get("summary", {})
    lines.append(f"- Source documents: {s.get('source_documents', 0)}")
    lines.append(f"- Supplementals: {s.get('supplementals_total', 0)} "
                 f"({s.get('negative_supplementals', 0)} negative)")
    lines.append(f"- Ordering: {report.get('ordering', '')}")
    lines.append("")
    for sec in report.get("sections", []):
        lines.append(f"## {sec['title']}")
        lines.append(f"*Effective DTG: {sec.get('effective_dtg')} "
                     f"(source: {sec.get('effective_dtg_source')})*")
        lines.append("")
        for item in sec.get("content", []):
            if item.get("table"):
                for row in item["table"]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
            elif item["type"] == "heading":
                lines.append(f"### {item['text']}")
            elif item["type"] == "list_item":
                lines.append(f"- {item['text']}")
            else:
                lines.append(item["text"])
            lines.append("")
        if sec.get("supplementals"):
            lines.append("**Supplementals:**")
            for sup in sec["supplementals"]:
                lines.append(f"- ({sup['kind']}/{sup['sentiment']}) {sup['author']}: {sup['body']}")
            lines.append("")
    return "\n".join(lines).encode("utf-8"), "text/markdown", "report.md"


def export_docx(report: dict) -> tuple[bytes, str, str]:
    try:
        import docx
    except ImportError as exc:
        raise RuntimeError("python-docx not installed; cannot export DOCX") from exc
    document = docx.Document()
    proj = report.get("project", {})
    document.add_heading(f"Aggregated Report — {proj.get('name', '')}", level=0)
    for sec in report.get("sections", []):
        document.add_heading(sec["title"], level=1)
        document.add_paragraph(
            f"Effective DTG: {sec.get('effective_dtg')} (source: {sec.get('effective_dtg_source')})"
        ).italic = True
        for item in sec.get("content", []):
            if item.get("table") and item["table"]:
                rows = item["table"]
                tbl = document.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        tbl.cell(ri, ci).text = cell
            elif item["type"] == "heading":
                document.add_heading(item["text"], level=2)
            elif item["type"] == "list_item":
                document.add_paragraph(item["text"], style="List Bullet")
            else:
                document.add_paragraph(item["text"])
        for sup in sec.get("supplementals", []):
            document.add_paragraph(
                f"[{sup['kind']}/{sup['sentiment']}] {sup['author']}: {sup['body']}"
            )
    buf = io.BytesIO()
    document.save(buf)
    return (buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "report.docx")


def export_pptx(report: dict) -> tuple[bytes, str, str]:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("python-pptx not installed; cannot export PPTX") from exc
    prs = Presentation()
    title_layout = prs.slide_layouts[0]
    body_layout = prs.slide_layouts[1]
    proj = report.get("project", {})
    s = prs.slides.add_slide(title_layout)
    s.shapes.title.text = f"Aggregated Report — {proj.get('name', '')}"
    s.placeholders[1].text = report.get("ordering", "")
    for sec in report.get("sections", []):
        slide = prs.slides.add_slide(body_layout)
        slide.shapes.title.text = sec["title"]
        body = slide.placeholders[1].text_frame
        body.text = f"Effective DTG: {sec.get('effective_dtg')}"
        for item in sec.get("content", [])[:12]:
            p = body.add_paragraph()
            p.text = (item.get("text", "") or "")[:180]
    buf = io.BytesIO()
    prs.save(buf)
    return (buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "report.pptx")


def export_pdf(report: dict) -> tuple[bytes, str, str]:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
    except ImportError as exc:
        raise RuntimeError("reportlab not installed; cannot export PDF") from exc
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    flow = []
    proj = report.get("project", {})
    flow.append(Paragraph(f"Aggregated Report — {proj.get('name', '')}", styles["Title"]))
    flow.append(Spacer(1, 12))
    for sec in report.get("sections", []):
        flow.append(Paragraph(sec["title"], styles["Heading1"]))
        flow.append(Paragraph(
            f"Effective DTG: {sec.get('effective_dtg')} "
            f"(source: {sec.get('effective_dtg_source')})", styles["Italic"]))
        for item in sec.get("content", []):
            if item.get("table") and item["table"]:
                flow.append(Table(item["table"]))
            else:
                style = styles["Heading2"] if item["type"] == "heading" else styles["BodyText"]
                text = (item.get("text", "") or "").replace("<", "&lt;").replace(">", "&gt;")
                if text:
                    flow.append(Paragraph(text, style))
        for sup in sec.get("supplementals", []):
            flow.append(Paragraph(
                f"[{sup['kind']}/{sup['sentiment']}] {sup['author']}: {sup['body']}",
                styles["BodyText"]))
        flow.append(Spacer(1, 12))
    doc.build(flow)
    return buf.getvalue(), "application/pdf", "report.pdf"


EXPORTERS = {
    "json": export_json,
    "markdown": export_markdown,
    "docx": export_docx,
    "pptx": export_pptx,
    "pdf": export_pdf,
}


def export_report(report: dict, fmt: str) -> tuple[bytes, str, str]:
    fn = EXPORTERS.get(fmt)
    if not fn:
        raise ValueError(f"unsupported export format: {fmt}")
    return fn(report)
