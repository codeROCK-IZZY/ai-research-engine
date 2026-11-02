"""HTML → readable text, plus title and publication date.

Keeps headings (as Markdown ``#`` lines) and paragraph/list text; drops scripts, styles,
navigation, headers, footers and forms, which would otherwise pollute search results.
"""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser

SKIP = {"script", "style", "nav", "header", "footer", "form", "noscript", "svg", "aside", "template"}
BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "blockquote", "pre", "table", "dd", "dt"}
DATE_META = ("article:published_time", "datePublished", "date", "dc.date", "pubdate", "og:published_time")


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.heading: int | None = None
        self.in_title = False
        self.title = ""
        self.published: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop") or ""
            if key in DATE_META and a.get("content") and not self.published:
                self.published = a["content"]
        elif tag == "time" and a.get("datetime") and not self.published:
            self.published = a["datetime"]
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = int(tag[1])
            self.out.append("\n\n" + "#" * self.heading + " ")
        elif tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = None
            self.out.append("\n")
        elif tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> tuple[str, str | None, datetime | None]:
    """Returns (text, title, published_at)."""
    p = _Parser()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    published = None
    if p.published:
        try:
            published = datetime.fromisoformat(p.published.replace("Z", "+00:00"))
        except ValueError:
            published = None
    return text, (p.title.strip() or None), published


def looks_like_html(data: bytes) -> bool:
    head = data[:2048].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or (b"<html" in head and b"</" in data[:65536].lower())
