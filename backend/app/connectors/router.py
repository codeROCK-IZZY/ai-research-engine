"""Add sources from the web or a ZIP archive; refresh web sources."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select

from app.api.deps import DB, Member
from app.connectors import service
from app.core.config import get_settings
from app.core.errors import UnprocessableData
from app.db.models import Document
from app.jobs import queue
from app.modules import audit
from app.modules.documents import service as docs
from app.modules.workspaces.service import require_kb

router = APIRouter(tags=["sources"])


class UrlIn(BaseModel):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=300)


@router.post("/knowledge-bases/{kb_id}/sources:url", status_code=202)
def add_url(kb_id: uuid.UUID, body: UrlIn, db: DB, user: Member) -> dict[str, Any]:
    """Fetch one web page (HTTPS, SSRF-protected) and add it as a source. Editors and owners."""
    require_kb(db, user, kb_id, "editor")
    doc = service.add_url(db, user.id, kb_id, str(body.url), body.title)
    audit.record(db, user.id, "source.add_url", "document", str(doc.id), url=str(body.url))
    db.commit()
    return {"document_id": str(doc.id), "title": doc.title, "status": "queued"}


@router.post("/knowledge-bases/{kb_id}/sources:zip", status_code=202)
async def add_zip(kb_id: uuid.UUID, db: DB, user: Member, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Add every PDF, DOCX, TXT, Markdown and HTML file in a ZIP archive."""
    require_kb(db, user, kb_id, "editor")
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise UnprocessableData(f"Archive exceeds the {get_settings().max_upload_mb} MB upload limit")
    report = service.add_archive(db, user.id, kb_id, data)
    audit.record(db, user.id, "source.add_zip", "knowledge_base", str(kb_id), added=len(report.added))
    db.commit()
    return {"added": report.added, "skipped": report.skipped}


@router.post("/documents/{document_id}/refresh")
def refresh(document_id: uuid.UUID, db: DB, user: Member) -> dict[str, str]:
    """Re-fetch a web source now. Unchanged content is not re-processed."""
    doc = docs.get_document(db, user.id, document_id, need="edit")
    outcome = service.refresh_url(db, user.id, doc)
    db.commit()
    return {"document_id": str(doc.id), "outcome": outcome}


@router.post("/knowledge-bases/{kb_id}/refresh", status_code=202)
def refresh_all(kb_id: uuid.UUID, db: DB, user: Member) -> dict[str, Any]:
    """Queue a background refresh of every web source in the knowledge base."""
    require_kb(db, user, kb_id, "editor")
    n = len(list(db.scalars(select(Document.id).where(Document.kb_id == kb_id, Document.source_kind == "url"))))
    job = queue.enqueue(db, "refresh_kb", {"kb_id": str(kb_id), "user_id": str(user.id)}, dedupe=True)
    db.commit()
    return {"job_id": job.id if job else None, "web_sources": n}
