"""Search across the knowledge bases the caller can read, plus personal search history."""

import time
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.db.models import SearchHistory
from app.modules.workspaces.service import require_kb
from app.retrieval.search import search

router = APIRouter(prefix="/search", tags=["search"])


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    kb_ids: list[uuid.UUID] | None = Field(default=None, max_length=50, description="Default: all readable")
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    doc_type: str | None = None
    mode: Literal["hybrid", "keyword", "vector"] = "hybrid"
    rerank: bool = True
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    document_title: str
    kb_id: uuid.UUID | None
    doc_type: str | None
    page_no: int
    char_start: int
    char_end: int
    text: str
    score: float
    vector_similarity: float | None
    keyword_rank: float | None
    rerank_score: float | None


class SearchOut(BaseModel):
    hits: list[SearchHit]
    mode: str
    reranked: bool
    latency_ms: int


@router.post("", response_model=SearchOut)
def run_search(body: SearchIn, db: DB, user: Viewer) -> SearchOut:
    """Hybrid keyword + semantic search, optionally re-ranked by a cross-encoder."""
    for kb in body.kb_ids or []:
        require_kb(db, user, kb)  # 404 for knowledge bases the caller cannot read
    started = time.perf_counter()
    hits = search(
        db,
        user.id,
        body.query,
        limit=body.limit,
        document_ids=body.document_ids,
        doc_type=body.doc_type,
        kb_ids=body.kb_ids,
        mode=body.mode,
        rerank=body.rerank,
    )
    latency = round((time.perf_counter() - started) * 1000)
    db.add(
        SearchHistory(
            user_id=user.id,
            query=body.query,
            kb_ids=[str(k) for k in body.kb_ids or []],
            mode=body.mode,
            reranked=body.rerank,
            results=len(hits),
            latency_ms=latency,
        )
    )
    db.commit()
    return SearchOut(
        hits=[SearchHit(**{k: getattr(h, k) for k in SearchHit.model_fields}) for h in hits],
        mode=body.mode,
        reranked=body.rerank,
        latency_ms=latency,
    )


class HistoryOut(BaseModel):
    query: str
    mode: str
    reranked: bool
    results: int
    latency_ms: int
    created_at: datetime


@router.get("/history", response_model=list[HistoryOut])
def search_history(db: DB, user: Viewer, limit: int = Query(default=30, ge=1, le=200)) -> list[SearchHistory]:
    return list(
        db.scalars(
            select(SearchHistory)
            .where(SearchHistory.user_id == user.id)
            .order_by(SearchHistory.created_at.desc())
            .limit(limit)
        )
    )
