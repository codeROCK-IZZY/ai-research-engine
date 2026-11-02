"""Connectors: SSRF protections, HTML extraction, ZIP archives, web-source refresh.

Network access is never used: DNS resolution and fetching are monkeypatched."""

import io
import socket
import zipfile
from datetime import UTC, datetime

import pytest

from app.connectors import service as connectors
from app.connectors import web
from app.connectors.web import Fetched, validate_url
from app.core.config import get_settings
from app.core.errors import UnprocessableData
from app.pipeline.html import html_to_text, looks_like_html
from tests.conftest import drain_jobs, make_kb

PAGE = (
    b"<!DOCTYPE html><html><head><title>Expense policy</title>"
    b'<meta property="article:published_time" content="2026-03-01T09:00:00Z"></head><body>'
    b"<nav>Home | Login</nav><script>alert('x')</script><article><h1>Expense policy</h1>"
    b"<p>Expenses up to $500 are approved by your manager.</p></article><footer>(c) Intranet</footer>"
    b"</body></html>"
)


def fake_dns(address: str):
    def _resolve(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return _resolve


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://example.com/x", "Only https"),
        ("https://user:pw@example.com/", "credentials"),
        ("https://example.com:8443/", "standard HTTPS port"),
        ("https://127.0.0.1/", "hostname, not an IP"),
        ("ftp://example.com/", "Only https"),
    ],
)
def test_url_shape_is_validated(url, message, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34"))
    with pytest.raises(UnprocessableData, match=message):
        validate_url(url)


@pytest.mark.parametrize("address", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"])
def test_private_and_metadata_addresses_are_blocked(address, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(address))
    with pytest.raises(UnprocessableData, match="not allowed"):
        validate_url("https://intranet.example.com/")


def test_allow_list_and_pinned_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34"))
    monkeypatch.setattr(get_settings(), "url_allowlist", ["example.com"])
    assert validate_url("https://docs.example.com/a?b=1") == ("docs.example.com", "/a?b=1", "93.184.216.34")
    with pytest.raises(UnprocessableData, match="allow-list"):
        validate_url("https://evil.test/")


def test_html_to_text_keeps_content_drops_chrome():
    text, title, published = html_to_text(PAGE.decode())
    assert title == "Expense policy" and published == datetime(2026, 3, 1, 9, tzinfo=UTC)
    assert "# Expense policy" in text and "$500" in text
    assert "alert" not in text and "Login" not in text and "Intranet" not in text
    assert looks_like_html(PAGE) and not looks_like_html(b"plain text")


def test_add_url_refresh_unchanged_then_updated(client, auth, monkeypatch):
    h = auth("alice")
    kb = make_kb(client, h)
    pages = iter([PAGE, PAGE, PAGE.replace(b"$500", b"$750")])
    monkeypatch.setattr(connectors, "fetch", lambda url: Fetched(url, next(pages), "text/html", None))
    r = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/sources:url",
        headers=h,
        json={"url": "https://intranet.example.com/expenses"},
    )
    assert r.status_code == 202 and r.json()["title"] == "Expense policy"
    did = r.json()["document_id"]
    dup = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/sources:url",
        headers=h,
        json={"url": "https://intranet.example.com/expenses"},
    )
    assert dup.status_code == 409
    drain_jobs()
    d = client.get(f"/api/v1/documents/{did}", headers=h).json()
    assert d["source_kind"] == "url" and d["published_at"].startswith("2026-03-01")
    assert client.post(f"/api/v1/documents/{did}/refresh", headers=h).json()["outcome"] == "unchanged"
    assert client.post(f"/api/v1/documents/{did}/refresh", headers=h).json()["outcome"] == "updated"
    drain_jobs()
    hits = client.post("/api/v1/search", headers=h, json={"query": "expense approval limit manager"}).json()["hits"]
    assert hits and "$750" in hits[0]["text"]


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_archive_adds_supported_files(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    archive = _zip(
        {
            "docs/handbook.md": b"# Handbook\n\nThe on-call rotation is weekly.",
            "docs/page.html": PAGE,
            "docs/notes.txt": b"Plain notes about the deploy pipeline.",
            "docs/image.exe": b"MZ\x90\x00",
            "__MACOSX/._handbook.md": b"junk",
            "../escape.txt": b"path traversal names are harmless: nothing is written to disk",
        }
    )
    r = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/sources:zip", headers=h, files={"file": ("bundle.zip", archive)}
    )
    assert r.status_code == 202, r.text
    out = r.json()
    assert sorted(out["added"]) == ["../escape.txt", "docs/handbook.md", "docs/notes.txt", "docs/page.html"]
    assert out["skipped"] == [{"file": "docs/image.exe", "reason": "unsupported type"}]
    drain_jobs()
    titles = {d["title"] for d in client.get(f"/api/v1/documents?kb_id={kb['id']}", headers=h).json()["items"]}
    assert "Expense policy" in titles and "handbook" in titles


def test_zip_limits(client, auth, monkeypatch):
    h = auth("alice")
    kb = make_kb(client, h)
    monkeypatch.setattr(get_settings(), "zip_max_entries", 2)
    r = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/sources:zip",
        headers=h,
        files={"file": ("x.zip", _zip({f"{i}.txt": b"hello" for i in range(3)}))},
    )
    assert r.status_code == 422 and "more than 2 files" in r.json()["detail"]
    monkeypatch.setattr(get_settings(), "zip_max_entries", 500)
    monkeypatch.setattr(get_settings(), "zip_max_uncompressed_mb", 1)
    bomb = _zip({"big.txt": b"a" * (2 * 1024 * 1024)})
    r = client.post(f"/api/v1/knowledge-bases/{kb['id']}/sources:zip", headers=h, files={"file": ("b.zip", bomb)})
    assert r.status_code == 422 and "expands to more than" in r.json()["detail"]
    r = client.post(f"/api/v1/knowledge-bases/{kb['id']}/sources:zip", headers=h, files={"file": ("n.zip", b"nope")})
    assert r.status_code == 422


def test_refresh_job_counts_outcomes(client, auth, monkeypatch):
    h = auth("alice")
    kb = make_kb(client, h)
    monkeypatch.setattr(connectors, "fetch", lambda url: Fetched(url, PAGE, "text/html", None))
    client.post(f"/api/v1/knowledge-bases/{kb['id']}/sources:url", headers=h, json={"url": "https://a.example/x"})
    drain_jobs()

    def broken(url):
        raise UnprocessableData("The page returned HTTP 503")

    monkeypatch.setattr(connectors, "fetch", broken)
    r = client.post(f"/api/v1/knowledge-bases/{kb['id']}/refresh", headers=h).json()
    assert r["web_sources"] == 1
    drain_jobs()
    from app.db.models import Job
    from app.db.session import new_session

    with new_session() as s:
        job = s.get(Job, r["job_id"])
        assert job is not None and job.result == {"unchanged": 0, "updated": 0, "failed": 1}


def test_fetch_never_used_for_private_hosts(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("10.1.2.3"))
    called = []
    monkeypatch.setattr(web, "_PinnedHTTPSConnection", lambda *a, **k: called.append(a))
    with pytest.raises(UnprocessableData):
        web.fetch("https://internal.example.com/")
    assert called == []
