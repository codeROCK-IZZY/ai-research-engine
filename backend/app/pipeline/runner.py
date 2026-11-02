"""Processing pipeline orchestration for one document version.

Stages: extract → normalise/language → classify → chunk+embed+index → understand
(summary, entities, invoice/contract fields) → near-duplicate signature.

Each stage records ``{status, ms, detail}`` in ``document_versions.stages`` so the UI can
show progress and the exact failing stage. The whole run is **idempotent**: rows from a
previous attempt are deleted first, so a retried job never duplicates chunks.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, delete, func, insert
from sqlalchemy.orm import Session

from app.ai.providers import get_embedder, get_llm
from app.ai.textutil import extractive_summary
from app.core.logging import log_extra
from app.db.models import DOC_TYPES, Chunk, Document, DocumentVersion, Entity, Extraction, MinhashSignature, Page
from app.pipeline import understand
from app.pipeline.extract import PageContent, extract
from app.pipeline.text import chunk_page, detect_language, normalize, ts_config
from app.storage.files import get_store

logger = logging.getLogger(__name__)


def _stage(db: Session, v: DocumentVersion, name: str, fn: Callable[[], Any]) -> Any:
    v.stage = name
    v.stages = {**v.stages, name: {"status": "running"}}
    db.commit()
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        db.rollback()
        v = db.merge(v)
        v.stages = {**v.stages, name: {"status": "failed", "error": str(exc)[:500]}}
        db.commit()
        raise
    ms = round((time.perf_counter() - t0) * 1000)
    detail = result if isinstance(result, dict) else {}
    v.stages = {**v.stages, name: {"status": "done", "ms": ms, **detail}}
    db.commit()
    return result


def _reset(db: Session, version_id: uuid.UUID) -> None:
    for model in (Chunk, Page, Entity, Extraction, MinhashSignature):
        db.execute(delete(model).where(model.version_id == version_id))
    db.flush()


def process_version(db: Session, version_id: uuid.UUID) -> dict[str, Any]:
    found = db.get(DocumentVersion, version_id)
    if found is None:
        return {"skipped": "version deleted"}
    v: DocumentVersion = found  # non-optional for the nested stage functions
    doc = db.get(Document, v.document_id)
    assert doc is not None
    _reset(db, v.id)
    v.status, v.error, v.stages = "processing", None, {}
    db.commit()
    llm = get_llm()
    state: dict[str, Any] = {}

    def do_extract() -> dict[str, Any]:
        pages: list[PageContent] = extract(get_store().get(v.file_key), v.mime)
        for p in pages:
            p.text = normalize(p.text)
        state["pages"] = pages
        for p in pages:
            db.add(
                Page(
                    version_id=v.id,
                    page_no=p.page_no,
                    text=p.text,
                    ocr_used=p.ocr_used,
                    ocr_confidence=p.ocr_confidence,
                    tables=p.tables,
                )
            )
        v.page_count = len(pages)
        state["full_text"] = "\n\n".join(p.text for p in pages)
        v.language = detect_language(state["full_text"])
        return {"pages": len(pages), "ocr_pages": sum(p.ocr_used for p in pages), "language": v.language}

    def do_classify() -> dict[str, Any]:
        text = state["full_text"]
        label, conf = understand.classify_rules(text)
        source = "rule:keywords"
        llm_result = llm.classify(text, DOC_TYPES)
        if llm_result is not None:
            label, conf, source = llm_result
        if doc.doc_type_source != "user":  # never override a type the user set manually
            doc.doc_type, doc.doc_type_source = label, source
        return {"label": label, "confidence": conf, "source": source}

    def do_index() -> dict[str, Any]:
        spans = [s for p in state["pages"] for s in chunk_page(p.page_no, p.text)]
        if not spans:
            return {"chunks": 0}
        vectors = get_embedder().passages([s.text for s in spans])
        cfg = ts_config(v.language)
        # The full-text vector is computed by PostgreSQL with the document's language config;
        # bindparam lets one executemany statement apply to_tsvector to every row.
        db.execute(
            insert(Chunk).values(tsv=func.to_tsvector(cfg, bindparam("tsv_source"))),
            [
                {
                    "version_id": v.id,
                    "page_no": s.page_no,
                    "ordinal": i,
                    "char_start": s.start,
                    "char_end": s.end,
                    "text": s.text,
                    "tsv_source": s.text,
                    "embedding": vec,
                }
                for i, (s, vec) in enumerate(zip(spans, vectors, strict=True))
            ],
        )
        return {"chunks": len(spans), "ts_config": cfg}

    def do_understand() -> dict[str, Any]:
        text = state["full_text"]
        summary = llm.summarize(text) or (extractive_summary(text), "rule:extractive-summary")
        db.add(Extraction(version_id=v.id, kind="summary", data={"summary": summary[0]}, source=summary[1]))
        ents = understand.extract_entities([(p.page_no, p.text) for p in state["pages"]])
        if ents:
            db.execute(insert(Entity), [{"version_id": v.id, **e} for e in ents])
        fields = None
        if doc.doc_type == "invoice":
            tables = [t for p in state["pages"] for t in p.tables]
            got = llm.extract("invoice", text, understand.INVOICE_SCHEMA)
            data, source = got if got else (understand.extract_invoice_rules(text, tables), "rule:regex")
            fields = Extraction(
                version_id=v.id, kind="invoice", data=data, source=source, validation=understand.validate_invoice(data)
            )
        elif doc.doc_type == "contract":
            got = llm.extract("contract", text, understand.CONTRACT_SCHEMA)
            data, source = got if got else (understand.extract_contract_rules(text), "rule:regex")
            fields = Extraction(
                version_id=v.id,
                kind="contract",
                data=data,
                source=source,
                validation=understand.validate_contract(data),
            )
        if fields is not None:
            db.add(fields)
        return {"entities": len(ents), "fields": fields.kind if fields else None}

    def do_minhash() -> dict[str, Any]:
        db.add(MinhashSignature(version_id=v.id, signature=understand.minhash(state["full_text"]).tobytes()))
        return {}

    try:
        _stage(db, v, "extract", do_extract)
        _stage(db, v, "classify", do_classify)
        _stage(db, v, "index", do_index)
        _stage(db, v, "understand", do_understand)
        _stage(db, v, "deduplicate", do_minhash)
    except Exception as exc:
        v = db.merge(v)
        v.status, v.error = "failed", f"{v.stage}: {exc}"[:2000]
        db.commit()
        logger.exception("pipeline failed", extra=log_extra(version_id=str(version_id), stage=v.stage))
        raise
    v.status, v.stage, v.processed_at = "ready", None, datetime.now(UTC)
    db.commit()
    logger.info("document processed", extra=log_extra(version_id=str(version_id), pages=v.page_count))
    return {"status": "ready", "pages": v.page_count}
