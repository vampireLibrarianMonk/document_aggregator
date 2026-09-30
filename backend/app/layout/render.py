"""DOCX -> PDF rendering for the vector-layout tier.

Production path (Linux/RHEL enclave): LibreOffice headless renders DOCX to PDF,
from which geometry is extracted. Gated on `soffice` presence; returns None when
absent so the pipeline degrades to structural inspection.

For offline testing where LibreOffice is unavailable, `synthetic_pdf` builds a
PDF with elements at KNOWN geometry (via reportlab) so the extractor and
geometric inspector can be exercised deterministically.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def soffice_available() -> bool:
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))


def docx_to_pdf_bytes(docx_bytes: bytes) -> bytes | None:
    """Render DOCX bytes to PDF bytes via LibreOffice headless. None if soffice
    is unavailable or conversion fails (caller degrades to structural tier)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.docx"
        src.write_bytes(docx_bytes)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                check=True, capture_output=True, timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        pdf = Path(tmp) / "in.pdf"
        return pdf.read_bytes() if pdf.exists() else None


def synthetic_pdf(caption_position: str = "below", table_title_position: str = "above",
                  page_number: str = "footer-center") -> bytes:
    """Build a PDF with elements at known geometry for deterministic testing.
    Places a figure rectangle + caption, a table + title, and a page number so
    the geometric inspector can be checked against ground truth."""
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)

    # Figure rectangle in the upper-middle of the page.
    fig_x0, fig_y0, fig_w, fig_h = 200, 560, 200, 120  # reportlab origin = bottom-left
    c.rect(fig_x0, fig_y0, fig_w, fig_h, stroke=1, fill=0)
    fig_cx = fig_x0 + fig_w / 2

    # Caption below or above the figure, centered on it.
    c.setFont("Helvetica", 10)
    cap = "Figure 1: System diagram"
    cap_w = c.stringWidth(cap, "Helvetica", 10)
    cap_x = fig_cx - cap_w / 2
    cap_y = (fig_y0 - 16) if caption_position == "below" else (fig_y0 + fig_h + 6)
    c.drawString(cap_x, cap_y, cap)

    # Table region lower on the page + a title above/below it.
    tbl_x0, tbl_y0, tbl_w, tbl_h = 120, 300, 360, 90
    c.rect(tbl_x0, tbl_y0, tbl_w, tbl_h, stroke=1, fill=0)
    # add a couple of internal lines so find_tables detects it
    c.line(tbl_x0, tbl_y0 + tbl_h / 2, tbl_x0 + tbl_w, tbl_y0 + tbl_h / 2)
    c.line(tbl_x0 + tbl_w / 3, tbl_y0, tbl_x0 + tbl_w / 3, tbl_y0 + tbl_h)
    title = "Table 1: Corrective Actions"
    title_y = (tbl_y0 + tbl_h + 6) if table_title_position == "above" else (tbl_y0 - 16)
    c.drawString(tbl_x0, title_y, title)

    # Page number.
    pn = "1"
    if page_number == "footer-center":
        c.drawString(W / 2 - 3, 30, pn)
    elif page_number == "footer-right":
        c.drawString(W - 60, 30, pn)
    elif page_number == "header-right":
        c.drawString(W - 60, H - 40, pn)
    c.showPage()
    c.save()
    return buf.getvalue()
