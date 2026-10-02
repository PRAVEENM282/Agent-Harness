from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.agent import mock_intent
from app.db import get_db, init_db
from app.models import ActionLog
from app.schemas import AgentExecuteRequest, RestoreRequest
from app.services.harness import (
    HarnessError,
    InvalidTargetError,
    MutationError,
    approve_pending,
    list_pending,
    process_agent_intent,
)
from app.services.rollback import restore_from_log
from app.services.tracing import TraceWriteError


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Progressive Autonomy Agent Harness", version="0.1.0", lifespan=lifespan)


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, InvalidTargetError):
        raise HTTPException(status_code=422, detail={"code": "INVALID_TARGET", "message": str(exc)}) from exc
    if isinstance(exc, TraceWriteError):
        raise HTTPException(status_code=503, detail={"code": "TRACE_FAILURE", "message": "request failed closed"}) from exc
    if isinstance(exc, MutationError):
        raise HTTPException(status_code=500, detail={"code": "MUTATION_FAILED", "message": "request failed closed"}) from exc
    if isinstance(exc, HarnessError):
        raise HTTPException(status_code=422, detail={"code": "HARNESS_REJECTED", "message": str(exc)}) from exc
    raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "request failed closed"}) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/hitl/pending")
def pending_actions(db: Session = Depends(get_db)) -> list[dict]:
    return list_pending(db)


@app.post("/api/agent/execute")
def execute_agent(request: AgentExecuteRequest, db: Session = Depends(get_db)) -> dict:
    try:
        intent = mock_intent(request.prompt, request.target_id)
        return process_agent_intent(intent, db, raw_prompt=request.prompt)
    except Exception as exc:
        db.rollback()
        _raise_api_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/hitl/approve/{intent_id}")
def approve_action(intent_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return approve_pending(db, intent_id)
    except Exception as exc:
        db.rollback()
        _raise_api_error(exc)
        raise AssertionError("unreachable")


@app.post("/api/system/restore")
def restore_action(request: RestoreRequest, db: Session = Depends(get_db)) -> dict:
    original = db.get(ActionLog, request.action_id)
    if original is None:
        raise HTTPException(status_code=404, detail={"code": "ACTION_NOT_FOUND", "message": "action log entry does not exist"})
    try:
        rollback_log = restore_from_log(db, request.action_id)
        return {
            "status": "restored",
            "action_id": rollback_log.action_id,
            "restored_action_id": request.action_id,
            "action_type": original.action_type,
            "result": rollback_log.new_state,
        }
    except Exception as exc:
        db.rollback()
        _raise_api_error(exc)
        raise AssertionError("unreachable")
