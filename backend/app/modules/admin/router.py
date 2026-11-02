from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, Admin, Viewer
from app.core.config import get_settings
from app.db.models import Chunk, Document, DocumentVersion, Job, Message

router = APIRouter(tags=["admin"])


@router.get("/system/info")
def system_info(_: Viewer) -> dict[str, Any]:
    """What the UI needs to label AI output honestly (which provider/model is active)."""
    s = get_settings()
    return {
        "llm_mode": s.llm_mode,
        "llm_model": s.claude_model if s.llm_mode == "claude" else None,
        "embedding_model": s.embedding_model,
        "documents_leave_server": s.llm_mode == "claude",
    }


@router.get("/admin/stats")
def stats(db: DB, _: Admin) -> dict[str, Any]:
    by_status = dict(db.execute(select(DocumentVersion.status, func.count()).group_by(DocumentVersion.status)).all())
    by_type = dict(db.execute(select(Document.doc_type, func.count()).group_by(Document.doc_type)).all())
    answers = dict(
        db.execute(
            select(Message.answer_status, func.count())
            .where(Message.role == "assistant")
            .group_by(Message.answer_status)
        ).all()
    )
    return {
        "documents": db.scalar(select(func.count()).select_from(Document)) or 0,
        "chunks": db.scalar(select(func.count()).select_from(Chunk)) or 0,
        "versions_by_status": by_status,
        "documents_by_type": {str(k): v for k, v in by_type.items()},
        "answers_by_status": {str(k): v for k, v in answers.items()},
    }


@router.get("/admin/jobs")
def jobs(db: DB, _: Admin, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500)))
    return [
        {
            "id": j.id,
            "type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "payload": j.payload,
            "last_error": (j.last_error or "")[:500] or None,
            "created_at": j.created_at.isoformat(),
        }
        for j in rows
    ]
