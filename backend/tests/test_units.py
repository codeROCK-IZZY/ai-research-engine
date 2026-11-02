"""Unit tests: validation, text processing, understanding, providers (no database)."""

import io
import itertools
import zipfile
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.ai.base import Passage
from app.ai.claude import ClaudeProvider
from app.ai.extractive import ExtractiveProvider
from app.ai.textutil import identifiers, sentences
from app.core.errors import UnprocessableData
from app.pipeline import understand as u
from app.pipeline.extract import extract
from app.pipeline.text import chunk_page, detect_language, normalize
from app.pipeline.validate import MIME_DOCX, MIME_PDF, MIME_PNG, MIME_TXT, sniff, validate_upload
from app.synthetic.corpus import _pdf as _corpus_pdf
from tests.conftest import INVOICE_TXT


def _pdf(rows: list[list[str]]) -> bytes:
    return _corpus_pdf("Test", [" ".join(r) for r in rows])


def _docx(paragraphs: list[str]) -> bytes:
    import docx

    d = docx.Document()
    for t in paragraphs:
        d.add_paragraph(t)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- validation
def test_sniff_by_content_not_name():
    assert sniff(_pdf([["Hello"]])).mime == MIME_PDF
    assert sniff(_docx(["Hello"])).mime == MIME_DOCX
    assert sniff(b"plain text").mime == MIME_TXT
    assert sniff(b"\x89PNG\r\n\x1a\n....").mime == MIME_PNG
    with pytest.raises(UnprocessableData):
        sniff(b"MZ\x90\x00\x03\x00\x00\x00 windows executable")  # a .pdf name wouldn't matter
    with pytest.raises(UnprocessableData):
        sniff(b"\xff\xfe\x00\x00binary\x00garbage")


def test_zip_that_is_not_docx_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("evil.sh", "rm -rf /")
    with pytest.raises(UnprocessableData, match="Word"):
        sniff(buf.getvalue())


