from __future__ import annotations

from app.config import settings
from app.schemas import MarkPostedIntent, PurgeIntent, RetryIntent, ToolCallIntent
from app.services.harness import HarnessError


def mock_intent(prompt: str, target_id: int) -> ToolCallIntent:
    """Convert a prompt to one strictly typed, single-row intent.

    This parser is deliberately deterministic. It never calls the database and it
    has no access to trust state; the harness owns those decisions.
    """
    if not settings.mock_llm_enabled:
        raise HarnessError("mock agent is disabled")
    normalized = prompt.strip().lower()
    if any(word in normalized for word in ("purge", "delete", "remove", "cleanup")):
        return PurgeIntent(action_name="purge", target_id=target_id, reason=prompt.strip())
    if "retry" in normalized or "again" in normalized:
        return RetryIntent(action_name="retry", target_id=target_id, reason=prompt.strip())
    if "post" in normalized or "publish" in normalized or "mark" in normalized:
        return MarkPostedIntent(action_name="mark_posted", target_id=target_id)
    raise HarnessError("mock agent could not select a supported tool")
