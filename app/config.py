from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _threshold() -> int:
    raw = os.getenv("TRUST_THRESHOLD", "3")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("TRUST_THRESHOLD must be an integer") from exc
    if value < 0:
        raise ValueError("TRUST_THRESHOLD must be non-negative")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./agent_harness.db")
    trust_threshold: int = _threshold()
    trace_path: str = os.getenv("TRACE_PATH", "traces.jsonl")
    mock_llm_enabled: bool = os.getenv("MOCK_LLM_ENABLED", "true").lower() == "true"


settings = Settings()
