"""Page-aware PDF text extraction for research reports.

A PDF is the most hostile input this platform accepts: it arrives from outside,
it is a container format that can embed JavaScript and external references, and
it is large enough to exhaust memory before anything validates it. So the limits
come first and the parsing second — signature, size, page count and encryption
are all checked before a single byte is interpreted.

**Nothing embedded is ever executed or fetched.** Only the text layer is read.

Every block keeps its page number and bounding box, because a factor extracted
from a report has to be traceable back to the sentence it came from. "The report
says X" is not reviewable; "page 7, this paragraph" is.

Each page also reports ``text_density`` and layout flags. Those are not
diagnostics: Task 23 uses density to decide whether a page needs OCR, and Task 22
uses the flags to decide whether an extracted formula needs human confirmation. A
two-column page with heavy sub/superscripts is where formula extraction quietly
goes wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pymupdf  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError

#: Prototype ceilings. Generous enough for a sell-side report, small enough that a
#: malicious upload cannot exhaust the host before validation runs.
MAX_FILE_BYTES: Final = 50 * 1024 * 1024
MAX_PAGES: Final = 200

#: Characters per 1000 square points below which a page carries no usable text.
#: Measured, not guessed: a sparse but genuine text page runs about 0.05, and a
#: page with no text layer at all is exactly 0.
SCAN_DENSITY_THRESHOLD: Final = 0.02

#: A page with an image and almost no text is a scan with a caption. Density
#: alone cannot tell that apart from a legitimately sparse text page, so the
#: image is required as corroboration rather than inferred from the number.
SPARSE_WITH_IMAGE_THRESHOLD: Final = 0.10

_PDF_MAGIC: Final = b"%PDF-"


class ReportLimitError(DomainError):
    """Raised when a document exceeds a safety limit or cannot be read safely."""


class TextBlock(BaseModel):
    """One extracted block, with the evidence needed to cite it."""

    text: str
    bbox: tuple[float, float, float, float]


class ParsedPage(BaseModel):
    page_number: int
    text: str
    blocks: list[TextBlock] = Field(default_factory=list)
    width: float = 0.0
    height: float = 0.0

    #: Characters per 1000 square points. Drives the OCR decision in Task 23.
    text_density: float = 0.0
    #: ``multi_column`` / ``has_table`` / ``has_image`` / ``dense_scripts``.
    layout_flags: list[str] = Field(default_factory=list)

    #: ``text`` when read from the PDF's text layer, ``ocr`` when recognised.
    #: Surfaced to the user: a recognised line is a guess, and the reader has to
    #: know which kind of evidence they are looking at.
    source: str = "text"
    #: Mean OCR confidence; 1.0 for directly extracted text.
    confidence: float = 1.0

    @property
    def looks_scanned(self) -> bool:
        """Whether this page needs OCR to yield its content.

        Two cases, kept separate on purpose. A page with no text layer is
        unambiguous. A page with a little text *and* an image is a scan with a
        caption — and density alone cannot distinguish that from a genuinely
        sparse text page, which is why the image has to corroborate it. Guessing
        wrong in the permissive direction costs a slow OCR pass; guessing wrong
        in the other direction silently drops the page's content.
        """
        if self.text_density < SCAN_DENSITY_THRESHOLD:
            return True
        return (
            self.text_density < SPARSE_WITH_IMAGE_THRESHOLD
            and "has_image" in self.layout_flags
        )


class ParsedReport(BaseModel):
    filename: str
    page_count: int
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def scanned_pages(self) -> list[int]:
        return [page.page_number for page in self.pages if page.looks_scanned]


class PdfParser:
    """Extracts page-numbered text under strict limits."""

    def __init__(
        self, *, max_bytes: int = MAX_FILE_BYTES, max_pages: int = MAX_PAGES
    ) -> None:
        self._max_bytes = max_bytes
        self._max_pages = max_pages

    def extract(self, path: Path | str, *, ocr_engine: Any | None = None) -> ParsedReport:
        """Parse ``path``; run ``ocr_engine`` only on pages with no text layer.

        OCR is opt-in per call rather than automatic. It is orders of magnitude
        slower, and a page that extracted cleanly must not have its exact text
        replaced by a recognition guess.
        """
        source = Path(path)
        self._check_file(source)

        with pymupdf.open(source) as document:
            if document.needs_pass:
                raise ReportLimitError(
                    "encrypted PDF: no password supplied, and guessing one is not "
                    "something this platform does"
                )
            if document.page_count > self._max_pages:
                raise ReportLimitError(
                    f"page_count {document.page_count} exceeds the {self._max_pages}-page limit"
                )

            pages = [
                self._parse_page(document[index], index + 1)
                for index in range(document.page_count)
            ]
            if ocr_engine is not None:
                pages = _apply_ocr(document, pages, ocr_engine)
            metadata = _safe_metadata(document.metadata or {})
            page_count = document.page_count

        return ParsedReport(
            filename=source.name,
            page_count=page_count,
            pages=pages,
            metadata=metadata,
        )

    # ------------------------------------------------------------------ limits

    def _check_file(self, source: Path) -> None:
        if not source.exists():
            raise ReportLimitError(f"file not found: {source}")

        size = source.stat().st_size
        if size > self._max_bytes:
            raise ReportLimitError(
                f"file_size {size} exceeds the {self._max_bytes}-byte limit"
            )
        if size == 0:
            raise ReportLimitError("file_size is zero")

        # Check the signature rather than the extension: the extension is chosen
        # by whoever uploaded the file.
        with source.open("rb") as handle:
            if handle.read(len(_PDF_MAGIC)) != _PDF_MAGIC:
                raise ReportLimitError(
                    "not a PDF: the file does not start with the %PDF- signature"
                )

    # ------------------------------------------------------------------ pages

    @staticmethod
    def _parse_page(page: Any, page_number: int) -> ParsedPage:
        raw_blocks = page.get_text("blocks")
        blocks: list[TextBlock] = []
        x_starts: list[float] = []

        for entry in raw_blocks:
            x0, y0, x1, y1, text = entry[0], entry[1], entry[2], entry[3], entry[4]
            normalized = " ".join(str(text).split())
            if not normalized:
                continue
            blocks.append(
                TextBlock(text=normalized, bbox=(float(x0), float(y0), float(x1), float(y1)))
            )
            x_starts.append(float(x0))

        width = float(page.rect.width)
        height = float(page.rect.height)
        text = "\n".join(block.text for block in blocks)
        area = (width * height) / 1000 or 1.0

        return ParsedPage(
            page_number=page_number,
            text=text,
            blocks=blocks,
            width=width,
            height=height,
            text_density=len(text) / area,
            layout_flags=_layout_flags(page, x_starts, width),
        )


def _layout_flags(page: Any, x_starts: list[float], width: float) -> list[str]:
    """Describe the page shapes that make extraction unreliable.

    Consumed by later tasks rather than displayed: a multi-column page reads out
    of order, and a formula pulled from interleaved columns is wrong in a way
    that still parses.
    """
    flags: list[str] = []

    # Two distinct left margins in the right half of the page means two columns.
    if width and x_starts:
        left = [x for x in x_starts if x < width / 2]
        right = [x for x in x_starts if x >= width / 2]
        if len(left) >= 3 and len(right) >= 3:
            flags.append("multi_column")

    try:
        if page.get_images(full=False):
            flags.append("has_image")
    except Exception:  # noqa: BLE001 - an unreadable image list is not fatal
        pass

    try:
        if page.find_tables().tables:
            flags.append("has_table")
    except Exception:  # noqa: BLE001 - table detection is best-effort
        pass

    return flags


def _apply_ocr(document: Any, pages: list[ParsedPage], engine: Any) -> list[ParsedPage]:
    """Replace scanned pages' text with OCR output, capped per report."""
    from factor_platform.reports.ocr import MAX_OCR_PAGES, render_page

    targets = [page.page_number for page in pages if page.looks_scanned][:MAX_OCR_PAGES]
    if not targets:
        return pages

    by_number = {page.page_number: page for page in pages}
    for number in targets:
        recognised = engine.extract_page(
            render_page(document[number - 1]), number
        )
        page = by_number[number]
        by_number[number] = page.model_copy(
            update={
                "text": recognised.text,
                "source": "ocr",
                "confidence": recognised.mean_confidence,
                "blocks": [
                    TextBlock(text=line.text, bbox=line.bbox)
                    for line in recognised.lines
                    if line.text
                ],
            }
        )
    return [by_number[page.page_number] for page in pages]


def _safe_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep descriptive metadata only.

    Document metadata is attacker-controlled text. Only a known set of fields is
    carried forward, so nothing unexpected reaches a prompt or a log.
    """
    allowed = ("title", "author", "subject", "creationDate", "modDate", "producer")
    return {key: str(raw[key])[:500] for key in allowed if raw.get(key)}


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_PAGES",
    "SCAN_DENSITY_THRESHOLD",
    "SPARSE_WITH_IMAGE_THRESHOLD",
    "ParsedPage",
    "ParsedReport",
    "PdfParser",
    "ReportLimitError",
    "TextBlock",
]
