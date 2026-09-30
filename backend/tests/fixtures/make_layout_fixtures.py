"""Generate committed PDF fixtures for the vector-layout tests, so geometry
inspection runs offline without LibreOffice (which is absent in dev/CI here but
present in the RHEL enclave). Run once:  python make_layout_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.layout.render import synthetic_pdf  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    cases = {
        "compliant.pdf": dict(caption_position="below", table_title_position="above",
                              page_number="footer-center"),
        "caption_above.pdf": dict(caption_position="above", table_title_position="above",
                                  page_number="footer-center"),
        "title_below.pdf": dict(caption_position="below", table_title_position="below",
                                page_number="footer-center"),
        "pagenum_header.pdf": dict(caption_position="below", table_title_position="above",
                                   page_number="header-right"),
    }
    for name, kw in cases.items():
        (HERE / name).write_bytes(synthetic_pdf(**kw))
        print("wrote", name)


if __name__ == "__main__":
    main()
