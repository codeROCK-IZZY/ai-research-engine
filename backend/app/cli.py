"""Command-line tools: ``python -m app.cli <command>``.

migrate                          apply database migrations
create-user --email E --role R   create a user (password from RKE_USER_PASSWORD or prompt)
generate-corpus --out DIR        write the synthetic knowledge corpus + manifest.json
load-demo                        demo users, workspaces, knowledge bases; upload the corpus through the real pipeline
evaluate --out DIR               evaluate retrieval and answers on the loaded demo corpus and write the report
worker                           run the background worker
drain                            process all queued jobs, then exit
demo                             migrate + load-demo + drain (one-command setup)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging

DEMO_USERS = {"erin": "member", "fiona": "member", "admin": "admin"}
CORPUS_DIR = Path(os.environ.get("RKE_CORPUS_DIR", "./var/corpus"))


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    print("migrations applied")


def _create_user(email: str, role: str, password: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = password or os.environ.get("RKE_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} exists — skipped")
            return
        db.add(
            User(
                email=email.lower(),
                full_name=email.split("@")[0].title(),
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"user {email} ({role}) created")


def write_corpus(out: Path) -> dict:
    from app.synthetic.corpus import generate, manifest

    sources, questions = generate()
    out.mkdir(parents=True, exist_ok=True)
    for s in sources:
        (out / s.filename).write_bytes(s.content)
    m = manifest(sources, questions)
    (out / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return m


def load_demo(corpus_dir: Path = CORPUS_DIR) -> dict[str, str]:
    """Users, workspaces (admin owns all; erin and fiona get the roles in the manifest), one knowledge
    base per corpus KB, and every source uploaded through the real pipeline. Returns filename → id."""
    from sqlalchemy import select

    from app.db.models import KnowledgeBase, User, Workspace, WorkspaceMember
    from app.db.session import session_scope
    from app.modules.documents.service import create_document

    password = os.environ.get("RKE_DEMO_PASSWORD")
    if not password:
        sys.exit("set RKE_DEMO_PASSWORD (min 12 chars) for the demo users")
    for name, role in DEMO_USERS.items():
        _create_user(f"{name}@example.com", role, password)
    m = write_corpus(corpus_dir)
    ids: dict[str, str] = {}
    with session_scope() as db:
        users = {u.email.split("@")[0]: u for u in db.scalars(select(User))}
        workspaces: dict[str, Workspace] = {}
        kbs: dict[tuple[str, str], KnowledgeBase] = {}
        for ws_name, members in m["members"].items():
            ws = db.scalar(select(Workspace).where(Workspace.name == ws_name))
            if ws is None:
                ws = Workspace(
                    name=ws_name,
                    description=f"Northwind Labs {ws_name.lower()} knowledge",
                    created_by=users["admin"].id,
                )
                db.add(ws)
                db.flush()
                db.add(WorkspaceMember(workspace_id=ws.id, user_id=users["admin"].id, role="owner"))
                for u, role in members.items():
                    db.add(WorkspaceMember(workspace_id=ws.id, user_id=users[u].id, role=role))
            workspaces[ws_name] = ws
        for s in m["sources"]:
            key = (s["workspace"], s["kb"])
            if key not in kbs:
                kb = db.scalar(
                    select(KnowledgeBase).where(
                        KnowledgeBase.workspace_id == workspaces[key[0]].id, KnowledgeBase.name == key[1]
                    )
                )
                if kb is None:
                    kb = KnowledgeBase(
                        workspace_id=workspaces[key[0]].id,
                        name=key[1],
                        stale_after_days=730 if key[1] == "Policies" else 365,
                    )
                    db.add(kb)
                    db.flush()
                kbs[key] = kb
            published = datetime.fromisoformat(s["published"]).replace(tzinfo=UTC) if s["published"] else None
            doc, _, _ = create_document(
                db,
                users["admin"].id,
                kbs[key].id,
                (corpus_dir / s["filename"]).read_bytes(),
                s["filename"],
                s["title"],
                published_at=published,
            )
            ids[s["filename"]] = str(doc.id)
    (corpus_dir / "loaded_ids.json").write_text(json.dumps(ids), encoding="utf-8")
    print(f"uploaded {len(ids)} sources into {len(kbs)} knowledge bases; processing is queued")
    return ids


def _evaluate(out: Path) -> None:
    import uuid

    from app.db.session import new_session
    from app.evaluation.run import evaluate, write_report

    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    ids = {k: uuid.UUID(v) for k, v in json.loads((CORPUS_DIR / "loaded_ids.json").read_text()).items()}
    db = new_session()
    try:
        report = evaluate(db, manifest, ids)
    finally:
        db.close()
    write_report(report, out)
    print(json.dumps({"retrieval": report["retrieval"], "answers": report["answers"]["overall"]}, indent=1))


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    gc = sub.add_parser("generate-corpus")
    gc.add_argument("--out", type=Path, default=CORPUS_DIR)
    sub.add_parser("load-demo")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../eval/reports"))
    sub.add_parser("worker")
    sub.add_parser("drain")
    sub.add_parser("demo")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-user":
        _create_user(args.email, args.role)
    elif args.cmd == "generate-corpus":
        m = write_corpus(args.out)
        print(f"{len(m['sources'])} sources, {len(m['questions'])} questions → {args.out}")
    elif args.cmd == "load-demo":
        load_demo()
    elif args.cmd == "evaluate":
        _evaluate(args.out)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd in ("drain", "demo"):
        if args.cmd == "demo":
            _migrate()
            load_demo()
        from app.jobs.worker import drain

        print(f"processed {drain()} jobs")


if __name__ == "__main__":
    main()
