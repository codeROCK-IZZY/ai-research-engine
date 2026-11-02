"""Evaluation harness. Every reported number comes from here.

Retrieval and answers are measured **separately**, on the labelled synthetic corpus:

* **Retrieval** (answerable questions; document-level relevance from the gold sources):
  recall@5, recall@10, MRR and nDCG@10 for keyword-only, vector-only, hybrid (RRF) and
  hybrid + cross-encoder re-ranking. A good answer is impossible if retrieval misses.
* **Answers** (full pipeline, as each question's asker):
  - fact accuracy — the gold fact string appears in the answer;
  - citation precision — share of citations that point at a gold source;
  - groundedness — share of answer sentences that carry a citation;
  - correct refusal — "not found" for unanswerable questions;
  - false refusal — "not found" for answerable questions;
  - conflict flagged — for questions whose sources disagree, the synthesis reports a conflict;
  - **leakage** — any citation to a source the asker cannot read (must be 0).

Answer rows depend on the active answer provider (offline extractive, or Claude); the report
states which one produced them.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.ai.providers import get_llm
from app.core.config import get_settings
from app.db.models import Conversation, User
from app.knowledge.synthesis import compare_sources
from app.retrieval.qa import answer_question
from app.retrieval.search import search

MODES = (
    ("keyword", "keyword", False),
    ("vector", "vector", False),
    ("hybrid", "hybrid", False),
    ("hybrid + re-rank", "hybrid", True),
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.%$+-]", "", s.lower().replace(",", ""))


def _doc_ranking(hits: list[Any]) -> list[uuid.UUID]:
    seen: list[uuid.UUID] = []
    for h in hits:
        if h.document_id not in seen:
            seen.append(h.document_id)
    return seen


def retrieval_metrics(ranked: list[uuid.UUID], gold: set[uuid.UUID]) -> dict[str, float]:
    first = next((i + 1 for i, d in enumerate(ranked) if d in gold), None)
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:10]) if d in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    return {
        "recall_at_5": 1.0 if first and first <= 5 else 0.0,
        "recall_at_10": 1.0 if first and first <= 10 else 0.0,
        "mrr": 1 / first if first else 0.0,
        "ndcg_at_10": dcg / idcg if idcg else 0.0,
    }


def _mean(rows: list[dict[str, float]]) -> dict[str, float]:
    return {k: round(sum(r[k] for r in rows) / len(rows), 3) for k in rows[0]} if rows else {}


def evaluate(db: Session, manifest: dict[str, Any], doc_ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    t0 = time.perf_counter()
    users = {u.email.split("@")[0]: u for u in db.scalars(select(User))}
    readable: dict[str, set[uuid.UUID]] = {
        name: set(db.scalars(text("SELECT document_id FROM readable_documents WHERE user_id = :u"), {"u": u.id}))
        for name, u in users.items()
    }
    s = get_settings()
    report: dict[str, Any] = {
        "provider": get_llm().name,
        "embedding_model": s.embedding_model,
        "reranker_model": s.reranker_model,
        "corpus": {
            "sources": len(manifest["sources"]),
            "questions": len(manifest["questions"]),
            "seed": manifest["seed"],
        },
    }
    answerable = [q for q in manifest["questions"] if q["answer"] is not None]

    # ---- retrieval, per mode, per question kind
    retrieval: dict[str, Any] = {}
    latency: dict[str, float] = {}
    for label, mode, rerank in MODES:
        rows_by_kind: dict[str, list[dict[str, float]]] = defaultdict(list)
        started = time.perf_counter()
        for q in answerable:
            hits = search(db, users[q["asker"]].id, q["question"], limit=10, mode=mode, rerank=rerank)
            m = retrieval_metrics(_doc_ranking(hits), {doc_ids[f] for f in q["gold"]})
            rows_by_kind[q["kind"]].append(m)
            rows_by_kind["all"].append(m)
        latency[label] = round((time.perf_counter() - started) * 1000 / len(answerable), 1)
        retrieval[label] = {k: _mean(v) for k, v in rows_by_kind.items()}
    report["retrieval"] = {label: v["all"] for label, v in retrieval.items()}
    report["retrieval_by_kind"] = retrieval
    report["retrieval_latency_ms_per_query"] = latency

    # ---- answers (full pipeline)
    rows: list[dict[str, Any]] = []
    eval_convs: list[uuid.UUID] = []
    for q in manifest["questions"]:
        asker = users[q["asker"]]
        conv = Conversation(user_id=asker.id, title=f"eval {q['id']}")
        db.add(conv)
        db.flush()
        eval_convs.append(conv.id)
        r = answer_question(db, asker.id, conv, q["question"])
        gold = {doc_ids[f] for f in q["gold"]}
        cited = [c.document_id for c in r.citations]
        row: dict[str, Any] = {
            "id": q["id"],
            "kind": q["kind"],
            "status": r.status,
            "leaked": any(d not in readable[q["asker"]] for d in cited),
        }
        if q["answer"] is None:
            row["correct_refusal"] = r.status == "not_found"
        else:
            row["false_refusal"] = r.status != "answered"
            row["fact_correct"] = r.status == "answered" and _norm(q["answer"]) in _norm(r.answer)
            if cited:
                row["citation_precision"] = sum(d in gold for d in cited) / len(cited)
            if r.sentences:
                row["grounded"] = sum(1 for x in r.sentences if x["supported"]) / len(r.sentences)
            if q["kind"] == "conflict":
                syn = compare_sources(
                    r.standalone_question, [{**asdict(c), "document_id": str(c.document_id)} for c in r.citations]
                )
                row["conflict_flagged"] = syn["agreement"] == "conflict"
        rows.append(row)
    db.execute(delete(Conversation).where(Conversation.id.in_(eval_convs)))
    db.commit()

    def avg(key: str, subset: list[dict[str, Any]]) -> float | None:
        vals = [float(r[key]) for r in subset if key in r]
        return round(sum(vals) / len(vals), 3) if vals else None

    kinds = sorted({r["kind"] for r in rows})
    keys = ("fact_correct", "false_refusal", "citation_precision", "grounded", "correct_refusal", "conflict_flagged")
    report["answers"] = {
        "overall": {
            **{k: avg(k, rows) for k in keys},
            "leakage": sum(r["leaked"] for r in rows),
            "questions": len(rows),
        },
        "by_kind": {
            k: {
                **{m: avg(m, [r for r in rows if r["kind"] == k]) for m in keys},
                "questions": sum(r["kind"] == k for r in rows),
            }
            for k in kinds
        },
        "rows": rows,
    }
    report["seconds"] = round(time.perf_counter() - t0, 1)
    return report


def _f(v: Any) -> str:
    return "—" if v is None else (f"{100 * v:.1f}%" if isinstance(v, float) and v <= 1 else str(v))


def write_report(r: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    L = [
        "# Evaluation report — AI Research & Knowledge Engine",
        "",
        "Generated by `python -m app.cli evaluate` on the seeded synthetic corpus "
        f"({r['corpus']['sources']} sources, {r['corpus']['questions']} questions, seed {r['corpus']['seed']}). "
        "These numbers describe this corpus, not every real knowledge base.",
        "",
        f"Answer provider: **{r['provider']}** · embeddings: `{r['embedding_model']}` · "
        f"re-ranker: `{r['reranker_model']}`",
        "",
        "## Retrieval (document level, answerable questions)",
        "",
        "| Mode | Recall@5 | Recall@10 | MRR | nDCG@10 | Latency / query |",
        "|---|---|---|---|---|---|",
    ]
    for mode, m in r["retrieval"].items():
        L.append(
            f"| {mode} | {_f(m['recall_at_5'])} | {_f(m['recall_at_10'])} | {m['mrr']:.3f} | "
            f"{m['ndcg_at_10']:.3f} | {r['retrieval_latency_ms_per_query'][mode]} ms |"
        )
    L += [
        "",
        "### MRR by question kind",
        "",
        "| Kind | " + " | ".join(r["retrieval"]) + " |",
        "|---|" + "---|" * len(r["retrieval"]),
    ]
    kinds = sorted(k for k in next(iter(r["retrieval_by_kind"].values())) if k != "all")
    for k in kinds:
        L.append(f"| {k} | " + " | ".join(f"{r['retrieval_by_kind'][m][k]['mrr']:.3f}" for m in r["retrieval"]) + " |")
    a = r["answers"]
    L += [
        "",
        "## Answers (full pipeline, as each question's asker)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fact accuracy (answerable) | {_f(a['overall']['fact_correct'])} |",
        f"| False refusals (answerable) | {_f(a['overall']['false_refusal'])} |",
        f"| Citation precision | {_f(a['overall']['citation_precision'])} |",
        f"| Groundedness (sentences with a citation) | {_f(a['overall']['grounded'])} |",
        f"| Correct refusals (unanswerable) | {_f(a['overall']['correct_refusal'])} |",
        f"| Conflicting sources flagged | {_f(a['overall']['conflict_flagged'])} |",
        f"| **Leakage** (citations to unreadable sources) | **{a['overall']['leakage']}** |",
        "",
        "| Kind | Questions | Fact accuracy | False refusal | Correct refusal |",
        "|---|---|---|---|---|",
    ]
    for k, m in a["by_kind"].items():
        L.append(
            f"| {k} | {m['questions']} | {_f(m['fact_correct'])} | {_f(m['false_refusal'])} | "
            f"{_f(m['correct_refusal'])} |"
        )
    L += ["", f"Evaluation took {r['seconds']} s."]
    (out / "evaluation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
