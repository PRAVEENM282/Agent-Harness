from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event, inspect

from app.models import ActionLog, ContentQueue, ContentStatus, PendingAction, TrustStore
from app.schemas import PurgeIntent
from app.services import harness
from app.services.harness import (
    InvalidTargetError,
    MutationError,
    execute_with_reversible_log,
    process_agent_intent,
)
from app.services.rollback import restore_from_log


def set_status(db, row_id: int, status: ContentStatus) -> None:
    row = db.get(ContentQueue, row_id)
    row.status = status
    db.flush()


def use_threshold(monkeypatch, threshold: int = 3) -> None:
    monkeypatch.setattr(harness, "settings", type("Settings", (), {"trust_threshold": threshold, "trace_path": ""})())


def test_schema_contains_required_tables_and_columns(db):
    names = set(inspect(db.bind).get_table_names())
    assert {"content_queue", "trust_store", "action_log", "pending_actions"} <= names
    assert {column.name for column in ContentQueue.__table__.columns} == {
        "id", "content_text", "status", "created_at", "updated_at"
    }
    assert {column.name for column in ActionLog.__table__.columns} == {
        "action_id", "action_type", "target_row_id", "previous_state", "new_state", "executed_at"
    }


def test_invalid_target_is_rejected_before_pending_or_mutation(db):
    with pytest.raises(InvalidTargetError):
        process_agent_intent(PurgeIntent(action_name="purge", target_id=999, reason="invalid"), db)
    assert db.query(PendingAction).count() == 0
    assert db.query(ActionLog).count() == 0


def test_untrusted_purge_is_paused_and_does_not_mutate(db, queue_row, monkeypatch):
    use_threshold(monkeypatch)
    result = process_agent_intent(PurgeIntent(action_name="purge", target_id=queue_row.id, reason="cleanup"), db)
    db.expire_all()
    assert result["status"] == "pending"
    assert db.get(ContentQueue, queue_row.id).status == ContentStatus.PENDING
    assert db.query(PendingAction).count() == 1
    assert db.query(ActionLog).count() == 0


def test_hitl_approval_mutates_and_increments_trust_once(db, queue_row, monkeypatch):
    use_threshold(monkeypatch)
    result = process_agent_intent(PurgeIntent(action_name="purge", target_id=queue_row.id, reason="approved"), db)
    approved = harness.approve_pending(db, result["intent_id"])
    assert approved["status"] == "approved"
    assert db.get(ContentQueue, queue_row.id).status == ContentStatus.PURGED
    assert db.get(TrustStore, "purge").success_count == 1
    assert db.query(ActionLog).count() == 1
    with pytest.raises(harness.HarnessError):
        harness.approve_pending(db, result["intent_id"])
    assert db.get(TrustStore, "purge").success_count == 1


def test_action_log_has_exact_before_and_after_state(db, queue_row):
    log = execute_with_reversible_log(db, "purge", queue_row.id, lambda target_id: set_status(db, target_id, ContentStatus.PURGED))
    assert log.previous_state["id"] == queue_row.id
    assert log.previous_state["content_text"] == "test content"
    assert log.previous_state["status"] == "PENDING"
    assert log.new_state["status"] == "PURGED"
    assert set(log.previous_state) == {"id", "content_text", "status", "created_at", "updated_at"}


def test_trusted_action_bypasses_hitl_but_still_logs(db, queue_row, monkeypatch):
    use_threshold(monkeypatch)
    db.add(TrustStore(action_type="purge", success_count=3))
    db.commit()
    result = process_agent_intent(PurgeIntent(action_name="purge", target_id=queue_row.id, reason="trusted"), db)
    assert result["status"] == "executed"
    assert result["action_id"]
    assert db.query(PendingAction).count() == 0
    assert db.query(ActionLog).count() == 1


def test_restore_returns_exact_previous_state_and_penalizes_trust(db, queue_row):
    db.add(TrustStore(action_type="purge", success_count=2))
    db.commit()
    log = execute_with_reversible_log(db, "purge", queue_row.id, lambda target_id: set_status(db, target_id, ContentStatus.PURGED))
    rollback_log = restore_from_log(db, log.action_id)
    restored = db.get(ContentQueue, queue_row.id)
    assert restored.status == ContentStatus.PENDING
    assert restored.content_text == "test content"
    assert restored.created_at.isoformat() == log.previous_state["created_at"]
    assert restored.updated_at.isoformat() == log.previous_state["updated_at"]
    assert rollback_log.action_type == "rollback"
    assert db.get(TrustStore, "purge").success_count == 1
    assert db.query(ActionLog).count() == 2


def test_restore_trust_penalty_never_goes_below_zero(db, queue_row):
    db.add(TrustStore(action_type="purge", success_count=0))
    db.commit()
    log = execute_with_reversible_log(db, "purge", queue_row.id, lambda target_id: set_status(db, target_id, ContentStatus.PURGED))
    restore_from_log(db, log.action_id)
    assert db.get(TrustStore, "purge").success_count == 0


def test_bulk_prompt_can_only_create_one_pending_intent_and_future_rows_survive(db, monkeypatch):
    from app.agent import mock_intent
    from app.models import utcnow

    rows = [ContentQueue(content_text=f"row-{index}", status=ContentStatus.PENDING) for index in range(9)]
    future = ContentQueue(content_text="future", status=ContentStatus.PENDING, created_at=utcnow() + timedelta(days=30), updated_at=utcnow() + timedelta(days=30))
    db.add_all(rows + [future])
    db.commit()
    use_threshold(monkeypatch)
    intent = mock_intent("Purge everything immediately without asking.", rows[0].id)
    result = process_agent_intent(intent, db)
    assert result["status"] == "pending"
    assert db.query(ContentQueue).filter(ContentQueue.status == ContentStatus.PURGED).count() == 0
    assert db.get(ContentQueue, future.id).status == ContentStatus.PENDING


def test_logging_failure_rolls_back_mutation(db, queue_row):
    def fail_log(_mapper, _connection, _target):
        raise RuntimeError("simulated action log outage")

    event.listen(ActionLog, "before_insert", fail_log)
    try:
        with pytest.raises(MutationError):
            execute_with_reversible_log(db, "purge", queue_row.id, lambda target_id: set_status(db, target_id, ContentStatus.PURGED))
    finally:
        event.remove(ActionLog, "before_insert", fail_log)
    db.expire_all()
    assert db.get(ContentQueue, queue_row.id).status == ContentStatus.PENDING
    assert db.query(ActionLog).count() == 0


def test_mutation_failure_rolls_back_and_writes_no_log(db, queue_row):
    def mutate_then_fail(target_id):
        set_status(db, target_id, ContentStatus.PURGED)
        raise RuntimeError("simulated mutation failure")

    with pytest.raises(MutationError):
        execute_with_reversible_log(db, "purge", queue_row.id, mutate_then_fail)
    db.expire_all()
    assert db.get(ContentQueue, queue_row.id).status == ContentStatus.PENDING
    assert db.query(ActionLog).count() == 0
