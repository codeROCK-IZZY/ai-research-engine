"""Knowledge features: freshness/reliability indicators, cross-source conflicts, topics,
concept graph, re-ranking, search history and saved answers."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.knowledge.indicators import SourceFacts, freshness, reliability
from app.knowledge.synthesis import compare_sources
from app.knowledge.topics import choose_k, kmeans, label_topics
from app.retrieval import search as search_mod
from tests.conftest import add_member, drain_jobs, make_kb, upload

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def facts(**kw):
    base = {
        "source_kind": "upload",
        "source_url": None,
        "published_at": NOW - timedelta(days=10),
        "fetched_at": None,
        "created_at": NOW - timedelta(days=5),
        "status": "ready",
        "char_count": 5000,
        "min_ocr_confidence": None,
    }
    return SourceFacts(**{**base, **kw})


def test_freshness_uses_publication_date_and_threshold():
    f = freshness(facts(published_at=NOW - timedelta(days=400)), stale_after_days=365, now=NOW)
    assert f["basis"] == "published" and f["age_days"] == 400 and f["stale"]
    f = freshness(facts(published_at=None), stale_after_days=365, now=NOW)
    assert f["basis"] == "added" and f["age_days"] == 5 and not f["stale"]


def test_reliability_levels_and_reasons():
    assert reliability(facts(), stale=False)["level"] == "high"
    low = reliability(
        facts(published_at=None, source_kind="url", source_url="http://x", min_ocr_confidence=40, char_count=50),
        stale=True,
    )
    assert low["level"] == "low" and any("plain HTTP" in r for r in low["reasons"])
    assert low["source"] == "rule-based"


def test_conflicting_quantities_between_sources_are_flagged():
    cites = [
        {
            "document_id": "a",
            "document_title": "Refund policy (current)",
            "cited_text": "a refund within 14 days of purchase",
        },
        {
            "document_id": "b",
            "document_title": "Refund policy (2022)",
            "cited_text": "a refund within 30 days of purchase",
        },
    ]
    r = compare_sources("Within how many days can customers request a refund?", cites)
    assert r["agreement"] == "conflict"
    vals = {v["title"]: v["values"] for v in r["conflicts"][0]["values"]}
    assert vals == {"Refund policy (current)": ["14 day"], "Refund policy (2022)": ["30 day"]}
    same = [{**c, "cited_text": "a refund within 14 days"} for c in cites]
    assert compare_sources("refund window in days", same)["agreement"] == "consistent"
    assert compare_sources("x", cites[:1])["agreement"] == "single_source"


def test_kmeans_separates_clear_clusters_and_labels():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.05, (6, 4)) + np.array([1, 0, 0, 0])
    b = rng.normal(0, 0.05, (6, 4)) + np.array([0, 1, 0, 0])
    x = np.vstack([a, b])
    labels = kmeans(x, 2)
    assert len(set(labels[:6])) == 1 and len(set(labels[6:])) == 1 and labels[0] != labels[6]
    assert choose_k(4) == 2 and choose_k(200) == 8
    names = label_topics({0: "kafka streaming consumer kafka offsets", 1: "parental leave policy weeks leave"})
    assert "kafka" in names[0] and "leave" in names[1]


SERVICE_DOCS = {
    f"svc-{name}.md": (
        f"# {name.title()} Service\n\nThe {name.title()} Service is owned by Team Falcon. It uses PostgreSQL and "
        f"follows the Incident Response Process with a weekly on-call rotation. SLA 99.9%."
    ).encode()
    for name in ("payments", "ledger", "billing", "identity")
}
POLICY_DOCS = {
    f"policy-{n}.md": (
        f"# {n.title()} policy\n\nEmployees receive {i + 2} weeks of {n} leave per year as described "
        f"by Human Resources. Requests go through the HR Portal."
    ).encode()
    for i, n in enumerate(("parental", "sick", "study", "volunteer"))
}


def _load(client, h):
    kb = make_kb(client, h)
    for name, data in {**SERVICE_DOCS, **POLICY_DOCS}.items():
        upload(client, h, name, data, kb["id"])
    drain_jobs()
    return kb


def test_topics_graph_and_health_endpoints(client, auth):
    h = auth("alice")
    kb = _load(client, h)
    t = client.get(f"/api/v1/knowledge-bases/{kb['id']}/topics", headers=h).json()
    groups = [sorted(s["title"] for s in topic["sources"]) for topic in t["topics"]]
    assert t["k"] == 2 and sorted(groups) == sorted(
        [sorted(n[:-3] for n in SERVICE_DOCS), sorted(n[:-3] for n in POLICY_DOCS)]
    )
    g = client.get(f"/api/v1/knowledge-bases/{kb['id']}/graph", headers=h).json()
    ids = {n["id"] for n in g["nodes"]}
    assert {"team falcon", "incident response process", "sla"} <= ids
    assert any({e["source"], e["target"]} == {"incident response process", "team falcon"} for e in g["edges"])
    health = client.get(f"/api/v1/knowledge-bases/{kb['id']}/health", headers=h).json()
    assert health["sources"] == 8 and health["source"] == "rule-based"


def test_reranking_reorders_and_reports_scores(client, auth, monkeypatch):
    h = auth("alice")
    _load(client, h)

    class FakeReranker:
        def scores(self, query, passages):
            return [5.0 if "sick" in p else -5.0 for p in passages]

    monkeypatch.setattr(search_mod, "get_reranker", lambda: FakeReranker())
    body = {"query": "how many weeks of leave", "limit": 3}
    plain = client.post("/api/v1/search", headers=h, json={**body, "rerank": False}).json()
    ranked = client.post("/api/v1/search", headers=h, json={**body, "rerank": True}).json()
    assert plain["reranked"] is False and plain["hits"][0]["rerank_score"] is None
    assert ranked["hits"][0]["document_title"] == "policy-sick" and ranked["hits"][0]["rerank_score"] > 0.99
    history = client.get("/api/v1/search/history", headers=h).json()
    assert [x["reranked"] for x in history[:2]] == [True, False] and history[0]["results"] == 3


def test_saved_answers_shared_in_workspace(client, auth):
    h, reader = auth("alice"), auth("rita")
    kb = _load(client, h)
    ws = kb["workspace"]["id"]
    add_member(client, h, ws, "rita@example.com", "reader")
    cid = client.post("/api/v1/conversations", headers=h, json={"kb_ids": [kb["id"]]}).json()["id"]
    ans = client.post(
        f"/api/v1/conversations/{cid}/messages",
        headers=h,
        json={"question": "How many weeks of sick leave do employees receive?"},
    ).json()
    assert ans["status"] == "answered" and ans["synthesis"]["source"] == "rule-based"
    msg_id = client.get(f"/api/v1/conversations/{cid}/messages", headers=h).json()[-1]["id"]
    assert (
        client.post(
            f"/api/v1/workspaces/{ws}/saved-answers", headers=h, json={"message_id": msg_id, "note": "HR confirmed"}
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/workspaces/{ws}/saved-answers", headers=h, json={"message_id": msg_id}).status_code == 409
    )
    assert (
        client.post(f"/api/v1/workspaces/{ws}/saved-answers", headers=reader, json={"message_id": msg_id}).status_code
        == 403
    )
    saved = client.get(f"/api/v1/workspaces/{ws}/saved-answers", headers=reader).json()
    assert saved[0]["note"] == "HR confirmed" and "sick" in saved[0]["question"]
