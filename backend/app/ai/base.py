"""Provider-neutral types for the language-model layer.

The rest of the application talks only to ``LLMProvider``. Two implementations:
``ClaudeProvider`` (Anthropic API, native citations) and ``ExtractiveProvider``
(offline, deterministic: returns cited source sentences instead of generated text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

AnswerStatus = Literal["answered", "not_found", "refused", "error"]

NOT_FOUND_TEXT = "I couldn't find this in the documents you have access to."


@dataclass(frozen=True)
class Passage:
    """A retrieved chunk handed to the model. ``index`` is its position in the prompt."""

    index: int
    chunk_id: int
    document_id: str
    document_title: str
    page_no: int
    text: str


@dataclass
class CitedSentence:
    text: str
    citations: list[tuple[int, str]] = field(default_factory=list)  # (passage index, cited text)


@dataclass
class AnswerResult:
    status: AnswerStatus
    sentences: list[CitedSentence]
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.sentences if s.text.strip())


class LLMProvider(Protocol):
    name: str

    def answer(self, question: str, passages: list[Passage], history: list[tuple[str, str]]) -> AnswerResult: ...

    def rewrite_question(self, question: str, history: list[tuple[str, str]]) -> str: ...

    def summarize(self, text: str) -> tuple[str, str] | None:
        """Return (summary, source) or None when the provider declines/fails."""
        ...

    def classify(self, text: str, labels: tuple[str, ...]) -> tuple[str, float, str] | None: ...

    def extract(self, kind: str, text: str, schema: dict[str, Any]) -> tuple[dict[str, Any], str] | None: ...
