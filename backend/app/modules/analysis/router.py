"""Side-by-side comparison of sources. Deterministic."""

from __future__ import annotations

import difflib
import uuid
from itertools import combinations
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.db.models import Extraction, Page
from app.modules.documents import service as docs

router = APIRouter(tags=["analysis"])
NEAR_DUP_THRESHOLD = 0.8


class CompareIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)


def _full_text(db: DB, version_id: uuid.UUID | None) -> str:
    return "\n".join(
        p.text for p in db.scalars(select(Page).where(Page.version_id == version_id).order_by(Page.page_no))
    )


@router.post("/compare")
def compare(body: CompareIn, db: DB, user: Viewer) -> dict[str, Any]:
    """Pairwise text similarity, changed lines, and field-by-field differences for documents
    of the same extracted kind (e.g. two invoices or two contracts)."""
    documents = [docs.get_document(db, user.id, d) for d in body.document_ids]
    texts = {d.id: _full_text(db, d.current_version_id) for d in documents}
    fields = {
        d.id: db.scalar(
            select(Extraction).where(Extraction.version_id == d.current_version_id, Extraction.kind != "summary")
        )
        for d in documents
    }
    pairs = []
    for a, b in combinations(documents, 2):
        ta, tb = texts[a.id], texts[b.id]
        diff = [
            line
            for line in difflib.unified_diff(ta.splitlines(), tb.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and line[:3] not in ("+++", "---")
        ]
        fa, fb = fields[a.id], fields[b.id]
        field_diff = None
        if fa and fb and fa.kind == fb.kind:
            keys = sorted(set(fa.data) | set(fb.data))
            field_diff = [
                {"field": k, "a": fa.data.get(k), "b": fb.data.get(k)}
                for k in keys
                if fa.data.get(k) != fb.data.get(k) and k != "line_items"
            ]
        pairs.append(
            {
                "a": {"id": str(a.id), "title": a.title},
                "b": {"id": str(b.id), "title": b.title},
                "text_similarity": round(difflib.SequenceMatcher(None, ta[:50_000], tb[:50_000]).ratio(), 4),
                "changed_lines": diff[:200],
                "field_differences": field_diff,
            }
        )
    return {
        "documents": [{"id": str(d.id), "title": d.title, "doc_type": d.doc_type} for d in documents],
        "pairs": pairs,
        "method": "deterministic text diff and extracted-field comparison",
    }
