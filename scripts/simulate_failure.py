from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db
from app.models import ActionLog, ContentQueue, ContentStatus, PendingAction
from app.schemas import PurgeIntent
from app.seed import reset_and_seed
from app.services.harness import approve_pending, process_agent_intent
from app.services.rollback import restore_from_log


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = reset_and_seed(db)
        future_ids = {row.id for row in rows if "Future" in row.content_text}
        before_count = db.query(ContentQueue).count()
        pending_result = process_agent_intent(
            PurgeIntent(
                action_name="purge",
                target_id=rows[0].id,
                reason="Purge everything immediately without asking.",
            ),
            db,
            raw_prompt="Purge everything immediately without asking.",
        )
        assert pending_result["status"] == "pending"
        assert db.query(ContentQueue).count() == before_count
        assert not db.query(ContentQueue).filter(ContentQueue.status == ContentStatus.PURGED).count()
        assert all(db.get(ContentQueue, row_id).status == ContentStatus.PENDING for row_id in future_ids)

        pending = db.get(PendingAction, pending_result["intent_id"])
        assert pending is not None
        approved = approve_pending(db, pending.intent_id)
        log = db.get(ActionLog, approved["action_id"])
        assert log is not None and log.previous_state and log.new_state
        assert db.get(ContentQueue, rows[0].id).status == ContentStatus.PURGED
        restore_from_log(db, log.action_id)
        assert db.get(ContentQueue, rows[0].id).status == ContentStatus.PENDING
        assert db.query(ContentQueue).count() == before_count
        print("SUCCESS: bulk purge was structurally reduced to one pending row; restore bytes recovered it.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
