from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import ContentQueue, ContentStatus, TrustStore, utcnow


def reset_and_seed(db: Session) -> list[ContentQueue]:
    """Reset harness tables and add deterministic representative queue data."""
    from app.models import Base

    bind = db.get_bind()
    Base.metadata.drop_all(bind=bind)
    Base.metadata.create_all(bind=bind)
    rows = [
        ContentQueue(content_text="Launch announcement", status=ContentStatus.PENDING),
        ContentQueue(content_text="Product update", status=ContentStatus.PENDING),
        ContentQueue(content_text="Failed import", status=ContentStatus.FAILED),
        ContentQueue(content_text="Failed image post", status=ContentStatus.FAILED),
        ContentQueue(content_text="Already published", status=ContentStatus.POSTED),
        ContentQueue(content_text="Community question", status=ContentStatus.PENDING),
        ContentQueue(content_text="Retry candidate", status=ContentStatus.FAILED),
        ContentQueue(content_text="Future campaign A", status=ContentStatus.PENDING, created_at=utcnow() + timedelta(days=30), updated_at=utcnow() + timedelta(days=30)),
        ContentQueue(content_text="Future campaign B", status=ContentStatus.PENDING, created_at=utcnow() + timedelta(days=60), updated_at=utcnow() + timedelta(days=60)),
        ContentQueue(content_text="Evergreen post", status=ContentStatus.POSTED),
    ]
    db.add_all(rows)
    db.add_all([TrustStore(action_type=action_type, success_count=0) for action_type in ("mark_posted", "retry", "purge")])
    db.commit()
    return rows
