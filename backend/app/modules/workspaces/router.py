"""Workspaces, membership and knowledge bases."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Member, Viewer
from app.core.errors import Conflict, NotFound, UnprocessableData
from app.db.models import (
    Conversation,
    Document,
    DocumentVersion,
    KnowledgeBase,
    Message,
    SavedAnswer,
    User,
    Workspace,
    WorkspaceMember,
)
from app.modules import audit
from app.modules.workspaces.service import require_kb, require_workspace, workspace_role

router = APIRouter(tags=["workspaces"])

Role = Literal["owner", "editor", "reader"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    role: str
    members: int
    knowledge_bases: int
    created_at: datetime


class MemberIn(BaseModel):
    email: EmailStr
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    stale_after_days: int = Field(default=365, ge=1, le=3650)


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str
    name: str
    description: str
    stale_after_days: int
    sources: int
    ready_sources: int
    role: str
    created_at: datetime


def _ws_out(db: DB, ws: Workspace, role: str) -> WorkspaceOut:
    members = db.scalar(select(func.count()).where(WorkspaceMember.workspace_id == ws.id)) or 0
    kbs = db.scalar(select(func.count()).where(KnowledgeBase.workspace_id == ws.id)) or 0
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        description=ws.description,
        role=role,
        members=members,
        knowledge_bases=kbs,
        created_at=ws.created_at,
    )


def _kb_out(db: DB, kb: KnowledgeBase, ws: Workspace, role: str) -> KnowledgeBaseOut:
    total = db.scalar(select(func.count()).where(Document.kb_id == kb.id)) or 0
    ready = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            .where(Document.kb_id == kb.id, DocumentVersion.status == "ready")
        )
        or 0
    )
    return KnowledgeBaseOut(
        id=kb.id,
        workspace_id=ws.id,
        workspace_name=ws.name,
        name=kb.name,
        description=kb.description,
        stale_after_days=kb.stale_after_days,
        sources=total,
        ready_sources=ready,
        role=role,
        created_at=kb.created_at,
    )


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(db: DB, user: Viewer) -> list[WorkspaceOut]:
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.name)
    ).all()
    return [_ws_out(db, ws, role) for ws, role in rows]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn, db: DB, user: Member) -> WorkspaceOut:
    ws = Workspace(name=body.name.strip(), description=body.description, created_by=user.id)
    db.add(ws)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A workspace with this name already exists") from exc
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    audit.record(db, user.id, "workspace.create", "workspace", str(ws.id), name=ws.name)
    db.commit()
    return _ws_out(db, ws, "owner")


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: uuid.UUID, db: DB, user: Member) -> None:
    ws = require_workspace(db, user, workspace_id, "owner")
    audit.record(db, user.id, "workspace.delete", "workspace", str(ws.id), name=ws.name)
    db.delete(ws)
    db.commit()


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_members(workspace_id: uuid.UUID, db: DB, user: Viewer) -> list[MemberOut]:
    require_workspace(db, user, workspace_id)
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(User.email)
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=m.role) for m, u in rows]


@router.put("/workspaces/{workspace_id}/members", response_model=MemberOut)
def upsert_member(workspace_id: uuid.UUID, body: MemberIn, db: DB, user: Member) -> MemberOut:
    """Add a member or change their role (owners only)."""
    require_workspace(db, user, workspace_id, "owner")
    target = db.scalar(select(User).where(User.email == body.email.lower(), User.is_active))
    if target is None:
        raise NotFound(f"No active user with e-mail {body.email}")
    m = db.get(WorkspaceMember, (workspace_id, target.id))
    if m is not None and m.role == "owner" and body.role != "owner" and _owner_count(db, workspace_id) == 1:
        raise UnprocessableData("A workspace must keep at least one owner")
    if m is None:
        m = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=body.role)
        db.add(m)
    else:
        m.role = body.role
    audit.record(db, user.id, "workspace.member", "workspace", str(workspace_id), email=target.email, role=body.role)
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(workspace_id: uuid.UUID, user_id: uuid.UUID, db: DB, user: Member) -> None:
    if user_id != user.id:  # anyone may leave; removing others needs owner
        require_workspace(db, user, workspace_id, "owner")
    m = db.get(WorkspaceMember, (workspace_id, user_id))
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner" and _owner_count(db, workspace_id) == 1:
        raise UnprocessableData("A workspace must keep at least one owner")
    db.delete(m)
    audit.record(db, user.id, "workspace.member_remove", "workspace", str(workspace_id), member_id=str(user_id))
    db.commit()


def _owner_count(db: DB, workspace_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
        )
        or 0
    )


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(db: DB, user: Viewer, workspace_id: uuid.UUID | None = None) -> list[KnowledgeBaseOut]:
    stmt = (
        select(KnowledgeBase, Workspace, WorkspaceMember.role)
        .join(Workspace, Workspace.id == KnowledgeBase.workspace_id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
    )
    if workspace_id:
        stmt = stmt.where(Workspace.id == workspace_id)
    rows = db.execute(stmt.order_by(Workspace.name, KnowledgeBase.name)).all()
    return [_kb_out(db, kb, ws, role) for kb, ws, role in rows]


@router.post("/workspaces/{workspace_id}/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201)
def create_knowledge_base(workspace_id: uuid.UUID, body: KnowledgeBaseIn, db: DB, user: Member) -> KnowledgeBaseOut:
    ws = require_workspace(db, user, workspace_id, "owner")
    kb = KnowledgeBase(
        workspace_id=ws.id, name=body.name.strip(), description=body.description, stale_after_days=body.stale_after_days
    )
    db.add(kb)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("This workspace already has a knowledge base with that name") from exc
    audit.record(db, user.id, "kb.create", "knowledge_base", str(kb.id), name=kb.name)
    db.commit()
    return _kb_out(db, kb, ws, "owner")


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
def get_knowledge_base(kb_id: uuid.UUID, db: DB, user: Viewer) -> KnowledgeBaseOut:
    kb = require_kb(db, user, kb_id)
    ws = db.get(Workspace, kb.workspace_id)
    assert ws is not None
    return _kb_out(db, kb, ws, workspace_role(db, user, ws.id) or "reader")


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
def update_knowledge_base(kb_id: uuid.UUID, body: KnowledgeBaseIn, db: DB, user: Member) -> KnowledgeBaseOut:
    kb = require_kb(db, user, kb_id, "owner")
    kb.name, kb.description, kb.stale_after_days = body.name.strip(), body.description, body.stale_after_days
    db.commit()
    return get_knowledge_base(kb_id, db, user)


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: uuid.UUID, db: DB, user: Member) -> None:
    kb = require_kb(db, user, kb_id, "owner")
    audit.record(db, user.id, "kb.delete", "knowledge_base", str(kb.id), name=kb.name)
    db.delete(kb)
    db.commit()


class SavedAnswerIn(BaseModel):
    message_id: uuid.UUID
    note: str = Field(default="", max_length=2000)


@router.post("/workspaces/{workspace_id}/saved-answers", status_code=201)
def save_answer(workspace_id: uuid.UUID, body: SavedAnswerIn, db: DB, user: Member) -> dict[str, str]:
    """Pin one of your answers to a workspace so colleagues can reuse it (editors and owners)."""
    require_workspace(db, user, workspace_id, "editor")
    msg = db.get(Message, body.message_id)
    conv = db.get(Conversation, msg.conversation_id) if msg else None
    if msg is None or conv is None or conv.user_id != user.id or msg.role != "assistant":
        raise NotFound("Answer not found")
    db.add(SavedAnswer(workspace_id=workspace_id, message_id=msg.id, saved_by=user.id, note=body.note))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("This answer is already saved in the workspace") from exc
    return {"status": "saved"}


@router.get("/workspaces/{workspace_id}/saved-answers")
def list_saved_answers(workspace_id: uuid.UUID, db: DB, user: Viewer) -> list[dict[str, object]]:
    require_workspace(db, user, workspace_id)
    rows = db.execute(
        select(SavedAnswer, Message, User)
        .join(Message, Message.id == SavedAnswer.message_id)
        .outerjoin(User, User.id == SavedAnswer.saved_by)
        .where(SavedAnswer.workspace_id == workspace_id)
        .order_by(SavedAnswer.created_at.desc())
    ).all()
    question = {}
    for sa, msg, _ in rows:
        q = db.scalar(
            select(Message.content)
            .where(
                Message.conversation_id == msg.conversation_id,
                Message.role == "user",
                Message.created_at <= msg.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        question[sa.id] = q
    return [
        {
            "id": str(sa.id),
            "question": question[sa.id],
            "answer": msg.content,
            "note": sa.note,
            "saved_by": u.email if u else None,
            "created_at": sa.created_at.isoformat(),
            "message_id": str(msg.id),
        }
        for sa, msg, u in rows
    ]
