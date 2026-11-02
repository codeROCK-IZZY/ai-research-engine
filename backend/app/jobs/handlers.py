"""Background job handlers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.runner import process_version


def handle_process_version(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return process_version(db, uuid.UUID(payload["version_id"]))


def handle_refresh_kb(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Re-fetch every web source in a knowledge base; unchanged pages are not re-processed."""
    from sqlalchemy import select

    from app.connectors.service import refresh_url
    from app.core.errors import AppError
    from app.db.models import Document

    outcomes: dict[str, int] = {"unchanged": 0, "updated": 0, "failed": 0}
    user_id = uuid.UUID(payload["user_id"])
    for doc in db.scalars(
        select(Document).where(Document.kb_id == uuid.UUID(payload["kb_id"]), Document.source_kind == "url")
    ):
        try:
            with db.begin_nested():
                outcomes[refresh_url(db, user_id, doc)] += 1
        except AppError:
            outcomes["failed"] += 1
    return outcomes


HANDLERS = {"process_version": handle_process_version, "refresh_kb": handle_refresh_kb}
