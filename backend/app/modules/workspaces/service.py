"""Workspace membership and knowledge-base access.

Roles (strongest first): ``owner`` > ``editor`` > ``reader``.
* reader — search, ask, read sources
* editor — also add, re-ingest and delete sources
* owner  — also manage members and knowledge bases

A workspace or knowledge base the caller is not a member of is reported as **404**, not
403, so its existence is not revealed. Document-level checks (``documents.service``) read
the ``readable_documents`` view, which derives from the same membership table.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.db.models import KnowledgeBase, User, Workspace, WorkspaceMember

RANK = {"reader": 1, "editor": 2, "owner": 3}


def workspace_role(db: Session, user: User, workspace_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id
        )
    )


def require_workspace(db: Session, user: User, workspace_id: uuid.UUID, need: str = "reader") -> Workspace:
    role = workspace_role(db, user, workspace_id)
    ws = db.get(Workspace, workspace_id) if role else None
    if ws is None:
        raise NotFound("Workspace not found")
    assert role is not None
    if RANK[role] < RANK[need]:
        raise Forbidden(f"Requires the '{need}' role in this workspace")
    return ws


def require_kb(db: Session, user: User, kb_id: uuid.UUID, need: str = "reader") -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or workspace_role(db, user, kb.workspace_id) is None:
        raise NotFound("Knowledge base not found")
    require_workspace(db, user, kb.workspace_id, need)
    return kb


def readable_kb_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(KnowledgeBase.id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == KnowledgeBase.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
        )
    )
