"""OCR adapter + parser wiring tests.

These run with NO tesseract installed: the engine-absent path is asserted
directly, and the engine-present path is exercised by monkeypatching the OCR
functions, so coverage holds in CI without any OCR system dependency.
"""
from __future__ import annotations

from app import ocr
from app.parsers import parse_image, parse_pdf

# --- engine-absent: graceful degrade, posture unchanged --------------------

def test_ocr_unavailable_by_default(monkeypatch):
    # Default: OCR disabled -> unavailable, functions return empty, never raise.
    monkeypatch.setattr(ocr.settings, "OCR_ENABLED", False, raising=False)
    assert ocr.ocr_available() is False
    assert ocr.ocr_image_bytes(b"x") == ""
    assert ocr.ocr_pdf_bytes(b"x") == []
    st = ocr.ocr_status()
    assert st["enabled"] is False and st["available"] is False


def test_image_parse_unchanged_when_ocr_off(monkeypatch):
    monkeypatch.setattr(ocr.settings, "OCR_ENABLED", False, raising=False)
    r = parse_image(b"notanimage", "d1", "image/png")
    assert r.method == "native"
    assert len(r.artifacts) == 1          # still an artifact
    assert r.blocks == []                 # no text blocks without OCR


# --- engine-present (monkeypatched): wiring produces text ------------------

def test_image_parse_uses_ocr_when_available(monkeypatch):
    # Pretend an engine is available and returns text.
    monkeypatch.setattr("app.parsers.ocr_available", lambda: True)
    monkeypatch.setattr("app.parsers.ocr_image_bytes",
                        lambda data: "Line one\nLine two")
    r = parse_image(b"fakeimagebytes", "d1", "image/png")
    assert r.method == "ocr_image"
    assert [b.text for b in r.blocks] == ["Line one", "Line two"]
    assert len(r.artifacts) == 1          # artifact still kept


def test_pdf_scanned_recovered_via_ocr(monkeypatch):
    # Force the "no native text" branch: patch pypdf to yield an empty page, and
    # a fake OCR engine to recover text.
    class _FakePage:
        def extract_text(self):
            return ""

    class _FakeReader:
        def __init__(self, *a, **k):
            self.pages = [_FakePage()]

    monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
    monkeypatch.setattr("app.parsers.ocr_available", lambda: True)
    monkeypatch.setattr("app.parsers.ocr_pdf_bytes",
                        lambda data: ["Recovered page text\nSecond paragraph"])
    r = parse_pdf(b"%PDF-1.4 scanned", "d1")
    assert r.method == "ocr_pdf"
    assert any("Recovered page text" in b.text for b in r.blocks)


def test_pdf_scanned_still_needs_ocr_when_engine_absent(monkeypatch):
    class _FakePage:
        def extract_text(self):
            return ""

    class _FakeReader:
        def __init__(self, *a, **k):
            self.pages = [_FakePage()]

    monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
    monkeypatch.setattr("app.parsers.ocr_available", lambda: False)
    r = parse_pdf(b"%PDF-1.4 scanned", "d1")
    assert r.method == "needs_ocr"        # unchanged graceful behavior


def test_from_document_project_uses_ocr_text(monkeypatch):
    # The project from-document path shares the parsers, so OCR'd text flows
    # into the corpus. Prove an image upload yields a usable corpus when OCR is on.
    monkeypatch.setattr("app.parsers.ocr_available", lambda: True)
    monkeypatch.setattr(
        "app.parsers.ocr_image_bytes",
        lambda data: "# Incident\nSite: North Plant\nSeverity: High")
    from app.projectgen.corpus_intake import corpus_from_upload
    corpus = corpus_from_upload("scan.png", b"fakeimagebytes")
    assert corpus and "North Plant" in corpus[0].text
