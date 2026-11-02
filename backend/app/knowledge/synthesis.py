"""Cross-source synthesis: do the cited sources agree?

When an answer cites passages from more than one source, this deterministic check looks
for **quantities that differ between sources about the same thing**: numbers with a unit
(days, %, $, hours, GB …) or years that follow the same key term from the question.

Example: one policy says "refunds within 30 days", a newer one says "refunds within 14
days" → reported as a conflict, naming both sources, so the reader checks which is current
(the freshness indicator helps). It is a rule-based hint, labelled as such, not a verdict.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.ai.textutil import terms

QUANTITY = re.compile(
    r"(?P<num>[$€£]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>%|percent|days?|hours?|minutes?|weeks?|months?|years?|"
    r"gb|mb|tb|ms|seconds?|users?|requests?|usd|eur)?",
    re.IGNORECASE,
)


def _quantities(text: str, keys: set[str]) -> dict[str, set[str]]:
    """key term → quantities appearing within 8 words after it."""
    words = re.findall(r"[\w$€£.%,]+", text.lower())
    found: dict[str, set[str]] = defaultdict(set)
    for i, w in enumerate(words):
        stem = w.strip(".,")
        if stem in keys:
            window = " ".join(words[i + 1 : i + 9])
            for m in QUANTITY.finditer(window):
                unit = (m.group("unit") or "").lower().rstrip("s")
                if unit or m.group("num").startswith(("$", "€", "£")):
                    found[stem].add(f"{m.group('num').rstrip('.,')} {unit}".strip())
    return found


def compare_sources(question: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    """``citations``: dicts with ``document_id``, ``document_title``, ``cited_text``."""
    by_doc: dict[str, dict[str, Any]] = {}
    for c in citations:
        d = by_doc.setdefault(str(c["document_id"]), {"title": c["document_title"], "texts": []})
        d["texts"].append(c["cited_text"])
    docs = [{"document_id": k, "title": v["title"], "citations": len(v["texts"])} for k, v in by_doc.items()]
    if len(by_doc) < 2:
        return {
            "documents": docs,
            "agreement": "single_source" if by_doc else "none",
            "conflicts": [],
            "source": "rule-based",
        }
    keys = {t for t in terms(question) if len(t) > 2}
    per_doc = {k: _quantities(" ".join(v["texts"]), keys) for k, v in by_doc.items()}
    conflicts = []
    for key in sorted(keys):
        values = {k: q[key] for k, q in per_doc.items() if q.get(key)}
        if len(values) >= 2 and len({frozenset(v) for v in values.values()}) > 1:
            conflicts.append(
                {
                    "term": key,
                    "values": [
                        {"document_id": k, "title": by_doc[k]["title"], "values": sorted(v)} for k, v in values.items()
                    ],
                }
            )
    return {
        "documents": docs,
        "agreement": "conflict" if conflicts else "consistent",
        "conflicts": conflicts,
        "source": "rule-based",
    }
