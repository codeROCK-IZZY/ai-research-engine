"""Source freshness and reliability indicators.

Both are **deterministic rules**, not model output, and the API labels them
``"source": "rule-based"``. They help a reader judge a source; they never hide one.

Freshness: age of the source's own publication date if known, otherwise when it was
fetched or uploaded. A source older than the knowledge base's ``stale_after_days`` is
marked stale.

Reliability (high / medium / low) from transparent signals:
* + published date known (the reader can judge currency)
* + fetched over HTTPS / uploaded by a workspace member
* + text extracted cleanly (no low-confidence OCR)
* − stale for this knowledge base
* − processing failed or produced very little text
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SourceFacts:
    source_kind: str
    source_url: str | None
    published_at: datetime | None
    fetched_at: datetime | None
    created_at: datetime
    status: str | None
    char_count: int
    min_ocr_confidence: float | None


def freshness(f: SourceFacts, stale_after_days: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    ref, basis = (
        (f.published_at, "published")
        if f.published_at
        else ((f.fetched_at, "fetched") if f.fetched_at else (f.created_at, "added"))
    )
    age = max(0, (now - ref).days)
    return {
        "age_days": age,
        "basis": basis,
        "reference_date": ref.isoformat(),
        "stale": age > stale_after_days,
        "stale_after_days": stale_after_days,
        "source": "rule-based",
    }


def reliability(f: SourceFacts, stale: bool) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    if f.published_at:
        score += 1
        reasons.append("Publication date is known")
    else:
        reasons.append("No publication date")
    if f.source_kind == "url":
        if (f.source_url or "").startswith("https://"):
            score += 1
            reasons.append("Fetched over HTTPS")
        else:
            score -= 1
            reasons.append("Fetched over plain HTTP")
    else:
        score += 1
        reasons.append("Added by a workspace member")
    if f.min_ocr_confidence is not None and f.min_ocr_confidence < 70:
        score -= 1
        reasons.append(f"Some text came from low-confidence OCR ({f.min_ocr_confidence:.0f}%)")
    elif f.status == "ready":
        score += 1
        reasons.append("Text extracted cleanly")
    if f.status == "failed" or (f.status == "ready" and f.char_count < 200):
        score -= 2
        reasons.append("Little or no usable text")
    if stale:
        score -= 1
        reasons.append("Older than this knowledge base's freshness limit")
    level = "high" if score >= 3 else "medium" if score >= 1 else "low"
    return {"level": level, "score": score, "reasons": reasons, "source": "rule-based"}
