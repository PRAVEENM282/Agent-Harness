# Progressive Autonomy Agent Harness Implementation Plan

## Goal

Build a small FastAPI and SQLAlchemy service whose validated single-row tool intents pass through persistent trust gating, reversible action logging, HITL approval, and deterministic restore.

## Architecture

`app/db.py` owns the SQLAlchemy engine and sessions. `app/models/` defines the queue, trust, action log, and pending action tables. `app/services/harness.py` is the only mutation gateway; it validates targets, applies the trust threshold, and invokes the reversible transaction primitive. `app/services/rollback.py` restores exact snapshots and applies the trust penalty. HTTP routes and scripts call these services but never mutate queue rows directly.

## Ordered slices

1. Add configuration, models, database setup, and strict intent schemas.
2. Add the reversible mutation primitive and trust-gated harness service.
3. Add FastAPI agent, HITL, restore, and health endpoints.
4. Add deterministic seed/simulation scripts and JSONL tracing.
5. Add pytest coverage for schema, guardrails, transactions, trust, and restore.
6. Add Docker Compose, README, and final runtime verification.

## Constraints

- PostgreSQL is the container and production database; SQLite is used only for isolated tests/local fallback.
- Only `mark_posted`, `retry`, and `purge` exist, each targeting one row.
- Every successful mutation and rollback is represented by an append-only action log row.
- Trust is persisted outside prompts and increments only after successful HITL execution.
- Any validation, logging, transaction, or restore failure fails closed.
