"""Small deterministic text utilities shared by the pipeline and the extractive provider."""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'&.-]*")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "does",
        "do",
        "did",
        "how",
        "many",
        "much",
        "our",
        "your",
        "their",
        "there",
        "than",
        "then",
        "into",
        "over",
        "under",
        "per",
        "any",
        "le",
        "la",
        "les",
        "de",
        "des",
        "du",
        "et",
        "un",
        "une",
        "en",
        "est",
        "pour",
        "par",
        "el",
        "los",
        "las",
        "del",
        "y",
        "que",
        "con",
        "por",
        "para",
    ]
)


# A sentence ends at . ! ? followed by whitespace and an uppercase letter/digit, or at a newline.
# Decimal points ("1,234.56") and abbreviations inside numbers are therefore never split.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9\"“(])|\n+")


def sentences(text: str) -> list[tuple[int, int, str]]:
    """Split into sentences/lines, returning (start, end, sentence) with offsets into ``text``."""
    out = []
    pos = 0
    for m in [*_BOUNDARY_RE.finditer(text), None]:
        end = m.start() if m else len(text)
        seg = text[pos:end]
        stripped = seg.strip()
        if len(stripped) >= 3:
            lead = len(seg) - len(seg.lstrip())
            out.append((pos + lead, pos + lead + len(stripped), stripped))
        if m:
            pos = m.end()
    return out


def stem(word: str) -> str:
    """Crude, language-agnostic stem: a 5-character prefix (governs/governed → gover)."""
    return word[:5] if len(word) > 5 else word


_IDENT_SKIP = frozenset(
    [
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "is",
        "are",
        "was",
        "were",
        "does",
        "do",
        "did",
        "according",
        "the",
        "a",
        "an",
        "in",
        "on",
        "of",
        "for",
        "to",
        "and",
        "or",
        "please",
        "tell",
        "me",
        "list",
        "show",
        "give",
    ]
)
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9&-]*(?:\.[A-Za-z0-9]+)*")


def identifiers(question: str) -> list[str]:
    """Specific things a question names: tokens with digits (INV-20260003, Q3, 2019, W-9)
    and capitalised words that aren't the first word (Zephyr, Okafor, Delaware).
    Generic question words are skipped. Used for entity-constrained retrieval."""
    out: list[str] = []
    for i, m in enumerate(_TOKEN_RE.finditer(question)):
        tok = re.sub(r"'s$", "", m.group(0)).strip(".-")
        if not tok or tok.lower() in _IDENT_SKIP or tok.lower() in STOPWORDS:
            continue
        if re.fullmatch(r"\d+(st|nd|rd|th)", tok.lower()):
            continue  # ordinals ("95th percentile", "1st") describe, they don't name anything
        has_digit = any(ch.isdigit() for ch in tok)
        capitalised = tok[0].isupper() and i > 0
        if (has_digit or capitalised) and len(tok) > 1:
            out.append(tok)
    return list(dict.fromkeys(out))[:6]


def terms(text: str) -> list[str]:
    return [w.lower().strip(".") for w in _WORD_RE.findall(text) if w.lower() not in STOPWORDS and len(w) > 1]


def overlap_score(query_terms: set[str], text: str) -> float:
    """Stem-overlap between query terms and a text, normalised by text length."""
    words = {stem(w) for w in terms(text)}
    if not words or not query_terms:
        return 0.0
    hits = sum(1 for w in words if w in {stem(q) for q in query_terms})
    return hits / math.sqrt(len(words))


def extractive_summary(text: str, max_sentences: int = 3) -> str:
    """Pick the most central sentences (word-frequency scoring), in original order."""
    # Skip table rows (Markdown "|" or mostly numbers): they make poor summaries.
    sents = [
        s
        for _, _, s in sentences(text)
        if 30 <= len(s) <= 400 and "|" not in s and sum(ch.isalpha() for ch in s) / len(s) > 0.6
    ]
    if not sents:
        return text[:300].strip()
    freq = Counter(terms(text))
    scored = []
    for i, s in enumerate(sents[:200]):
        ws = terms(s)
        if ws:
            # Mild lead bias: opening sentences of documents tend to state their purpose.
            scored.append((sum(freq[w] for w in ws) / len(ws) * (1.15 if i < 3 else 1.0), i, s))
    top = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda t: t[1])
    return " ".join(s for _, _, s in top)
