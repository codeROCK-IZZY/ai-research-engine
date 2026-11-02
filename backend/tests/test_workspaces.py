"""Workspaces, knowledge bases and access control.

The central guarantee: a source in a knowledge base is reachable — through listing, detail,
pages, download, search, Q&A, citations, comparison or knowledge views — only by members of
the workspace that owns it, and only with the rights of their role."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from app.db.models import DocumentVersion, metadata
from app.db.session import get_engine, new_session
from tests.conftest import INVOICE_TXT, add_member, drain_jobs, make_kb, upload

POLICY = (
    b"Northwind Remote Work Policy\n\nPurpose\nThis policy applies to all employees.\n\nPolicy\n"
    b"Employees may work remotely up to 3 days per week with manager approval.\n"
)
SECRET = (
    b"Project Falcon Budget Memo\n\nThe confidential budget for Project Falcon is 4,750,000 USD, approved by "
    b"the board on 2026-02-14. Do not distribute.\n"
)


def ask(client, h, q, **scope):
    cid = client.post("/api/v1/conversations", headers=h, json=scope).json()["id"]
    return client.post(f"/api/v1/conversations/{cid}/messages", headers=h, json={"question": q}).json()


def test_schema_matches_models(clean_db):
    with get_engine().connect() as conn:
        mc = MigrationContext.configure(
            conn, opts={"include_object": lambda o, n, t, r, c: n != "alembic_version", "compare_type": True}
        )
        assert compare_metadata(mc, metadata) == []


def test_upload_processes_with_indicators(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    doc = upload(client, h, "policy.txt", POLICY, kb["id"], published_at="2026-01-01T00:00:00Z")["document"]
    assert doc["current_version"]["status"] == "queued" and doc["access"] == "owner" and doc["kb_name"] == "Docs"
    assert drain_jobs() == 1
    d = client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()
    assert d["current_version"]["status"] == "ready"
    assert d["freshness"]["basis"] == "published" and d["freshness"]["source"] == "rule-based"
    assert d["reliability"]["level"] in ("high", "medium") and d["reliability"]["source"] == "rule-based"
    assert client.get(f"/api/v1/knowledge-bases/{kb['id']}", headers=h).json()["ready_sources"] == 1


def test_non_members_cannot_see_anything(client, auth):
    alice, bob = auth("alice"), auth("bob")
    secret_kb = make_kb(client, bob, "Finance", "Board")
    sid = upload(client, bob, "secret.txt", SECRET, secret_kb["id"])["document"]["id"]
    own = make_kb(client, alice, "Engineering", "Handbook")
    upload(client, alice, "policy.txt", POLICY, own["id"])
    drain_jobs()
    # Workspaces, knowledge bases and sources: invisible (404, never 403 — existence is not revealed).
    assert [w["name"] for w in client.get("/api/v1/workspaces", headers=alice).json()] == ["Engineering"]
    assert client.get(f"/api/v1/knowledge-bases/{secret_kb['id']}", headers=alice).status_code == 404
    assert client.get(f"/api/v1/workspaces/{secret_kb['workspace']['id']}/members", headers=alice).status_code == 404
    assert client.get("/api/v1/documents", headers=alice).json()["total"] == 1
    for path in (
        f"/documents/{sid}",
        f"/documents/{sid}/pages/1",
        f"/documents/{sid}/file",
        f"/documents/{sid}/chunks",
        f"/knowledge-bases/{secret_kb['id']}/graph",
        f"/knowledge-bases/{secret_kb['id']}/health",
    ):
        assert client.get(f"/api/v1{path}", headers=alice).status_code == 404, path
    assert client.delete(f"/api/v1/documents/{sid}", headers=alice).status_code == 404
    up = client.post(
        "/api/v1/documents", headers=alice, files={"file": ("x.txt", b"hello there")}, data={"kb_id": secret_kb["id"]}
    )
    assert up.status_code == 404
    # Search and Q&A never surface the secret, even when asked directly or scoped to it.
    hits = client.post("/api/v1/search", headers=alice, json={"query": "Project Falcon confidential budget"}).json()
    assert all(x["document_id"] != sid for x in hits["hits"])
    assert (
        client.post("/api/v1/search", headers=alice, json={"query": "budget", "kb_ids": [secret_kb["id"]]}).status_code
        == 404
    )
    ans = ask(client, alice, "What is the budget for Project Falcon?")
    assert ans["status"] == "not_found" and ans["citations"] == []
    assert client.post("/api/v1/conversations", headers=alice, json={"kb_ids": [secret_kb["id"]]}).status_code == 404
    assert client.post("/api/v1/compare", headers=alice, json={"document_ids": [sid, sid]}).status_code == 404
    # Bob, a member, gets a cited answer.
    ans = ask(client, bob, "What is the budget for Project Falcon?")
    assert ans["status"] == "answered" and "4,750,000" in ans["answer"] and ans["citations"][0]["document_id"] == sid


def test_roles_reader_editor_owner(client, auth):
    owner, editor, reader = auth("olive"), auth("ed"), auth("rita")
    kb = make_kb(client, owner)
    ws = kb["workspace"]["id"]
    add_member(client, owner, ws, "ed@example.com", "editor")
    add_member(client, owner, ws, "rita@example.com", "reader")
    # Reader: may read and ask, may not add or delete sources or manage the workspace.
    r = client.post("/api/v1/documents", headers=reader, files={"file": ("p.txt", POLICY)}, data={"kb_id": kb["id"]})
    assert r.status_code == 403
    did = upload(client, editor, "p.txt", POLICY, kb["id"])["document"]["id"]
    drain_jobs()
    assert client.get(f"/api/v1/documents/{did}", headers=reader).json()["access"] == "read"
    assert ask(client, reader, "How many days per week can employees work remotely?")["status"] == "answered"
    assert client.delete(f"/api/v1/documents/{did}", headers=reader).status_code == 403
    assert (
        client.post(f"/api/v1/workspaces/{ws}/knowledge-bases", headers=editor, json={"name": "X"}).status_code == 403
    )
    assert (
        client.put(
            f"/api/v1/workspaces/{ws}/members", headers=editor, json={"email": "rita@example.com", "role": "owner"}
        ).status_code
        == 403
    )
    # The last owner cannot be demoted or removed.
    me = client.get("/api/v1/auth/me", headers=owner).json()
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{me['id']}", headers=owner).status_code == 422
    # Removing a member immediately removes access, including old citations in history.
    cid = client.post("/api/v1/conversations", headers=reader, json={}).json()["id"]
    client.post(
        f"/api/v1/conversations/{cid}/messages",
        headers=reader,
        json={"question": "How many days per week can employees work remotely?"},
    )
    rita = next(
        m
        for m in client.get(f"/api/v1/workspaces/{ws}/members", headers=owner).json()
        if m["email"] == "rita@example.com"
    )
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{rita['user_id']}", headers=owner).status_code == 204
    assert client.get(f"/api/v1/documents/{did}", headers=reader).status_code == 404
    hist = client.get(f"/api/v1/conversations/{cid}/messages", headers=reader).json()
    assert all(m["citations"] == [] for m in hist)
    # Editors may delete sources.
    assert client.delete(f"/api/v1/documents/{did}", headers=editor).status_code == 204


def test_versions_diff_and_current_version_only(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    did = upload(client, h, "policy.txt", POLICY, kb["id"])["document"]["id"]
    r = client.post(
        f"/api/v1/documents/{did}/versions",
        headers=h,
        files={"file": ("policy-v2.txt", POLICY.replace(b"3 days", b"4 days"))},
    )
    assert r.status_code == 202
    drain_jobs()
    d = client.get(f"/api/v1/documents/{did}", headers=h).json()
    assert [v["version_no"] for v in d["versions"]] == [2, 1]
    diff = client.get(f"/api/v1/documents/{did}/diff?base=1&other=2", headers=h).json()
    assert diff["added"] == 1 and diff["removed"] == 1
    hits = client.post("/api/v1/search", headers=h, json={"query": "remote work days per week"}).json()["hits"]
    assert hits and all("3 days" not in x["text"] for x in hits)


def test_download_headers_delete_and_kb_delete(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    did = upload(client, h, "inv<script>.txt", INVOICE_TXT.encode(), kb["id"])["document"]["id"]
    r = client.get(f"/api/v1/documents/{did}/file", headers=h)
    assert r.content == INVOICE_TXT.encode()
    assert r.headers["content-disposition"] == 'attachment; filename="invscript.txt"'
    assert r.headers["x-content-type-options"] == "nosniff"
    upload(client, h, "p.txt", POLICY, kb["id"])
    assert client.delete(f"/api/v1/knowledge-bases/{kb['id']}", headers=h).status_code == 204
    with new_session() as s:
        assert s.scalar(select(DocumentVersion)) is None


def test_duplicates_are_per_knowledge_base(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    other = make_kb(client, h, kb="Other")
    first = upload(client, h, "a.txt", INVOICE_TXT.encode(), kb["id"])
    second = upload(client, h, "b.txt", INVOICE_TXT.encode(), kb["id"])
    elsewhere = upload(client, h, "c.txt", INVOICE_TXT.encode(), other["id"])
    assert first["duplicate_of"] is None and second["duplicate_of"] == first["document"]["id"]
    assert elsewhere["duplicate_of"] is None  # same bytes in another knowledge base are not a duplicate
    near = INVOICE_TXT.replace("Payment terms: Net 30.", "Payment terms: Net 30. Thank you!")
    upload(client, h, "d.txt", near.encode(), kb["id"])
    drain_jobs()
    dup = client.get(f"/api/v1/knowledge-bases/{kb['id']}/duplicates", headers=h).json()
    assert len(dup["exact"]) == 1 and len(dup["exact"][0]) == 2
    assert dup["near"] and dup["near"][0]["similarity"] >= 0.8


def test_validation_and_auth(client, auth):
    h = auth("alice")
    kb = make_kb(client, h)
    r = client.post(
        "/api/v1/documents",
        headers=h,
        files={"file": ("x.pdf", b"MZ\x90\x00 not a pdf \x00\x01")},
        data={"kb_id": kb["id"]},
    )
    assert r.status_code == 422 and "Unsupported" in r.json()["detail"]
    assert client.get("/api/v1/documents").status_code == 401
    assert client.post("/api/v1/workspaces", headers=auth("val", "viewer"), json={"name": "V"}).status_code == 403
    assert client.post("/api/v1/workspaces", headers=h, json={"name": "Team"}).status_code == 409
