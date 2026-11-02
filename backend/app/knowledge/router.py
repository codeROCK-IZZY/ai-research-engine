"""Knowledge views of a knowledge base: concept graph, topics, duplicates, freshness."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

import numpy as np
from fastapi import APIRouter, Query
from sqlalchemy import select, text

from app.api.deps import DB, Viewer
from app.db.models import Document
from app.knowledge.topics import choose_k, kmeans, label_topics
from app.modules.documents.router import _doc_out
from app.modules.documents.service import access_level
from app.modules.workspaces.service import require_kb
from app.pipeline.understand import jaccard_estimate

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["knowledge"])
NEAR_DUP_THRESHOLD = 0.8


@router.get("/graph")
def concept_graph(
    kb_id: uuid.UUID, db: DB, user: Viewer, max_nodes: int = Query(default=60, ge=5, le=200)
) -> dict[str, Any]:
    """Concepts (acronyms, multi-word names, organisations) as nodes; an edge joins two concepts
    that appear in the same source, weighted by how many sources they share. Rule-based."""
    require_kb(db, user, kb_id)
    rows = db.execute(
        text(
            """SELECT d.id AS doc, d.title, e.normalized AS key, min(e.value) AS label
               FROM documents d
               JOIN entities e ON e.version_id = d.current_version_id
               WHERE d.kb_id = :kb AND e.entity_type IN ('concept', 'organization') AND e.normalized IS NOT NULL
               GROUP BY d.id, d.title, e.normalized"""
        ),
        {"kb": kb_id},
    ).all()
    docs_of: dict[str, set[str]] = defaultdict(set)
    label: dict[str, str] = {}
    titles: dict[str, str] = {}
    for r in rows:
        docs_of[r.key].add(str(r.doc))
        label.setdefault(r.key, r.label)
        titles[str(r.doc)] = r.title
    # Keep concepts that appear in at least two sources (single-source names are rarely useful).
    ranked = sorted((k for k, d in docs_of.items() if len(d) >= 2), key=lambda k: (-len(docs_of[k]), k))[:max_nodes]
    keep = set(ranked)
    edges: list[dict[str, Any]] = []
    for a, b in combinations(ranked, 2):
        shared = docs_of[a] & docs_of[b]
        if len(shared) >= 2:
            edges.append({"source": a, "target": b, "weight": len(shared)})
    edges.sort(key=lambda e: -int(e["weight"]))
    return {
        "nodes": [
            {
                "id": k,
                "label": label[k],
                "sources": len(docs_of[k]),
                "examples": sorted(titles[d] for d in docs_of[k])[:5],
            }
            for k in ranked
        ],
        "edges": edges[: max_nodes * 4],
        "concepts_total": len(docs_of),
        "shown": len(keep),
        "source": "rule-based",
    }


@router.get("/topics")
def topics(kb_id: uuid.UUID, db: DB, user: Viewer) -> dict[str, Any]:
    """Group sources into topics by meaning (k-means on mean chunk embeddings)."""
    require_kb(db, user, kb_id)
    rows = db.execute(
        text(
            """SELECT d.id, d.title, avg(c.embedding)::text AS vec, string_agg(left(c.text, 400), ' ') AS sample
               FROM documents d JOIN chunks c ON c.version_id = d.current_version_id
               WHERE d.kb_id = :kb GROUP BY d.id, d.title ORDER BY d.title"""
        ),
        {"kb": kb_id},
    ).all()
    if len(rows) < 4:
        return {"topics": [], "note": "At least four processed sources are needed to find topics."}
    x = np.array([[float(v) for v in r.vec.strip("[]").split(",")] for r in rows])
    x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-12
    k = choose_k(len(rows))
    labels = kmeans(x, k)
    texts: dict[int, str] = defaultdict(str)
    members: dict[int, list[dict[str, str]]] = defaultdict(list)
    for r, lab in zip(rows, labels, strict=True):
        texts[int(lab)] += " " + r.title + " " + (r.sample or "")
        members[int(lab)].append({"id": str(r.id), "title": r.title})
    names = label_topics(texts)
    out = [
        {"topic": t, "label": " · ".join(names[t]), "keywords": names[t], "sources": members[t]}
        for t in sorted(members, key=lambda t: -len(members[t]))
    ]
    return {"topics": out, "k": k, "method": "k-means on mean chunk embeddings; TF-IDF labels", "source": "rule-based"}


@router.get("/duplicates")
def duplicates(kb_id: uuid.UUID, db: DB, user: Viewer) -> dict[str, Any]:
    """Exact duplicates (same SHA-256) and near-duplicates (MinHash Jaccard ≥ 0.8) in this knowledge base."""
    require_kb(db, user, kb_id)
    rows = db.execute(
        text(
            """SELECT d.id, d.title, v.sha256, m.signature
               FROM documents d JOIN document_versions v ON v.id = d.current_version_id
               LEFT JOIN minhash_signatures m ON m.version_id = v.id
               WHERE d.kb_id = :kb"""
        ),
        {"kb": kb_id},
    ).all()
    exact: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        exact[r.sha256].append({"id": str(r.id), "title": r.title})
    near = []
    signed = [r for r in rows if r.signature is not None]
    for a, b in combinations(signed[:2000], 2):  # O(n²); LSH banding is the scale-up path
        if a.sha256 != b.sha256 and (j := jaccard_estimate(a.signature, b.signature)) >= NEAR_DUP_THRESHOLD:
            near.append(
                {
                    "a": {"id": str(a.id), "title": a.title},
                    "b": {"id": str(b.id), "title": b.title},
                    "similarity": round(j, 3),
                }
            )
    return {
        "exact": [g for g in exact.values() if len(g) > 1],
        "near": sorted(near, key=lambda x: -x["similarity"]),
        "threshold": NEAR_DUP_THRESHOLD,
    }


@router.get("/health")
def kb_health(kb_id: uuid.UUID, db: DB, user: Viewer) -> dict[str, Any]:
    """Freshness and reliability across the knowledge base (rule-based indicators)."""
    require_kb(db, user, kb_id)
    docs = list(db.scalars(select(Document).where(Document.kb_id == kb_id).order_by(Document.title)))
    out = [_doc_out(db, d, access_level(db, user.id, d.id) or "read") for d in docs]
    stale = [d for d in out if d.freshness["stale"]]
    levels = Counter(d.reliability["level"] for d in out)
    return {
        "sources": len(out),
        "stale": [
            {"id": str(d.id), "title": d.title, "age_days": d.freshness["age_days"], "basis": d.freshness["basis"]}
            for d in stale
        ],
        "reliability": {lvl: levels.get(lvl, 0) for lvl in ("high", "medium", "low")},
        "low_reliability": [
            {"id": str(d.id), "title": d.title, "reasons": d.reliability["reasons"]}
            for d in out
            if d.reliability["level"] == "low"
        ],
        "source": "rule-based",
    }
