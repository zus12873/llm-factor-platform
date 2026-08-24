"""Tests for PDF extraction.

A PDF is the most hostile input this platform accepts, so most of these check a
refusal rather than a success. The limits exist to fail before anything is
interpreted — a 2 GB upload must be rejected on its size, not after PyMuPDF has
tried to open it.

The other half is evidence. A factor pulled from a report has to be traceable to
the page it came from, or a reviewer cannot check it: "the report says X" is not
reviewable, "page 7, this paragraph" is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factor_platform.reports.pdf import (
    MAX_PAGES,
    ParsedReport,
    PdfParser,
    ReportLimitError,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reports"


@pytest.fixture
def parser() -> PdfParser:
    return PdfParser()


# --------------------------------------------------------------------------- evidence


def test_a_chinese_report_keeps_its_page_numbers(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert report.page_count == 2
    assert report.pages[0].page_number == 1
    assert report.pages[1].page_number == 2
    assert "净资产收益率" in report.pages[0].text


def test_an_english_report_extracts_too(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_en.pdf")
    assert "return on equity" in report.pages[0].text.lower()


def test_every_block_carries_a_bounding_box(parser: PdfParser) -> None:
    """Without coordinates, a citation cannot point at anything."""
    report = parser.extract(FIXTURES / "text_zh.pdf")
    blocks = report.pages[0].blocks
    assert blocks
    for block in blocks:
        assert len(block.bbox) == 4
        assert block.bbox[2] > block.bbox[0]


def test_whitespace_is_normalized_without_losing_the_text(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert "  " not in report.pages[0].text
    assert "ROE_TTM" in report.pages[0].text


def test_page_geometry_is_recorded(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert report.pages[0].width > 0
    assert report.pages[0].height > 0


# --------------------------------------------------------------------------- scan hints


def test_a_text_page_reports_a_usable_density(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert report.pages[0].text_density > 0
    assert not report.pages[0].looks_scanned


def test_a_page_with_no_text_layer_is_flagged_as_scanned(parser: PdfParser) -> None:
    """This is what decides whether OCR runs; guessing it wastes a slow pass."""
    report = parser.extract(FIXTURES / "scanned.pdf")
    assert report.pages[0].text_density == 0.0
    assert report.pages[0].looks_scanned
    assert report.scanned_pages == [1]


def test_bilingual_ocr_fixture_is_image_only_and_visibly_nonblank() -> None:
    """Acceptance fixture must exercise OCR, not an invisible or text-layer PDF."""
    import pymupdf

    path = FIXTURES / "scanned_bilingual.pdf"
    document = pymupdf.open(path)
    try:
        assert not document[0].get_text().strip()
        pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(0.25, 0.25), alpha=False)
        values = memoryview(pixmap.samples)
        assert max(values) - min(values) > 50
    finally:
        document.close()


def test_a_sparse_but_genuine_text_page_is_not_called_a_scan(
    parser: PdfParser,
) -> None:
    """Page 2 of the fixture has one line. Sending it to OCR would be wasted work,
    and the threshold was originally set high enough to do exactly that."""
    report = parser.extract(FIXTURES / "text_zh.pdf")
    page_two = report.pages[1]
    assert page_two.text
    assert not page_two.looks_scanned


# --------------------------------------------------------------------------- refusals


def test_an_oversized_file_is_rejected_on_size_alone(
    parser: PdfParser, tmp_path: Path
) -> None:
    """Rejected before the parser opens it, which is the point of the limit."""
    small = PdfParser(max_bytes=1024)
    with pytest.raises(ReportLimitError, match="file_size"):
        small.extract(FIXTURES / "text_zh.pdf")
    del parser, tmp_path


def test_a_file_that_is_not_a_pdf_is_rejected(parser: PdfParser, tmp_path: Path) -> None:
    """The extension is chosen by whoever uploaded the file; the signature is not."""
    fake = tmp_path / "report.pdf"
    fake.write_bytes(b"MZ\x90\x00 this is an executable")
    with pytest.raises(ReportLimitError, match="%PDF-"):
        parser.extract(fake)


def test_an_empty_file_is_rejected(parser: PdfParser, tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ReportLimitError, match="zero"):
        parser.extract(empty)


def test_a_missing_file_is_rejected(parser: PdfParser, tmp_path: Path) -> None:
    with pytest.raises(ReportLimitError, match="not found"):
        parser.extract(tmp_path / "nope.pdf")


def test_too_many_pages_is_rejected(tmp_path: Path) -> None:
    tiny = PdfParser(max_pages=1)
    with pytest.raises(ReportLimitError, match="page_count"):
        tiny.extract(FIXTURES / "text_zh.pdf")
    del tmp_path


def test_the_page_limit_is_declared_not_implicit() -> None:
    assert MAX_PAGES == 200


# --------------------------------------------------------------------------- metadata


def test_only_known_metadata_fields_are_carried_forward(parser: PdfParser) -> None:
    """Document metadata is attacker-controlled text and may reach a prompt."""
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert set(report.metadata) <= {
        "title",
        "author",
        "subject",
        "creationDate",
        "modDate",
        "producer",
    }


def test_the_report_round_trips_through_json(parser: PdfParser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    restored = ParsedReport.model_validate_json(report.model_dump_json())
    assert restored.pages[0].text == report.pages[0].text
