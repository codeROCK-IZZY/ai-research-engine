"""Upload validation: identify the real file type from its bytes, enforce limits.

The client-supplied filename and Content-Type are never trusted for routing; they
are only used for display. A ``.pdf`` that is really an executable is rejected.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import UnprocessableData

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_TXT = "text/plain"
MIME_HTML = "text/html"
MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"
MIME_TIFF = "image/tiff"
IMAGE_MIMES = (MIME_PNG, MIME_JPEG, MIME_TIFF)
SUPPORTED = (MIME_PDF, MIME_DOCX, MIME_TXT, MIME_HTML, *IMAGE_MIMES)


@dataclass(frozen=True)
class Sniffed:
    mime: str
    extension: str


def sniff(data: bytes) -> Sniffed:
    """Detect the type from magic bytes. Raises UnprocessableData for anything unsupported."""
    head = data[:8]
    if head.startswith(b"%PDF-"):
        return Sniffed(MIME_PDF, "pdf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed(MIME_PNG, "png")
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed(MIME_JPEG, "jpg")
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return Sniffed(MIME_TIFF, "tiff")
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return Sniffed(MIME_DOCX, "docx")
        except zipfile.BadZipFile as exc:
            raise UnprocessableData("Corrupt ZIP/DOCX file") from exc
        raise UnprocessableData("ZIP archives other than Word documents are not supported")
    if b"\x00" not in data[:8192]:
        try:
            data.decode("utf-8")
            from app.pipeline.html import looks_like_html

            if looks_like_html(data):
                return Sniffed(MIME_HTML, "html")
            return Sniffed(MIME_TXT, "txt")
        except UnicodeDecodeError:
            pass
    raise UnprocessableData("Unsupported file type. Upload PDF, DOCX, TXT/Markdown, HTML, PNG, JPEG or TIFF.")


def check_limits(data: bytes, sniffed: Sniffed) -> None:
    s = get_settings()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise UnprocessableData(f"File exceeds the {s.max_upload_mb} MB limit")
    if not data:
        raise UnprocessableData("File is empty")
    if sniffed.mime == MIME_DOCX:
        # Zip-bomb guard: a tiny DOCX can declare gigabytes of uncompressed XML.
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
        if total > s.max_docx_uncompressed_mb * 1024 * 1024:
            raise UnprocessableData("DOCX expands to more than the allowed size")


def validate_upload(data: bytes) -> Sniffed:
    sniffed = sniff(data)
    check_limits(data, sniffed)
    return sniffed
