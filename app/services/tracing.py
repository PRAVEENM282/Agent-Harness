from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings


class TraceWriteError(RuntimeError):
    """Raised when a decision trace cannot be persisted."""


def write_trace(event: dict[str, Any]) -> None:
    if not settings.trace_path:
        return
    try:
        path = Path(settings.trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        raise TraceWriteError("decision trace could not be written") from exc
