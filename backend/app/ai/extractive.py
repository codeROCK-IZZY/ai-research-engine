"""Offline provider: no network, fully deterministic.

``answer`` does not generate text. It selects the source sentences that best answer
the question from the top retrieved passages and returns them verbatim, each cited.
That makes every answer trivially grounded, at the cost of fluency.

Sentence choice is lexical (does the sentence contain what the question asks about?),
with three rules measured on the evaluation corpus:
* headings and title lines are never quoted as answers (they label, they don't answer);
* questions asking for a value ("how long", "how much", "what effect", "how quickly")
  strongly prefer sentences that contain a number;
* the cross-encoder adds a small tie-breaker. It is not the main signal: the MiniLM
  re-ranker is trained on passages and is poorly calibrated on single sentences.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.ai.base import AnswerResult, CitedSentence, Passage
from app.ai.textutil import extractive_summary, identifiers, overlap_score, sentences, terms
from app.core.config import get_settings

# Words that appear in many questions but say little about which sentence answers them.
FOLLOW_UP_PRONOUNS = frozenset({"it", "its", "they", "them", "their", "that", "this", "those", "these", "he", "she"})
GENERIC = frozenset({"document", "according", "mentioned", "key", "limit", "requirement", "letter", "invoice"})


def is_heading(sentence: str) -> bool:
    """Markdown headings, and short label-like lines: no sentence punctuation and no number.
    A short line *with* a number ("Total Due: 108.00") is a value, so it stays a candidate."""
    t = sentence.strip()
    if t.startswith("#"):
        return True
    return not re.search(r"[.!?]$", t) and not re.search(r"\d", t) and len(t.split()) <= 10


class ExtractiveProvider:
    name = "extractive"

    def answer(self, question: str, passages: list[Passage], history: list[tuple[str, str]]) -> AnswerResult:
        """Pick the source sentences that best answer the question.

        Scoring favours sentences that match what the question *asks about* ("notice",
        "total due", "governs") over sentences that merely repeat the entities it names
        (those appear in headings everywhere). Questions asking for a quantity or a date
        prefer sentences that contain digits."""
        idents = {i.lower() for i in identifiers(question)}
        content = {t for t in terms(question) if t not in idents and t not in GENERIC}
        wants_value = bool(
            re.search(
                r"\b(how many|how much|how long|how quickly|how fast|by how much|what effect|when|"
                r"what (is|was|are) the|total|date|amount|days)\b",
                question,
                re.IGNORECASE,
            )
        )
        candidates: list[tuple[float, int, Passage, str]] = []
        for rank, p in enumerate(passages[:4]):
            for _, _, sent in sentences(p.text):
                if is_heading(sent):
                    continue
                score = 2.0 * overlap_score(content, sent) + 0.5 * overlap_score(idents, sent)
                if wants_value and re.search(r"\d", sent):
                    score += 1.0
                score *= 1.0 - 0.08 * rank  # prefer higher-ranked passages
                if score > 0:
                    candidates.append((score, rank, p, sent))
        if get_settings().rerank_answer_sentences:
            candidates = self._rerank(question, passages[:4], candidates)
        candidates.sort(key=lambda c: (-c[0], c[1]))
        chosen: list[CitedSentence] = []
        for score, _, p, sent in candidates:
            if any(c.text == sent for c in chosen):
                continue
            if chosen and score < 0.6 * candidates[0][0]:
                break
            chosen.append(CitedSentence(text=sent, citations=[(p.index, sent)]))
            if len(chosen) == 2:
                break
        if not chosen:
            return AnswerResult(status="not_found", sentences=[], provider=self.name)
        return AnswerResult(status="answered", sentences=chosen, provider=self.name)

    @staticmethod
    def _rerank(
        question: str, passages: list[Passage], lexical: list[tuple[float, int, Passage, str]]
    ) -> list[tuple[float, int, Passage, str]]:
        """Add the cross-encoder's relevance as a small tie-breaker (0.3 × probability)."""
        from app.ai.providers import get_reranker

        if not lexical:
            return lexical
        try:
            raw = get_reranker().scores(question, [sent for _, _, _, sent in lexical])
        except Exception:
            return lexical
        return [
            (score + 0.3 / (1 + math.exp(-x)), rank, p, sent)
            for (score, rank, p, sent), x in zip(lexical, raw, strict=True)
        ]

    def rewrite_question(self, question: str, history: list[tuple[str, str]]) -> str:
        """Offline follow-up handling. Only questions that *refer back* ("and when does it
        expire?", "what about that contract?") borrow key terms from the previous question;
        a self-contained question ("Who is the CEO of Zephyr Dynamics?") is left alone."""
        if not history:
            return question
        words = re.findall(r"[a-z']+", question.lower())
        refers_back = bool(words) and (
            words[0] in {"and", "also"}
            or " ".join(words[:2]) == "what about"
            or any(w in FOLLOW_UP_PRONOUNS for w in words)
        )
        if not refers_back or identifiers(question):
            return question
        prev_user = next((t for role, t in reversed(history) if role == "user"), "")
        return f"{question} {' '.join(identifiers(prev_user) or terms(prev_user)[:6])}"

    def summarize(self, text: str) -> tuple[str, str] | None:
        return extractive_summary(text), "rule:extractive-summary"

    def classify(self, text: str, labels: tuple[str, ...]) -> tuple[str, float, str] | None:
        return None  # the rule-based classifier in the pipeline is used instead

    def extract(self, kind: str, text: str, schema: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        return None  # rule-based extraction in the pipeline is used instead
