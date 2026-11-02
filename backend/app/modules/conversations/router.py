"""Conversational Q&A with citations."""

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.core.errors import NotFound
from app.db.models import Chunk, Citation, Conversation, Document, DocumentVersion, Message
from app.knowledge.synthesis import compare_sources
from app.modules.documents import service as docs
from app.modules.workspaces.service import require_kb
from app.retrieval.qa import answer_question

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    kb_ids: list[uuid.UUID] | None = Field(default=None, max_length=50, description="Default: all readable")
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    scope: dict[str, Any]
    created_at: datetime


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def _get(db: DB, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    c = db.get(Conversation, conversation_id)
    if c is None or c.user_id != user_id:
        raise NotFound("Conversation not found")
    return c


@router.post("", response_model=ConversationOut, status_code=201)
def create(body: ConversationIn, db: DB, user: Viewer) -> ConversationOut:
    for d in body.document_ids or []:
        docs.get_document(db, user.id, d)  # 404 if not readable
    for kb in body.kb_ids or []:
        require_kb(db, user, kb)
    c = Conversation(
        user_id=user.id,
        title=body.title or "New conversation",
        scope={
            "document_ids": [str(d) for d in body.document_ids or []],
            "kb_ids": [str(k) for k in body.kb_ids or []],
        },
    )
    db.add(c)
    db.commit()
    return ConversationOut(id=c.id, title=c.title, scope=c.scope, created_at=c.created_at)


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: DB, user: Viewer) -> list[ConversationOut]:
    rows = db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.created_at.desc()).limit(100)
    )
    return [ConversationOut(id=c.id, title=c.title, scope=c.scope, created_at=c.created_at) for c in rows]


@router.delete("/{conversation_id}", status_code=204)
def delete(conversation_id: uuid.UUID, db: DB, user: Viewer) -> None:
    db.delete(_get(db, user.id, conversation_id))
    db.commit()


@router.post("/{conversation_id}/messages")
def ask(conversation_id: uuid.UUID, body: AskIn, db: DB, user: Viewer) -> dict[str, Any]:
    """Ask a question. Answers come only from documents the caller can read, with citations.
    ``status`` is ``answered``, ``not_found``, ``refused`` or ``error``."""
    c = _get(db, user.id, conversation_id)
    scope = [uuid.UUID(d) for d in c.scope.get("document_ids") or []] or None
    kb_scope = [uuid.UUID(k) for k in c.scope.get("kb_ids") or []] or None
    r = answer_question(db, user.id, c, body.question, document_ids=scope, kb_ids=kb_scope)
    out = asdict(r)
    out["citations"] = [{**asdict(x), "document_id": str(x.document_id)} for x in r.citations]
    out["synthesis"] = compare_sources(r.standalone_question, out["citations"])
    return out


@router.get("/{conversation_id}/messages")
def history(conversation_id: uuid.UUID, db: DB, user: Viewer) -> list[dict[str, Any]]:
    """Conversation history. Citations are re-checked against current access: a citation to a
    document that is no longer shared with the caller is hidden."""
    c = _get(db, user.id, conversation_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at))
    out = []
    for m in msgs:
        cites = []
        for cit, chunk, doc in db.execute(
            select(Citation, Chunk, Document)
            .join(Chunk, Chunk.id == Citation.chunk_id)
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Citation.message_id == m.id)
            .order_by(Citation.ordinal)
        ):
            if docs.access_level(db, user.id, doc.id) is None:
                continue
            cites.append(
                {
                    "ordinal": cit.ordinal,
                    "document_id": str(doc.id),
                    "document_title": doc.title,
                    "page_no": chunk.page_no,
                    "cited_text": cit.cited_text,
                }
            )
        out.append(
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "status": m.answer_status,
                "provider": m.provider,
                "created_at": m.created_at.isoformat(),
                "citations": cites,
            }
        )
    return out
