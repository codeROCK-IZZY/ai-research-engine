"""SQLAlchemy ORM models. The schema is created by the explicit-SQL Alembic migration;
``tests/test_schema.py`` checks that models and migration agree."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384
DOC_TYPES = ("invoice", "contract", "receipt", "resume", "report", "letter", "policy", "other")

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = metadata


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=sql_text("gen_random_uuid()")
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _fk_uuid(target: str, ondelete: str = "CASCADE", nullable: bool = False, index: bool = False) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Workspace(Base):
    """A team space. Membership (with a role) is what grants access to its knowledge bases."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("name", name="uq_workspaces_name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = _created()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # owner | editor | reader
    created_at: Mapped[datetime] = _created()


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_knowledge_bases_workspace_name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk_uuid("workspaces.id", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    stale_after_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="365")
    created_at: Mapped[datetime] = _created()


class Document(Base):
    """A source in a knowledge base: an uploaded file, a file from a ZIP, or a fetched web page."""

    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "uq_documents_kb_url",
            "kb_id",
            "source_url",
            unique=True,
            postgresql_where=sql_text("source_url IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    kb_id: Mapped[uuid.UUID] = _fk_uuid("knowledge_bases.id", index=True)
    owner_id: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)  # added by
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(20))
    doc_type_source: Mapped[str | None] = mapped_column(String(100))
    source_kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="upload")
    source_url: Mapped[str | None] = mapped_column(String(2000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        order_by="DocumentVersion.version_no",
        lazy="selectin",
        foreign_keys="DocumentVersion.document_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_document_versions_doc_version"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = _fk_uuid("documents.id")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    stage: Mapped[str | None] = mapped_column(String(30))
    stages: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document: Mapped[Document] = relationship(back_populates="versions", foreign_keys=[document_id])


class Page(Base):
    __tablename__ = "pages"
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    tables: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[uuid.UUID] = _fk_uuid("document_versions.id", index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (UniqueConstraint("version_id", "kind", name="uq_extractions_version_kind"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[uuid.UUID] = _fk_uuid("document_versions.id")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[uuid.UUID] = _fk_uuid("document_versions.id", index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized: Mapped[str | None] = mapped_column(String(500))
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)


class MinhashSignature(Base):
    __tablename__ = "minhash_signatures"
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), primary_key=True
    )
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, server_default="New conversation")
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = _fk_uuid("conversations.id", index=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_status: Mapped[str | None] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(100))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Citation(Base):
    __tablename__ = "citations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[uuid.UUID] = _fk_uuid("messages.id", index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    cited_text: Mapped[str] = mapped_column(Text, nullable=False)
    sentence: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class SearchHistory(Base):
    __tablename__ = "search_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID] = _fk_uuid("users.id", index=True)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    kb_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    reranked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    results: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class SavedAnswer(Base):
    """An answer pinned to a workspace so colleagues can reuse it (with a note)."""

    __tablename__ = "saved_answers"
    __table_args__ = (UniqueConstraint("workspace_id", "message_id", name="uq_saved_answers_workspace_message"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _fk_uuid("workspaces.id")
    message_id: Mapped[uuid.UUID] = _fk_uuid("messages.id")
    saved_by: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = _fk_uuid("users.id", ondelete="SET NULL", nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
