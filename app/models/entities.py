from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ContentStatus(str, enum.Enum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    FAILED = "FAILED"
    PURGED = "PURGED"


class PendingStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


status_type = Enum(
    ContentStatus,
    name="content_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)


class ContentQueue(Base):
    __tablename__ = "content_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_text: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[ContentStatus] = mapped_column(status_type, nullable=False, default=ContentStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class TrustStore(Base):
    __tablename__ = "trust_store"

    action_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ActionLog(Base):
    __tablename__ = "action_log"

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_row_id: Mapped[int] = mapped_column(ForeignKey("content_queue.id"), nullable=False, index=True)
    previous_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    new_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())


class PendingAction(Base):
    __tablename__ = "pending_actions"

    intent_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_row_id: Mapped[int] = mapped_column(ForeignKey("content_queue.id"), nullable=False, index=True)
    intent: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[PendingStatus] = mapped_column(
        Enum(PendingStatus, name="pending_status", native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
        default=PendingStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
