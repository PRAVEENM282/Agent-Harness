from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActionLog, ContentQueue, ContentStatus, TrustStore
from app.services.harness import HarnessError, MutationError, serialize_queue_row


def _snapshot_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise MutationError("snapshot timestamp is invalid")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise MutationError("snapshot timestamp is invalid") from exc


def _validate_snapshot(snapshot: dict[str, Any], target_id: int) -> None:
    required = {"id", "content_text", "status", "created_at", "updated_at"}
    if set(snapshot) != required or snapshot["id"] != target_id:
        raise MutationError("snapshot shape does not match target")
    if not isinstance(snapshot["content_text"], str) or snapshot["status"] not in {item.value for item in ContentStatus}:
        raise MutationError("snapshot values are invalid")
    _snapshot_datetime(snapshot["created_at"])
    _snapshot_datetime(snapshot["updated_at"])


def restore_from_log(db_session: Session, action_id: str) -> ActionLog:
    original = db_session.get(ActionLog, str(action_id))
    if original is None:
        raise HarnessError("action log entry does not exist")
    if original.action_type == "rollback":
        raise HarnessError("rollback log entries cannot be restored")
    _validate_snapshot(original.previous_state, original.target_row_id)
    row = db_session.execute(
        select(ContentQueue).where(ContentQueue.id == original.target_row_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise MutationError("rollback target no longer exists")

    current_state = serialize_queue_row(row)
    snapshot = original.previous_state
    row.content_text = snapshot["content_text"]
    row.status = ContentStatus(snapshot["status"])
    row.created_at = _snapshot_datetime(snapshot["created_at"])
    row.updated_at = _snapshot_datetime(snapshot["updated_at"])
    db_session.flush()
    restored_state = serialize_queue_row(row)
    rollback_log = ActionLog(
        action_type="rollback",
        target_row_id=row.id,
        previous_state=current_state,
        new_state=restored_state,
    )
    db_session.add(rollback_log)
    trust = db_session.get(TrustStore, original.action_type)
    if trust is None:
        trust = TrustStore(action_type=original.action_type, success_count=0)
        db_session.add(trust)
    trust.success_count = max(0, trust.success_count - 1)
    try:
        db_session.commit()
        return rollback_log
    except Exception as exc:
        db_session.rollback()
        raise MutationError("rollback transaction failed closed") from exc
