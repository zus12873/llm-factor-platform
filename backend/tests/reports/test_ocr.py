"""Tests for bounded OCR.

The engine is faked. What is under test is the routing and the limits — which
pages get OCR'd, how many, and what the platform refuses to trust afterwards.
Running a real neural model here would test onnxruntime, not this code, and would
make the suite too slow to run on every change.

The rule that matters most: a page that already has text must never be OCR'd.
Replacing exact text with a recognition guess is a silent downgrade, and the
result still looks like text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.reports.ocr import (
    MAX_OCR_PAGES,
    MIN_LINE_CONFIDENCE,
    OCR_DPI,
    OcrLine,
    OcrPage,
    formula_is_trustworthy,
    pages_needing_ocr,
)
from factor_platform.reports.pdf import ParsedPage, ParsedReport, PdfParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reports"


class FakeOcr:
    """Records which pages it was asked to read."""

    def __init__(self, lines: list[OcrLine] | None = None) -> None:
        self.calls: list[int] = []
        self._lines = lines or [
            OcrLine(text="因子定义：对 ROE_TTM 做横截面排名", confidence=0.95,
                    bbox=(10.0, 40.0, 300.0, 60.0)),
            OcrLine(text="扫描版研报", confidence=0.91, bbox=(10.0, 10.0, 200.0, 30.0)),
        ]

    def extract_page(self, image: bytes, page_number: int) -> OcrPage:
        del image
        self.calls.append(page_number)
        return OcrPage(page_number=page_number, lines=self._lines)


# --------------------------------------------------------------------------- routing


def test_a_scanned_page_is_ocred_and_keeps_its_page_number() -> None:
    engine = FakeOcr()
    report = PdfParser().extract(FIXTURES / "scanned_bilingual.pdf", ocr_engine=engine)

    assert engine.calls == [1]
    assert report.pages[0].page_number == 1
    assert report.pages[0].source == "ocr"
    assert "ROE_TTM" in report.pages[0].text


def test_a_text_page_is_never_ocred() -> None:
    """Replacing exact text with a guess is a downgrade that still looks like text."""
    engine = FakeOcr()
    report = PdfParser().extract(FIXTURES / "text_zh.pdf", ocr_engine=engine)

    assert engine.calls == []
    assert all(page.source == "text" for page in report.pages)


def test_ocr_does_not_run_unless_asked() -> None:
    """OCR is opt-in per call; it is far too slow to be the default path."""
    report = PdfParser().extract(FIXTURES / "scanned_bilingual.pdf")
    assert report.pages[0].source == "text"
    assert report.pages[0].text == ""


def test_the_number_of_ocr_pages_is_capped() -> None:
    """One 200-page scan must not hold a worker indefinitely."""
    many = ParsedReport(
        filename="scan.pdf",
        page_count=120,
        pages=[
            ParsedPage(page_number=n, text="", width=595, height=842, text_density=0.0)
            for n in range(1, 121)
        ],
    )
    assert len(pages_needing_ocr(many)) == MAX_OCR_PAGES


def test_only_scanned_pages_are_candidates() -> None:
    mixed = ParsedReport(
        filename="mixed.pdf",
        page_count=2,
        pages=[
            ParsedPage(page_number=1, text="有文本", width=595, height=842, text_density=2.0),
            ParsedPage(page_number=2, text="", width=595, height=842, text_density=0.0),
        ],
    )
    assert pages_needing_ocr(mixed) == [2]


# --------------------------------------------------------------------------- evidence


def test_lines_merge_in_reading_order_not_recognition_order() -> None:
    """OCR returns lines in whatever order it found them."""
    page = OcrPage(
        page_number=1,
        lines=[
            OcrLine(text="第二行", confidence=0.9, bbox=(10.0, 100.0, 200.0, 120.0)),
            OcrLine(text="第一行", confidence=0.9, bbox=(10.0, 10.0, 200.0, 30.0)),
        ],
    )
    assert page.text == "第一行\n第二行"


def test_a_low_confidence_line_is_flagged() -> None:
    """A recognised line is a guess; presenting it bare makes it look like text."""
    line = OcrLine(text="ROE", confidence=MIN_LINE_CONFIDENCE - 0.1, bbox=(0, 0, 1, 1))
    assert line.is_uncertain

    page = OcrPage(page_number=1, lines=[line])
    assert page.uncertain_lines == [line]


def test_page_confidence_is_recorded_on_the_parsed_page() -> None:
    engine = FakeOcr()
    report = PdfParser().extract(FIXTURES / "scanned_bilingual.pdf", ocr_engine=engine)
    assert 0.9 <= report.pages[0].confidence <= 1.0


def test_ocr_blocks_keep_their_boxes_so_evidence_still_cites() -> None:
    engine = FakeOcr()
    report = PdfParser().extract(FIXTURES / "scanned_bilingual.pdf", ocr_engine=engine)
    assert report.pages[0].blocks
    assert all(len(block.bbox) == 4 for block in report.pages[0].blocks)


# --------------------------------------------------------------------------- refusal


def test_a_formula_from_an_ocr_page_is_never_auto_accepted() -> None:
    """A dropped minus sign inverts the factor, and the result still parses."""
    assert formula_is_trustworthy("text") is True
    assert formula_is_trustworthy("ocr") is False


def test_the_limits_are_declared_not_implicit() -> None:
    assert OCR_DPI == 200
    assert MAX_OCR_PAGES == 50
    assert pytest.approx(0.60) == MIN_LINE_CONFIDENCE


# --------------------------------------------------------------------------- decoding


def test_rendered_bytes_decode_to_a_two_dimensional_image() -> None:
    """Regression: passing raw PNG bytes to a recogniser yields a 1-D array.

    Only a real-engine smoke run surfaced this — a fake engine never touches the
    image at all — and the failure appeared several layers down as an opaque
    dimensionality error.
    """
    import pymupdf

    from factor_platform.reports.ocr import decode_png, render_page

    document = pymupdf.open(FIXTURES / "text_zh.pdf")
    try:
        array = decode_png(render_page(document[0], dpi=72))
    finally:
        document.close()

    assert array.ndim == 3
    assert array.shape[2] in (1, 3)
    assert array.shape[0] > 0 and array.shape[1] > 0
