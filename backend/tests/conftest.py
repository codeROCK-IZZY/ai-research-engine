"""Test fixtures. Integration tests run against a real PostgreSQL + pgvector database
(``rke_test``); the schema is rebuilt from the Alembic migration at session start."""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="rke-test-"))
os.environ.setdefault("RKE_DATABASE_URL", "postgresql+psycopg://rke:rke@127.0.0.1:5436/rke_test")
os.environ["RKE_JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["RKE_ENV"] = "test"
os.environ["RKE_COOKIE_SECURE"] = "false"
os.environ["RKE_LOG_JSON"] = "false"
os.environ["RKE_LOG_LEVEL"] = "WARNING"
os.environ["RKE_FILE_DIR"] = str(_tmp / "files")
os.environ["RKE_LLM_PROVIDER"] = "extractive"  # tests never call a paid API
os.environ.pop("ANTHROPIC_API_KEY", None)
# Persistent model cache so embedding / re-ranker models download once, not on every test run.
os.environ.setdefault("RKE_EMBEDDING_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "var" / "models"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine, new_session  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.router import login_limiter  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-Battery-9"
TABLES = (
    "audit_log, jobs, saved_answers, search_history, citations, messages, conversations, minhash_signatures, "
    "entities, extractions, chunks, pages, document_versions, documents, knowledge_bases, workspace_members, "
    "workspaces, refresh_tokens, users"
)


def _alembic(direction: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    (command.downgrade if direction == "down" else command.upgrade)(cfg, "base" if direction == "down" else "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    # Downgrade/upgrade (not DROP SCHEMA) so the superuser-created `vector` extension survives.
    _alembic("down")
    _alembic("up")


def reset_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    login_limiter.reset()


@pytest.fixture
def clean_db() -> None:
    reset_database()


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def create_user(email: str, role: str = "member", password: str = PASSWORD, active: bool = True) -> User:
    with new_session() as s:
        u = User(
            email=email,
            full_name=email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            is_active=active,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> Callable[[str, str], dict[str, str]]:
    """``auth("alice")`` → headers for member alice@example.com (created on first use)."""
    made: dict[str, dict[str, str]] = {}

    def _auth(name: str, role: str = "member") -> dict[str, str]:
        if name not in made:
            create_user(f"{name}@example.com", role)
            made[name] = login(client, f"{name}@example.com")
        return made[name]

    return _auth


def drain_jobs() -> int:
    from app.jobs.worker import drain

    return drain()


def make_kb(client: TestClient, headers: dict[str, str], workspace: str = "Team", kb: str = "Docs") -> dict:
    """Create (or reuse) a workspace owned by the caller and a knowledge base in it."""
    existing = {w["name"]: w for w in client.get("/api/v1/workspaces", headers=headers).json()}
    ws = existing.get(workspace) or client.post("/api/v1/workspaces", headers=headers, json={"name": workspace}).json()
    r = client.post(f"/api/v1/workspaces/{ws['id']}/knowledge-bases", headers=headers, json={"name": kb})
    assert r.status_code == 201, r.text
    return {**r.json(), "workspace": ws}


def add_member(client: TestClient, owner: dict[str, str], workspace_id: str, email: str, role: str) -> None:
    r = client.put(f"/api/v1/workspaces/{workspace_id}/members", headers=owner, json={"email": email, "role": role})
    assert r.status_code == 200, r.text


def upload(client: TestClient, headers: dict[str, str], name: str, data: bytes, kb_id: str, **form: str) -> dict:
    r = client.post("/api/v1/documents", headers=headers, files={"file": (name, data)}, data={"kb_id": kb_id, **form})
    assert r.status_code == 202, r.text
    return r.json()


INVOICE_TXT = (
    "INVOICE INV-7781\nFrom: Example Supplies LLC\nBill To: Test Customer Inc.\nInvoice Number: INV-7781\n"
    "Invoice Date: 2026-03-01\nDue Date: 2026-03-31\nSubtotal: 100.00\nSales Tax (8%): 8.00\nTotal Due: 108.00\n"
    "Payment terms: Net 30.\n"
)
