"""Report upload and extraction endpoints.

Uploads are stored under a generated artifact id, never under the filename the
client supplied. A filename is attacker-controlled: it can traverse directories,
collide with another user's upload, or carry a name that ends up in a log or a
prompt. The original is kept as a display label and nothing else.

The response always carries the evidence, even when extraction failed. A user
whose upload could not be parsed still needs the passages the platform found —
otherwise a failed extraction means starting over by hand with nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from factor_platform.reports.extractor import ExtractedFactor, ReportExtractor
from factor_platform.reports.pdf import (
    MAX_FILE_BYTES,
    ParsedReport,
    PdfParser,
    ReportLimitError,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

#: Read in chunks so an oversized upload is refused before it is all in memory.
_CHUNK = 1024 * 1024


class UploadResponse(BaseModel):
    artifact_id: str
    display_name: str
    page_count: int
    scanned_pages: list[int]
    extraction: ExtractedFactor
    #: Stated in the response, not only in the docs: the caller has to know what
    #: this release can and cannot read.
    capability_note: str = (
        "首期支持正文可复制、变量定义明确的中英文文本型研报。"
        "图片公式、复杂数学排版与扫描版仅提取正文线索，公式需人工确认。"
    )


def get_extractor() -> ReportExtractor:  # pragma: no cover - overridden by the app
    raise NotImplementedError("extractor dependency is wired in main.create_app")


def get_upload_root() -> Path:  # pragma: no cover - overridden by the app
    raise NotImplementedError("upload root dependency is wired in main.create_app")


@router.post("/upload")
async def upload_report(
    extractor: Annotated[ReportExtractor, Depends(get_extractor)],
    upload_root: Annotated[Path, Depends(get_upload_root)],
    file: Annotated[UploadFile, File()],
) -> UploadResponse:
    """Store a PDF, parse it, and attempt extraction."""
    artifact_id = uuid.uuid4().hex
    upload_root.mkdir(parents=True, exist_ok=True)
    # Generated id, never the client's filename: that string can traverse
    # directories and ends up in logs.
    target = upload_root / f"{artifact_id}.pdf"

    written = 0
    with target.open("wb") as handle:
        while chunk := await file.read(_CHUNK):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise ReportLimitError(
                    f"file_size exceeds the {MAX_FILE_BYTES}-byte limit"
                )
            handle.write(chunk)

    parsed: ParsedReport = PdfParser().extract(target)
    extraction = await extractor.extract(parsed)

    return UploadResponse(
        artifact_id=artifact_id,
        display_name=Path(file.filename or "report.pdf").name,
        page_count=parsed.page_count,
        scanned_pages=parsed.scanned_pages,
        extraction=extraction,
    )


__all__ = ["UploadResponse", "get_extractor", "get_upload_root", "router"]
