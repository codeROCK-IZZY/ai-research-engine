"""Grounded question answering.

Flow: history → standalone question → hybrid retrieval (access-controlled) →
relevance gate → provider answer with citations → grounding enforcement → persist.

Grounding is enforced in code, not only in the prompt:
* **Relevance gate** — if nothing retrieved is relevant enough, reply "not found"
  without calling the language model at all.
* **No citation, no answer** — an "answered" result with zero citations becomes
  "not found". Individual uncited sentences are kept but marked ``supported=False``.
* **Graceful degradation** — if the Claude call fails (network, rate limit), the
  offline extractive provider answers instead, and the response says so.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.base import NOT_FOUND_TEXT, AnswerResult, Passage
from app.ai.extractive import ExtractiveProvider
from app.ai.providers import get_llm
from app.ai.textutil import identifiers
from app.core.config import get_settings
from app.db.models import Citation, Conversation, Message
from app.retrieval.search import Hit, documents_containing, search


@dataclass
class CitationOut:
    ordinal: int
    chunk_id: int
    document_id: uuid.UUID
    document_title: str
    page_no: int
    cited_text: str
    char_start: int  # offsets into the page text (for highlighting)
    char_end: int


@dataclass
class AskResult:
    message_id: uuid.UUID
    status: str
    answer: str
    provider: str
    sentences: list[dict[str, Any]]
    citations: list[CitationOut]
    standalone_question: str
    retrieved: int
    latency_ms: int
    notes: list[str] = field(default_factory=list)


def is_relevant(hits: list[Hit]) -> bool:
    """Relevance gate. With re-ranking, the cross-encoder's relevance of the best passage must
    reach ``RKE_MIN_RERANK_RELEVANCE`` (it reads question and passage together, so it handles
    paraphrases). Without re-ranking, fall back to vector similarity plus a keyword match."""
    if not hits:
        return False
    reranked = [h.rerank_score for h in hits if h.rerank_score is not None]
    if reranked:
        return max(reranked) >= get_settings().min_rerank_relevance
    best_sim = max((h.vector_similarity or 0.0) for h in hits)
    has_kw = any(h.keyword_rank for h in hits[:5])
    return best_sim >= get_settings().min_relevance and (has_kw or best_sim >= get_settings().min_relevance + 0.15)


def _locate(hit: Hit, cited: str) -> tuple[int, int]:
    """Page-level offsets of the cited text (falls back to the whole chunk)."""
    idx = hit.text.find(cited.strip()[:200]) if cited else -1
    if idx < 0:
        return hit.char_start, hit.char_end
    return hit.char_start + idx, hit.char_start + idx + len(cited.strip())


def answer_question(
    db: Session,
    user_id: uuid.UUID,
    conversation: Conversation,
    question: str,
    *,
    document_ids: list[uuid.UUID] | None = None,
    kb_ids: list[uuid.UUID] | None = None,
) -> AskResult:
    t0 = time.perf_counter()
    s = get_settings()
    llm = get_llm()
    notes: list[str] = []
    history_rows = db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at.desc()).limit(6)
    ).all()
    history = [(m.role, m.content) for m in reversed(history_rows)]
    standalone = llm.rewrite_question(question, history)
    # Entity-constrained retrieval: if the question names specific things (codes, numbers,
    # proper names), only documents containing all of them are eligible. None → not found.
    idents = identifiers(standalone)
    scope = document_ids
    if idents:
        matching = documents_containing(db, user_id, idents, kb_ids)
        scope = [d for d in matching if document_ids is None or d in document_ids]
        if not scope:
            notes.append(f"No accessible document mentions: {', '.join(idents)}.")
    hits = (
        search(db, user_id, standalone, limit=s.retrieval_top_k, document_ids=scope, kb_ids=kb_ids)
        if scope != []
        else []
    )
    passages = [
        Passage(
            index=i,
            chunk_id=h.chunk_id,
            document_id=str(h.document_id),
            document_title=h.document_title,
            page_no=h.page_no,
            text=h.text,
        )
        for i, h in enumerate(hits)
    ]
    if not is_relevant(hits):
        result = AnswerResult(status="not_found", sentences=[], provider="relevance-gate")
    else:
        result = llm.answer(standalone, passages, history)
        if result.status == "error":
            notes.append("The language model was unavailable; showing the most relevant source sentences instead.")
            result = ExtractiveProvider().answer(standalone, passages, history)
    # ---- grounding enforcement
    if result.status == "answered" and not any(sent.citations for sent in result.sentences):
        result = AnswerResult(status="not_found", sentences=[], provider=result.provider, usage=result.usage)
        notes.append("The model's answer had no supporting citation, so it was withheld.")

    db.add(Message(conversation_id=conversation.id, role="user", content=question))
    answer_text = (
        result.text
        if result.status == "answered"
        else (NOT_FOUND_TEXT if result.status == "not_found" else "The request could not be answered.")
    )
    latency = round((time.perf_counter() - t0) * 1000)
    msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer_text,
        answer_status=result.status,
        provider=result.provider,
        usage=result.usage,
        latency_ms=latency,
    )
    db.add(msg)
    db.flush()

    citations: list[CitationOut] = []
    sentences_out: list[dict[str, Any]] = []
    for sent in result.sentences if result.status == "answered" else []:
        ords = []
        for passage_index, cited in sent.citations:
            hit = hits[passage_index]
            start, end = _locate(hit, cited)
            existing = next((c for c in citations if c.chunk_id == hit.chunk_id and c.cited_text == cited), None)
            if existing is None:
                existing = CitationOut(
                    len(citations) + 1,
                    hit.chunk_id,
                    hit.document_id,
                    hit.document_title,
                    hit.page_no,
                    cited,
                    start,
                    end,
                )
                citations.append(existing)
                db.add(
                    Citation(
                        message_id=msg.id,
                        chunk_id=hit.chunk_id,
                        ordinal=existing.ordinal,
                        cited_text=cited,
                        sentence=sent.text,
                    )
                )
            ords.append(existing.ordinal)
        sentences_out.append({"text": sent.text, "citations": ords, "supported": bool(ords)})
    if conversation.title == "New conversation":
        conversation.title = question[:120]
    db.commit()
    return AskResult(
        message_id=msg.id,
        status=result.status,
        answer=answer_text,
        provider=result.provider,
        sentences=sentences_out,
        citations=citations,
        standalone_question=standalone,
        retrieved=len(hits),
        latency_ms=latency,
        notes=notes,
    )
