"""Hybrid retrieval with access control inside the query.

1. ``allowed`` CTE — current versions of documents the user may read, via the
   ``readable_documents`` view (the single definition of "may read").
2. Keyword branch — PostgreSQL full-text search. The question's content words are OR-ed
   (natural questions rarely contain every word of the answer passage) and the query is
   built in English, French, Spanish and 'simple' configurations so stemmed and
   unstemmed chunks all match. Ranked with ``ts_rank_cd``.
3. Vector branch — cosine similarity against the local embedding of the question.
4. Reciprocal Rank Fusion: score = Σ 1 / (60 + rank) over both branches.
5. Optional re-ranking: the top ``rerank_candidates`` fused results are re-scored by a
   cross-encoder and re-ordered; ``rerank_score`` is exposed as the source relevance score.

Why the vector branch is an *exact* scan over allowed chunks rather than an HNSW index
scan: approximate indexes return the nearest neighbours first and filter afterwards, so a
user whose documents are a small share of the corpus can silently get zero results. Exact
search over the user's own chunks is always correct and fast at per-user scale
(thousands to ~100k chunks). See docs/known-limitations.md for the scaling path.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.providers import get_embedder, get_reranker
from app.ai.textutil import terms
from app.core.config import get_settings

RRF_K = 60
BRANCH_LIMIT = 50


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    document_id: uuid.UUID
    document_title: str
    doc_type: str | None
    version_id: uuid.UUID
    page_no: int
    char_start: int
    char_end: int
    text: str
    score: float
    vector_similarity: float | None
    keyword_rank: float | None
    rerank_score: float | None = None  # cross-encoder relevance, 0–1 (logistic of the raw score)
    kb_id: uuid.UUID | None = None


def or_tsquery_text(question: str) -> str | None:
    """OR-query of the question's content words. Hyphenated codes are split into their parts
    ("INV-20260003" → "inv | 20260003"), matching how PostgreSQL's parser indexes them."""
    words = [part for t in terms(question) for part in re.split(r"[^\w]+", t) if part]
    words = [w for w in dict.fromkeys(words) if len(w) > 1][:24]
    return " | ".join(words) if words else None


SEARCH_SQL = """
WITH allowed AS (
    SELECT DISTINCT d.id AS document_id, d.current_version_id AS version_id, d.title, d.doc_type, d.kb_id
    FROM readable_documents r
    JOIN documents d ON d.id = r.document_id
    WHERE r.user_id = :user_id
      AND d.current_version_id IS NOT NULL
      {filters}
),
kw AS (
    SELECT c.id, ts_rank_cd(c.tsv, q.query) AS rank_score,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.query) DESC, c.id) AS rnk
    FROM chunks c
    JOIN allowed a ON a.version_id = c.version_id
    CROSS JOIN (
        SELECT to_tsquery('english', :tsq) || to_tsquery('french', :tsq)
            || to_tsquery('spanish', :tsq) || to_tsquery('simple', :tsq) AS query
    ) q
    WHERE CAST(:tsq AS text) IS NOT NULL AND c.tsv @@ q.query
    ORDER BY rank_score DESC, c.id
    LIMIT :kw_limit
),
vec AS (
    SELECT c.id, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS sim,
           row_number() OVER (ORDER BY (c.embedding <=> CAST(:qvec AS vector)) + 0, c.id) AS rnk
    FROM chunks c
    JOIN allowed a ON a.version_id = c.version_id
    -- "+ 0" keeps the planner from using the HNSW index (see module docstring).
    ORDER BY (c.embedding <=> CAST(:qvec AS vector)) + 0, c.id
    LIMIT :vec_limit
),
fused AS (
    SELECT id, sum(rrf) AS score, max(sim) AS sim, max(rank_score) AS kw
    FROM (
        SELECT id, 1.0 / (:rrf_k + rnk) AS rrf, NULL::float AS sim, rank_score FROM kw
        UNION ALL
        SELECT id, 1.0 / (:rrf_k + rnk), sim, NULL::float FROM vec
    ) u
    GROUP BY id
)
SELECT c.id AS chunk_id, a.document_id, a.title, a.doc_type, a.kb_id, c.version_id, c.page_no, c.char_start,
       c.char_end, c.text, f.score, f.sim, f.kw
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN allowed a ON a.version_id = c.version_id
ORDER BY f.score DESC, c.id
LIMIT :limit
"""


