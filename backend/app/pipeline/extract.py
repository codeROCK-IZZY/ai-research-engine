"""Text and table extraction per page, with OCR for pages that have no text layer.

* PDF  — pdfplumber per page; tables via ``extract_tables``. A page with < 25 characters
         of text is treated as scanned: it is rendered at 200 dpi and OCR'd.
* DOCX — paragraphs and tables in document order; explicit page breaks split pages
         (Word files have no fixed pagination, so "page" means the section between breaks).
* TXT  — form feeds split pages (Markdown is plain text).
* HTML — readable text with headings; scripts, navigation and footers removed.
* Images — OCR (multi-page TIFF supported).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.errors import UnprocessableData
from app.pipeline.validate import IMAGE_MIMES, MIME_DOCX, MIME_HTML, MIME_PDF, MIME_TXT

logger = logging.getLogger(__name__)
SCANNED_PAGE_CHARS = 25


@dataclass
class PageContent:
    page_no: int
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    ocr_used: bool = False
    ocr_confidence: float | None = None


def ocr_image(image: object) -> tuple[str, float | None]:
    """OCR a PIL image. Returns (text, mean word confidence 0-100 or None)."""
    import pytesseract

    langs = get_settings().ocr_languages
    data = pytesseract.image_to_data(image, lang=langs, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"], strict=True) if str(w).strip() and float(c) >= 0]
    text = pytesseract.image_to_string(image, lang=langs)
    return text, (sum(confs) / len(confs) if confs else None)


def _table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[(c or "").replace("\n", " ").strip() for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


def extract_pdf(data: bytes) -> list[PageContent]:
    import pdfplumber

    max_pages = get_settings().max_pages
    pages: list[PageContent] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > max_pages:
            raise UnprocessableData(f"PDF has {len(pdf.pages)} pages; the limit is {max_pages}")
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = [[[c or "" for c in row] for row in t] for t in (page.extract_tables() or []) if t]
            if len(text.strip()) < SCANNED_PAGE_CHARS:
                image = page.to_image(resolution=200).original
                ocr_text, conf = ocr_image(image)
                pages.append(PageContent(i, ocr_text, [], True, conf))
                continue
            # Tables are kept structured and also appended as Markdown so they are searchable/citable.
            extra = "\n\n".join(_table_markdown(t) for t in tables)
            pages.append(PageContent(i, text + ("\n\n" + extra if extra else ""), tables))
    return pages


def extract_docx(data: bytes) -> list[PageContent]:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(data))
    pages: list[PageContent] = []
    buf: list[str] = []
    tables: list[list[list[str]]] = []

    def flush() -> None:
        pages.append(PageContent(len(pages) + 1, "\n".join(buf).strip(), list(tables)))
        buf.clear()
        tables.clear()

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            text = "".join(t.text or "" for t in child.iter(qn("w:t")))
            if text:
                buf.append(text)
            if any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br"))):
                flush()
        elif child.tag == qn("w:tbl"):
            rows = [
                ["".join(t.text or "" for t in cell.iter(qn("w:t"))) for cell in row.iter(qn("w:tc"))]
                for row in child.iter(qn("w:tr"))
            ]
            tables.append(rows)
            buf.append(_table_markdown(rows))
    if buf or tables or not pages:
        flush()
    return [p for p in pages if p.text] or [PageContent(1, "")]


def extract_txt(data: bytes) -> list[PageContent]:
    text = data.decode("utf-8", errors="replace")
    parts = text.split("\f")
    return [PageContent(i, part) for i, part in enumerate(parts, start=1) if part.strip()] or [PageContent(1, "")]


def extract_image(data: bytes) -> list[PageContent]:
    from PIL import Image, ImageSequence

    pages = []
    with Image.open(io.BytesIO(data)) as img:
        for i, frame in enumerate(ImageSequence.Iterator(img), start=1):
            if i > get_settings().max_pages:
                break
            text, conf = ocr_image(frame.convert("RGB"))
            pages.append(PageContent(i, text, [], True, conf))
    return pages


def extract(data: bytes, mime: str) -> list[PageContent]:
    if mime == MIME_PDF:
        return extract_pdf(data)
    if mime == MIME_DOCX:
        return extract_docx(data)
    if mime == MIME_TXT:
        return extract_txt(data)
    if mime == MIME_HTML:
        from app.pipeline.html import html_to_text

        text, _, _ = html_to_text(data.decode("utf-8", errors="replace"))
        return [PageContent(1, text)]
    if mime in IMAGE_MIMES:
        return extract_image(data)
    raise UnprocessableData(f"No extractor for {mime}")
