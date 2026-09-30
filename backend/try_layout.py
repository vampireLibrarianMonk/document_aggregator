"""Ad-hoc: exercise the vector-layout extractor + geometric inspector against
synthetic PDFs with known geometry (compliant vs violations)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.discipline.spec import LayoutRule  # noqa: E402
from app.layout.geometry import extract_layout  # noqa: E402
from app.layout.inspect_layout import inspect_layout  # noqa: E402
from app.layout.render import synthetic_pdf  # noqa: E402


def main() -> None:
    rule = LayoutRule(caption_position="below", caption_align_tol=0.15,
                      table_title_position="above", footer_band=0.12,
                      page_number_region="footer-center", header_band=0.12)
    for cap, ttl, pn in [("below", "above", "footer-center"),
                         ("above", "above", "footer-center"),
                         ("below", "below", "footer-center"),
                         ("below", "above", "header-right")]:
        pdf = synthetic_pdf(caption_position=cap, table_title_position=ttl, page_number=pn)
        pages = extract_layout(pdf)
        roles = [b.role for b in pages[0].boxes]
        findings = inspect_layout(pages, rule)
        caps = [(f.status, f.value["observed"]) for f in findings if "caption_geometry" in f.key]
        tts = [(f.status, f.value["observed"]) for f in findings if "title_geometry" in f.key]
        pns = [(f.status, f.value["observed"]) for f in findings if "page_number_geometry" in f.key]
        figs = roles.count("figure")
        print(f"cap={cap} ttl={ttl} pn={pn} figs={figs} tables={roles.count('table')}")
        print(f"   caption:{caps} title:{tts} pagenum:{pns}")


if __name__ == "__main__":
    main()