def documents_containing(
    db: Session, user_id: uuid.UUID, idents: list[str], kb_ids: list[uuid.UUID] | None = None
) -> list[uuid.UUID]:
    """Readable documents (optionally within ``kb_ids``) whose current version contains *every*
    identifier (case-insensitive)."""
    if not idents:
        return []
    conds = " AND ".join(
        f"EXISTS (SELECT 1 FROM chunks c WHERE c.version_id = d.current_version_id AND c.text ILIKE :p{i})"
        for i in range(len(idents))
    )
    params: dict[str, Any] = {"u": user_id}
    kb_filter = ""
    if kb_ids is not None:
        kb_filter = " AND d.kb_id = ANY(:kb_ids)"
        params["kb_ids"] = kb_ids
    for i, ident in enumerate(idents):
        params[f"p{i}"] = "%" + ident.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = (
        db.execute(
            text(
                "SELECT DISTINCT d.id FROM readable_documents r JOIN documents d ON d.id = r.document_id "
                f"WHERE r.user_id = :u AND d.current_version_id IS NOT NULL{kb_filter} AND {conds}"
            ),
            params,
        )
        .scalars()
        .all()
    )
    return list(rows)


def search(
    db: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    limit: int = 10,
    document_ids: list[uuid.UUID] | None = None,
    doc_type: str | None = None,
    kb_ids: list[uuid.UUID] | None = None,
    mode: str = "hybrid",
    rerank: bool | None = None,
) -> list[Hit]:
    """``mode`` = hybrid | keyword | vector (single-branch modes exist for evaluation).
    ``rerank`` = re-order the top candidates with the cross-encoder (default from settings)."""
    s = get_settings()
    rerank = s.rerank_by_default if rerank is None else rerank
    filters: list[str] = []
    params: dict[str, Any] = {}
    if document_ids:
        filters.append("AND d.id = ANY(:doc_ids)")
        params["doc_ids"] = document_ids
    if doc_type:
        filters.append("AND d.doc_type = :doc_type")
        params["doc_type"] = doc_type
    if kb_ids is not None:
        filters.append("AND d.kb_id = ANY(:kb_ids)")
        params["kb_ids"] = kb_ids
    if mode not in ("hybrid", "keyword", "vector"):
        raise ValueError(f"unknown search mode {mode}")
    tsq = or_tsquery_text(query) if mode != "vector" else None
    rows = (
        db.execute(
            text(SEARCH_SQL.format(filters=" ".join(filters))),
            {
                "user_id": user_id,
                "tsq": tsq,
                "qvec": str(get_embedder().query(query)),
                "rrf_k": RRF_K,
                "limit": max(limit, s.rerank_candidates) if rerank else limit,
                "kw_limit": BRANCH_LIMIT if mode != "vector" else 0,
                "vec_limit": BRANCH_LIMIT if mode != "keyword" else 0,
                **params,
            },
        )
        .mappings()
        .all()
    )
    hits = [_hit(r) for r in rows]
    if rerank and hits:
        raw = get_reranker().scores(query, [h.text for h in hits])
        scored = [replace(h, rerank_score=1 / (1 + math.exp(-x))) for h, x in zip(hits, raw, strict=True)]
        hits = sorted(scored, key=lambda h: h.rerank_score or 0.0, reverse=True)
    return hits[:limit]


def _hit(r: Any) -> Hit:
    return Hit(
        chunk_id=r["chunk_id"],
        document_id=r["document_id"],
        document_title=r["title"],
        doc_type=r["doc_type"],
        version_id=r["version_id"],
        page_no=r["page_no"],
        char_start=r["char_start"],
        char_end=r["char_end"],
        text=r["text"],
        score=float(r["score"]),
        vector_similarity=r["sim"],
        keyword_rank=r["kw"],
        kb_id=r["kb_id"],
    )
