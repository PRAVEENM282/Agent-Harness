from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActionLog, ContentQueue, ContentStatus, PendingAction, PendingStatus, TrustStore
from app.schemas import (
    MarkPostedIntent,
    PurgeIntent,
    RetryIntent,
    ToolCallIntent,
    model_dump,
)
from app.services.tracing import write_trace

SUPPORTED_ACTIONS = {"mark_posted", "retry", "purge"}
INTENT_ADAPTER = TypeAdapter(ToolCallIntent)


class HarnessError(RuntimeError):
    """Base error for a rejected or failed harness operation."""


class InvalidTargetError(HarnessError):
    pass


class MutationError(HarnessError):
    pass


def serialize_queue_row(row: ContentQueue) -> dict[str, Any]:
    if row.id is None or row.content_text is None or row.status is None:
        raise MutationError("queue row is missing required state")
    if row.created_at is None or row.updated_at is None:
        raise MutationError("queue row is missing timestamps")
    status = row.status.value if isinstance(row.status, ContentStatus) else str(row.status)
    if status not in {status.value for status in ContentStatus}:
        raise MutationError("queue row has an invalid status")
    return {
        "id": row.id,
        "content_text": row.content_text,
        "status": status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def parse_intent(payload: dict[str, Any]) -> ToolCallIntent:
    try:
        return INTENT_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise HarnessError("invalid or unsupported tool intent") from exc


def _target_allowed(intent: ToolCallIntent, row: ContentQueue) -> bool:
    status = row.status.value if isinstance(row.status, ContentStatus) else str(row.status)
    if isinstance(intent, MarkPostedIntent):
        return status == ContentStatus.PENDING.value
    if isinstance(intent, RetryIntent):
        return status == ContentStatus.FAILED.value
    if isinstance(intent, PurgeIntent):
        return status != ContentStatus.PURGED.value
    return False


def validate_intent_target(db_session: Session, intent: ToolCallIntent) -> ContentQueue:
    if intent.action_name not in SUPPORTED_ACTIONS:
        raise InvalidTargetError("unsupported action")
    row = db_session.get(ContentQueue, intent.target_id)
    if row is None:
        raise InvalidTargetError("target row does not exist")
    if not _target_allowed(intent, row):
        raise InvalidTargetError("target row is not executable for this action")
    return row


def get_trust_record(db_session: Session, action_type: str) -> TrustStore:
    if action_type not in SUPPORTED_ACTIONS:
        raise HarnessError("unsupported action type")
    record = db_session.get(TrustStore, action_type)
    if record is None:
        record = TrustStore(action_type=action_type, success_count=0)
        db_session.add(record)
        db_session.flush()
    if record.success_count < 0:
        raise HarnessError("invalid trust state")
    return record


def _invoke_mutation(mutation_func: Callable[[int], None], target_id: int) -> None:
    mutation_func(target_id)


def _execute_without_commit(
    db_session: Session,
    action_type: str,
    target_id: int,
    mutation_func: Callable[[int], None],
) -> ActionLog:
    if action_type not in SUPPORTED_ACTIONS:
        raise HarnessError("unsupported action type")
    row = db_session.get(ContentQueue, target_id)
    if row is None:
        raise InvalidTargetError("target row does not exist")
    previous_state = serialize_queue_row(row)
    _invoke_mutation(mutation_func, target_id)
    db_session.flush()
    db_session.expire(row)
    resulting_row = db_session.get(ContentQueue, target_id)
    if resulting_row is None:
        raise MutationError("mutation removed the target row")
    new_state = serialize_queue_row(resulting_row)
    action_log = ActionLog(
        action_id=str(uuid.uuid4()),
        action_type=action_type,
        target_row_id=target_id,
        previous_state=previous_state,
        new_state=new_state,
    )
    db_session.add(action_log)
    db_session.flush()
    return action_log


def execute_with_reversible_log(
    db_session: Session,
    action_type: str,
    target_id: int,
    mutation_func: Callable[[int], None],
) -> ActionLog:
    """Apply one mutation and its exact before/after log in one transaction."""
    try:
        action_log = _execute_without_commit(db_session, action_type, target_id, mutation_func)
        db_session.commit()
        return action_log
    except Exception as exc:
        db_session.rollback()
        if isinstance(exc, HarnessError):
            raise
        raise MutationError("mutation and action log transaction failed") from exc


def _set_status(db_session: Session, target_id: int, status: ContentStatus) -> None:
    row = db_session.get(ContentQueue, target_id)
    if row is None:
        raise InvalidTargetError("target row does not exist")
    row.status = status
    row.updated_at = datetime.now(timezone.utc)


def mutation_for_intent(db_session: Session, intent: ToolCallIntent) -> Callable[[int], None]:
    if isinstance(intent, MarkPostedIntent):
        return lambda target_id: _set_status(db_session, target_id, ContentStatus.POSTED)
    if isinstance(intent, RetryIntent):
        return lambda target_id: _set_status(db_session, target_id, ContentStatus.PENDING)
    if isinstance(intent, PurgeIntent):
        return lambda target_id: _set_status(db_session, target_id, ContentStatus.PURGED)
    raise HarnessError("unsupported action intent")


def _execute_intent(db_session: Session, intent: ToolCallIntent) -> ActionLog:
    return execute_with_reversible_log(db_session, intent.action_name, intent.target_id, mutation_for_intent(db_session, intent))


def process_agent_intent(intent: ToolCallIntent, db_session: Session, raw_prompt: str | None = None) -> dict[str, Any]:
    """Validate, trust-check, and either queue or execute one agent intent."""
    row = validate_intent_target(db_session, intent)
    trust = get_trust_record(db_session, intent.action_name)
    trusted = trust.success_count >= settings.trust_threshold
    trace = {
        "row_id": intent.target_id,
        "raw_prompt": raw_prompt,
        "validated_intent": model_dump(intent),
        "guardrail_checks": {
            "schema_valid": True,
            "target_exists": row.id == intent.target_id,
            "single_row_tool": True,
            "trust_count": trust.success_count,
            "trust_threshold": settings.trust_threshold,
        },
        "selected_action": intent.action_name,
        "outcome": "authorized" if trusted else "pending",
    }
    try:
        write_trace(trace)
        if not trusted:
            pending = PendingAction(
                intent_id=str(uuid.uuid4()),
                action_type=intent.action_name,
                target_row_id=intent.target_id,
                intent=model_dump(intent),
                status=PendingStatus.PENDING,
            )
            db_session.add(pending)
            db_session.commit()
            return {
                "status": "pending",
                "message": "Action registered and pending human review.",
                "intent_id": pending.intent_id,
                "action_type": intent.action_name,
                "target_id": intent.target_id,
            }
        action_log = _execute_intent(db_session, intent)
        return {
            "status": "executed",
            "message": "Action executed through the reversible harness.",
            "action_id": action_log.action_id,
            "action_type": intent.action_name,
            "target_id": intent.target_id,
            "result": {"status": "mutated"},
        }
    except Exception as exc:
        db_session.rollback()
        if isinstance(exc, HarnessError):
            raise
        raise MutationError("agent action failed closed") from exc


def list_pending(db_session: Session) -> list[dict[str, Any]]:
    pending = db_session.scalars(
        select(PendingAction)
        .where(PendingAction.status == PendingStatus.PENDING)
        .order_by(PendingAction.created_at, PendingAction.intent_id)
    ).all()
    return [
        {
            "intent_id": item.intent_id,
            "action_type": item.action_type,
            "target_row_id": item.target_row_id,
            "intent": item.intent,
            "status": item.status.value,
            "created_at": item.created_at.isoformat(),
        }
        for item in pending
    ]


def approve_pending(db_session: Session, intent_id: str) -> dict[str, Any]:
    pending = db_session.execute(
        select(PendingAction).where(PendingAction.intent_id == intent_id).with_for_update()
    ).scalar_one_or_none()
    if pending is None:
        raise InvalidTargetError("pending intent does not exist")
    if pending.status != PendingStatus.PENDING:
        raise HarnessError("pending intent is no longer executable")
    intent = parse_intent(pending.intent)
    if intent.target_id != pending.target_row_id or intent.action_name != pending.action_type:
        raise HarnessError("persisted intent does not match pending metadata")
    validate_intent_target(db_session, intent)
    try:
        action_log = _execute_without_commit(
            db_session,
            intent.action_name,
            intent.target_id,
            mutation_for_intent(db_session, intent),
        )
        trust = get_trust_record(db_session, intent.action_name)
        trust.success_count += 1
        pending.status = PendingStatus.COMPLETED
        pending.completed_at = datetime.now(timezone.utc)
        pending.result_action_id = action_log.action_id
        db_session.commit()
        return {
            "status": "approved",
            "message": "Pending action approved and executed.",
            "action_id": action_log.action_id,
            "action_type": intent.action_name,
            "target_id": intent.target_id,
            "result": {"status": "mutated", "trust_count": trust.success_count},
        }
    except Exception as exc:
        db_session.rollback()
        if isinstance(exc, HarnessError):
            raise
        raise MutationError("approval transaction failed closed") from exc
