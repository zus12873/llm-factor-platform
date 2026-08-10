"""Bounded OCR for pages that have no text layer.

OCR runs only where extraction already failed. A page with a text layer is read
directly — faster, exact, and free of the recognition errors OCR introduces — so
routing everything through OCR would trade correctness for nothing.

Two limits and one refusal shape the rest:

* **200 DPI, at most 50 pages per report.** OCR is orders of magnitude slower
  than text extraction; without a ceiling one 200-page scan occupies a worker
  for an unbounded time and starves every other job.
* **Every line keeps its confidence and box.** A recognised line is a guess, and
  a guess presented without its confidence is indistinguishable from text.
* **A formula on an OCR page is never auto-accepted.** General-purpose OCR is
  unreliable on mathematical notation — subscripts merge, minus signs vanish —
  and a corrupted formula still parses. This release does not promise to read
  formulas off a scan, and the code enforces that rather than hoping.

The engine is injected so the routing logic can be tested without running a
neural model.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from pydantic import BaseModel, Field

from factor_platform.domain.errors import DomainError

#: Rendering resolution. Higher improves recognition and costs time quadratically.
OCR_DPI: Final = 200

#: Per-report ceiling. A 200-page scan would otherwise hold a worker indefinitely.
MAX_OCR_PAGES: Final = 50

#: Below this, a recognised line is flagged for human review rather than trusted.
MIN_LINE_CONFIDENCE: Final = 0.60


class OcrError(DomainError):
    """Raised when OCR cannot be performed within its limits."""


class OcrLine(BaseModel):
    """One recognised line, with the evidence to judge it."""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]

    @property
    def is_uncertain(self) -> bool:
        return self.confidence < MIN_LINE_CONFIDENCE


class OcrPage(BaseModel):
    page_number: int
    lines: list[OcrLine] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """Lines merged in reading order: top to bottom, then left to right."""
        ordered = sorted(self.lines, key=lambda line: (round(line.bbox[1]), line.bbox[0]))
        return "\n".join(line.text for line in ordered if line.text)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)

    @property
    def uncertain_lines(self) -> list[OcrLine]:
        return [line for line in self.lines if line.is_uncertain]


class OcrEngine(Protocol):
    """Recognises text in a rendered page image."""

    def extract_page(self, image: bytes, page_number: int) -> OcrPage: ...


class RapidOcrEngine:
    """RapidOCR adapter.

    Imported lazily. The model weights are large and loading them at import time
    would make every CLI command — including the ones that never touch a PDF —
    pay for a dependency they do not use.
    """

    def __init__(self) -> None:
        self._engine: Any | None = None

    def _ensure_engine(self) -> Any:
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise OcrError(
                    "rapidocr-onnxruntime is not installed; OCR is unavailable"
                ) from exc
            self._engine = RapidOCR()
        return self._engine

    def extract_page(self, image: bytes, page_number: int) -> OcrPage:
        engine = self._ensure_engine()
        result, _ = engine(decode_png(image))
        lines: list[OcrLine] = []
        for entry in result or []:
            box, text, confidence = entry[0], entry[1], entry[2]
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            lines.append(
                OcrLine(
                    text=str(text).strip(),
                    confidence=float(confidence),
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                )
            )
        return OcrPage(page_number=page_number, lines=lines)


def render_page(page: Any, dpi: int = OCR_DPI) -> bytes:
    """Render a PyMuPDF page to a PNG byte string."""
    import pymupdf

    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return bytes(pixmap.tobytes("png"))


def decode_png(image: bytes) -> Any:
    """Decode PNG bytes into an ``(h, w, 3)`` array.

    Recognisers take a decoded image, not a byte string — passing the raw bytes
    yields a one-dimensional array and a confusing dimensionality error several
    layers down. Decoding through PyMuPDF avoids adding an image library for one
    call, since it is already a dependency.
    """
    import numpy as np
    import pymupdf

    pixmap = pymupdf.Pixmap(image)
    if pixmap.alpha:  # recognisers expect RGB, not RGBA
        pixmap = pymupdf.Pixmap(pixmap, 0)
    array = np.frombuffer(pixmap.samples, dtype=np.uint8)
    return array.reshape(pixmap.height, pixmap.width, pixmap.n)


def pages_needing_ocr(report: Any, *, limit: int = MAX_OCR_PAGES) -> list[int]:
    """Page numbers to OCR, capped.

    Only pages that produced no usable text. Running OCR on a page that already
    extracted cleanly would replace exact text with a guess.
    """
    candidates = [page.page_number for page in report.pages if page.looks_scanned]
    return candidates[:limit]


def formula_is_trustworthy(source: str) -> bool:
    """Whether a formula read from this source may be auto-accepted.

    Always ``False`` for OCR. General OCR mangles mathematical notation in ways
    that still parse — a dropped minus sign inverts the factor — and this release
    does not claim to read formulas off a scan.
    """
    return source != "ocr"


__all__ = [
    "MAX_OCR_PAGES",
    "MIN_LINE_CONFIDENCE",
    "OCR_DPI",
    "OcrEngine",
    "OcrError",
    "OcrLine",
    "OcrPage",
    "RapidOcrEngine",
    "decode_png",
    "formula_is_trustworthy",
    "pages_needing_ocr",
    "render_page",
]
