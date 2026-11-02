"""Normalisation, language detection and chunking.

Chunking rules (chosen so citations are exact):
* chunks never cross a page boundary;
* each chunk records character offsets into its page's normalised text;
* target ~1,600 characters (~400 tokens) with ~15% overlap, split at sentence/line ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.textutil import sentences

TARGET_CHARS = 1600
OVERLAP_CHARS = 240
TS_CONFIG = {"en": "english", "fr": "french", "es": "spanish"}


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_language(text: str) -> str | None:
    sample = text[:5000].strip()
    if len(sample) < 40:
        return None
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0  # deterministic results
    try:
        return str(detect(sample))
    except LangDetectException:
        return None


def ts_config(language: str | None) -> str:
    """PostgreSQL text-search configuration (stemming) for a detected language."""
    return TS_CONFIG.get(language or "", "simple")


@dataclass(frozen=True)
class ChunkSpan:
    page_no: int
    start: int
    end: int
    text: str


def chunk_page(page_no: int, text: str, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[ChunkSpan]:
    if not text.strip():
        return []
    if len(text) <= target:
        return [ChunkSpan(page_no, 0, len(text), text)]
    # Segments = sentences plus line breaks, so tables/lists split on rows, not mid-cell.
    segs: list[tuple[int, int]] = []
    for start, end, _ in sentences(text):
        while end - start > target:  # a single very long "sentence" (e.g. a table): hard split
            segs.append((start, start + target))
            start += target
        segs.append((start, end))
    out: list[ChunkSpan] = []
    i = 0
    while i < len(segs):
        start = segs[i][0]
        j = i
        while j + 1 < len(segs) and segs[j + 1][1] - start <= target:
            j += 1
        end = segs[j][1]
        out.append(ChunkSpan(page_no, start, end, text[start:end]))
        if j + 1 >= len(segs):
            break
        # Step back to create overlap, but always advance at least one segment.
        k = j
        while k > i + 1 and end - segs[k][0] < overlap:
            k -= 1
        i = max(k, i + 1)
    return out