def test_docx_zip_bomb_rejected(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_docx_uncompressed_mb", 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "A" * 3_000_000)  # compresses to a few KB
    assert len(buf.getvalue()) < 50_000
    with pytest.raises(UnprocessableData, match="expands"):
        validate_upload(buf.getvalue())


def test_size_and_empty_limits(monkeypatch):
    from app.core.config import get_settings

    with pytest.raises(UnprocessableData):
        validate_upload(b"")
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    with pytest.raises(UnprocessableData, match="limit"):
        validate_upload(b"x" * 10)


# ---------------------------------------------------------------- text
def test_sentences_keep_decimals_and_offsets():
    text = "Subtotal: 1,234.56\nTotal Due: 1,333.32. Thanks for your business! Pay by 2026-03-31."
    sents = sentences(text)
    assert [s for _, _, s in sents][:2] == ["Subtotal: 1,234.56", "Total Due: 1,333.32."]
    for start, end, s in sents:
        assert text[start:end] == s


def test_normalize_rejoins_hyphenation():
    assert normalize("termi-\nnation   notice\r\n\n\n\nend") == "termination notice\n\nend"


def test_chunks_cover_page_with_exact_offsets():
    page = " ".join(f"Sentence number {i} talks about clause {i} and its effects." for i in range(200))
    chunks = chunk_page(3, page, target=400, overlap=60)
    assert len(chunks) > 5
    for c in chunks:
        assert c.page_no == 3 and page[c.start : c.end] == c.text and len(c.text) <= 400
    assert chunks[0].start == 0 and chunks[-1].end == len(page)
    for a, b in itertools.pairwise(chunks):
        assert b.start <= a.end  # contiguous coverage (with overlap), never a gap


def test_language_detection():
    assert detect_language("Nous vous remercions pour votre commande. Votre livraison arrivera demain à Lyon.") == "fr"
    assert detect_language("Gracias por su pedido. Su envío llegará a nuestro almacén de Sevilla mañana.") == "es"
    assert detect_language("short") is None


def test_identifiers():
    assert identifiers("What is the total due on invoice INV-20260003?") == ["INV-20260003"]
    assert identifiers("Who is the account manager in the letter to Maya Okafor?") == ["Maya", "Okafor"]
    assert identifiers("How many days per week may employees work remotely?") == []


# ---------------------------------------------------------------- understanding
def test_invoice_rules_and_validation():
    pages = extract(INVOICE_TXT.encode(), MIME_TXT)
    f = u.extract_invoice_rules(pages[0].text, [])
    assert (f["invoice_number"], f["total"], f["tax"], f["due_date"]) == ("INV-7781", 108.0, 8.0, "2026-03-31")
    assert u.validate_invoice(f)["valid"]
    bad = {**f, "total": 120.0, "due_date": "2026-02-01"}
    v = u.validate_invoice(bad)
    assert not v["valid"] and len(v["issues"]) == 2


def test_docx_page_breaks_split_pages():
    from docx import Document
    from docx.enum.text import WD_BREAK

    d = Document()
    d.add_paragraph("First page text.")
    d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    d.add_paragraph("Second page text.")
    buf = io.BytesIO()
    d.save(buf)
    pages = extract(buf.getvalue(), MIME_DOCX)
    assert [p.text for p in pages] == ["First page text.", "Second page text."]


def test_classification_rules():
    assert u.classify_rules(INVOICE_TXT)[0] == "invoice"
    assert u.classify_rules("Dear Sam,\n\nThank you.\n\nSincerely,\nPat")[0] == "letter"
    assert u.classify_rules("Lorem ipsum dolor sit amet")[0] == "other"


def test_minhash_similarity():
    # Varied text (a repeated sentence has few distinct shingles and would distort the check).
    base = " ".join(f"clause {i} covers obligation {i * 7 % 13} for party {i % 5}." for i in range(120))
    assert u.jaccard_estimate(u.minhash(base), u.minhash(base + " One extra closing sentence here.")) > 0.8
    assert u.jaccard_estimate(u.minhash(base), u.minhash("Invoice total due thirty days net payment " * 20)) < 0.1


def test_entities():
    ents = u.extract_entities(
        [(1, "Contact billing@acme.example by March 5, 2026 about $1,250.00 owed to Acme Robotics Inc.")]
    )
    kinds = {e["entity_type"]: e["normalized"] for e in ents}
    assert kinds["date"] == "2026-03-05" and kinds["money"] == "USD 1250.00"
    assert kinds["email"] == "billing@acme.example" and "Acme Robotics Inc" in kinds["organization"]


# ---------------------------------------------------------------- extractive provider
P = [
    Passage(0, 11, "d1", "Invoice INV-1", 1, "INVOICE INV-1\nInvoice Date: 2026-01-01\nTotal Due: 1,234.56\nThanks."),
    Passage(1, 12, "d2", "Policy", 1, "Employees may work remotely up to 3 days per week with approval."),
]


def test_extractive_answer_cites_source_sentence():
    r = ExtractiveProvider().answer("What is the total due on invoice INV-1?", P, [])
    assert r.status == "answered" and "1,234.56" in r.text
    assert r.sentences[0].citations == [(0, "Total Due: 1,234.56")]


def test_extractive_follow_up_rewrite():
    hist = [("user", "What is the notice period in the Acme contract?"), ("assistant", "60 days.")]
    assert "acme" in ExtractiveProvider().rewrite_question("and when does it start?", hist).lower()


# ---------------------------------------------------------------- Claude provider (fake client, no network)
class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response, self.error = response, error
        create = self._create
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def _resp(blocks, stop="end_turn"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )


def _text(t, cites=()):
    return SimpleNamespace(
        type="text",
        text=t,
        citations=[SimpleNamespace(type="char_location", document_index=i, cited_text=c) for i, c in cites] or None,
    )


def test_claude_answer_maps_native_citations():
    fake = FakeClient(_resp([_text("The total due is "), _text("$1,234.56.", [(0, "Total Due: 1,234.56")])]))
    r = ClaudeProvider(client=fake).answer("total?", P, [])
    assert r.status == "answered" and r.text == "The total due is $1,234.56."
    assert r.sentences[1].citations == [(0, "Total Due: 1,234.56")] and r.usage["input_tokens"] == 100
    sent = fake.calls[0]
    docs = [c for c in sent["messages"][0]["content"] if c["type"] == "document"]
    assert len(docs) == 2 and all(d["citations"] == {"enabled": True} for d in docs)
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "untrusted data" in sent["system"]


def test_claude_not_found_refusal_and_errors():
    assert ClaudeProvider(client=FakeClient(_resp([_text("NOT_FOUND")]))).answer("q", P, []).status == "not_found"
    assert ClaudeProvider(client=FakeClient(_resp([], stop="refusal"))).answer("q", P, []).status == "refused"
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    assert ClaudeProvider(client=FakeClient(error=err)).answer("q", P, []).status == "error"


def test_claude_structured_extraction():
    fake = FakeClient(_resp([_text('{"label": "invoice", "confidence": 0.93}')]))
    assert ClaudeProvider(client=fake).classify("text", ("invoice", "other")) == (
        "invoice",
        0.93,
        "claude:claude-opus-5-5",
    )
    assert fake.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_follow_up_rewrite_only_when_question_refers_back():
    hist = [("user", "What is the total due on invoice INV-20260003?"), ("assistant", "Total Due: 1.00")]
    p = ExtractiveProvider()
    assert p.rewrite_question("Who is the CEO of Zephyr Dynamics?", hist) == "Who is the CEO of Zephyr Dynamics?"
    assert p.rewrite_question("and when is it due?", hist).endswith("INV-20260003")


def test_summary_skips_table_rows():
    from app.ai.textutil import extractive_summary

    text = (
        "This invoice covers consulting services delivered to Acme Robotics in February.\n"
        "| Consulting hours | 9 | 125.00 | 1,125.00 |\nConsulting hours 9 125.00 1,125.00 450.00 1,393.00\n"
        "Payment is due within sixty days of the invoice date shown above."
    )
    s = extractive_summary(text)
    assert "|" not in s and "1,125.00" not in s and "consulting services" in s


def test_organisation_entities_do_not_span_lines():
    ents = u.extract_entities(
        [(1, "Description Qty Unit Price Amount\nConsulting hours 9\nFrom: Granite Analytics Ltd.")]
    )
    assert [e["value"] for e in ents if e["entity_type"] == "organization"] == ["Granite Analytics Ltd."]
