"""Ingestion connectors: web pages and ZIP archives, plus refreshing web sources.

Change detection: every version stores the SHA-256 of its bytes. Refreshing a web source
re-fetches it; identical bytes only update ``fetched_at`` (nothing is re-processed),
different bytes create a new version that goes through the normal pipeline.
"""

from __future__ import annotations

import io
import posixpath
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.web import fetch
from app.core.config import get_settings
from app.core.errors import Conflict, UnprocessableData
from app.db.models import Document
from app.modules.documents import service as docs
from app.pipeline.html import html_to_text

ARCHIVE_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm")


def _page_meta(content: bytes, content_type: str) -> tuple[str | None, datetime | None]:
    if content_type in ("text/html", "application/xhtml+xml"):
        _, title, published = html_to_text(content.decode("utf-8", errors="replace"))
        return title, published
    return None, None


def add_url(db: Session, user_id: uuid.UUID, kb_id: uuid.UUID, url: str, title: str | None) -> Document:
    existing = db.scalar(select(Document).where(Document.kb_id == kb_id, Document.source_url == url))
    if existing is not None:
        raise Conflict("This URL is already in the knowledge base; refresh it instead")
    got = fetch(url)
    page_title, published = _page_meta(got.content, got.content_type)
    now = datetime.now(UTC)
    filename = posixpath.basename(got.url.split("?")[0]) or "page.html"
    doc, _, _ = docs.create_document(
        db,
        user_id,
        kb_id,
        got.content,
        filename,
        title or page_title or got.url,
        source_kind="url",
        source_url=url,
        published_at=published or got.last_modified,
        fetched_at=now,
    )
    return doc


def refresh_url(db: Session, user_id: uuid.UUID, doc: Document) -> str:
    """Returns ``unchanged`` or ``updated``."""
    if doc.source_kind != "url" or not doc.source_url:
        raise UnprocessableData("Only web sources can be refreshed")
    got = fetch(doc.source_url)
    import hashlib

    doc.fetched_at = datetime.now(UTC)
    if hashlib.sha256(got.content).hexdigest() == docs.current_sha(db, doc):
        return "unchanged"
    _, published = _page_meta(got.content, got.content_type)
    if published:
        doc.published_at = published
    docs.add_version(db, doc, got.content, posixpath.basename(doc.source_url) or "page.html", user_id)
    return "updated"


@dataclass
class ArchiveReport:
    added: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def add_archive(db: Session, user_id: uuid.UUID, kb_id: uuid.UUID, data: bytes) -> ArchiveReport:
    """Add every supported file in a ZIP. Limits guard against zip bombs; nothing is written to
    disk under archive-controlled names (so "zip slip" path traversal cannot apply)."""
    s = get_settings()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnprocessableData("Not a valid ZIP archive") from exc
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > s.zip_max_entries:
        raise UnprocessableData(f"The archive has more than {s.zip_max_entries} files")
    if sum(i.file_size for i in infos) > s.zip_max_uncompressed_mb * 1024 * 1024:
        raise UnprocessableData(f"The archive expands to more than {s.zip_max_uncompressed_mb} MB")
    report = ArchiveReport()
    for info in infos:
        name = info.filename.replace("\\", "/")
        base = posixpath.basename(name)
        if base.startswith(".") or "__MACOSX" in name:
            continue
        if not base.lower().endswith(ARCHIVE_EXTENSIONS):
            report.skipped.append({"file": name, "reason": "unsupported type"})
            continue
        content = z.read(info)
        try:
            title = base.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
            if base.lower().endswith((".html", ".htm")):
                title = _page_meta(content, "text/html")[0] or title
            docs.create_document(db, user_id, kb_id, content, base, title, source_kind="zip")
            report.added.append(name)
        except UnprocessableData as exc:
            report.skipped.append({"file": name, "reason": exc.detail})
    return report
