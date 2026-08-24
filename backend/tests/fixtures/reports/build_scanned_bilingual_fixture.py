"""Build the project-owned image-only OCR acceptance fixture."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "scanned_bilingual.pdf"
FONT = Path(r"C:\Windows\Fonts\msyh.ttc")


def build() -> None:
    width, height = 1654, 2339
    image = Image.new("L", (width, height), 246)
    draw = ImageDraw.Draw(image)
    regular = ImageFont.truetype(str(FONT), 44)
    title = ImageFont.truetype(str(FONT), 64)
    small = ImageFont.truetype(str(FONT), 34)

    draw.rectangle((100, 100, width - 100, height - 100), outline=95, width=3)
    draw.text((150, 170), "Synthetic Scanned Factor Note", font=title, fill=20)
    draw.text((150, 285), "项目自有合成扫描因子说明", font=regular, fill=35)
    draw.line((150, 365, width - 150, 365), fill=110, width=2)

    lines = [
        "Factor 1: ROE TTM cross-sectional rank",
        "公式：rank(ROE_TTM)，仅在公告日后使用财务数据。",
        "Factor 2: 20-day momentum factor",
        "Formula: rank(rolling_return(close, 20))",
        "Factor 3: Revenue growth factor",
        "变量：营业收入同比增长率 REVENUE_YOY。",
    ]
    y = 485
    for line in lines:
        draw.text((165, y), line, font=regular, fill=25)
        y += 170

    draw.rectangle((150, 1580, width - 150, 1970), outline=125, width=2)
    draw.text((190, 1640), "OCR acceptance rules", font=regular, fill=30)
    draw.text((190, 1735), "- page evidence must remain page 1", font=small, fill=45)
    draw.text((190, 1810), "- confidence must be recorded", font=small, fill=45)
    draw.text((190, 1885), "- OCR formula requires human confirmation", font=small, fill=45)

    image = ImageEnhance.Contrast(image).enhance(0.92)
    png = io.BytesIO()
    image.save(png, format="PNG", optimize=True)
    png.seek(0)

    page_width, page_height = A4
    canvas = Canvas(str(OUTPUT), pagesize=A4, pageCompression=1)
    canvas.drawImage(
        ImageReader(png),
        0,
        0,
        width=page_width,
        height=page_height,
        preserveAspectRatio=False,
    )
    canvas.showPage()
    canvas.save()

    reader = PdfReader(str(OUTPUT))
    extracted = "".join(page.extract_text() or "" for page in reader.pages)
    if extracted.strip():
        raise RuntimeError("fixture unexpectedly contains a PDF text layer")


if __name__ == "__main__":
    build()
