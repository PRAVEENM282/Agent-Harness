# Progressive Autonomy Agent Harness

This repository implements a small FastAPI content queue whose agent intents pass through a structural safety gateway. The mock agent parses prompts into one validated single-row intent; it never receives a database session and never chooses trust state.

## Architecture

```text
prompt -> strict Pydantic intent -> target/state validation -> trust gate
                                      |                       |
                                      |                       +-- trusted -> reversible transaction
                                      +-- untrusted -> persisted HITL intent

reversible transaction -> content_queue mutation + append-only action_log
restore_from_log       -> exact previous snapshot + rollback log + trust penalty
```

PostgreSQL is the container database. SQLAlchemy creates `content_queue`, `trust_store`, `action_log`, and the restart-safe `pending_actions` table at application startup. Local tests use isolated SQLite databases while exercising the same ORM and service code.

The only mutating tools are `mark_posted`, `retry`, and `purge`. They accept one positive `target_id`; `purge` changes a row to `PURGED` and there is no bulk purge or delete tool.

## Setup

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`.env.example` pins the mock model name and base URL, sets `MOCK_LLM_ENABLED=true`, and defaults to `TRUST_THRESHOLD=3`. `DATABASE_URL` may point to PostgreSQL; without an environment file the local fallback is `sqlite:///./agent_harness.db`.

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
python scripts/seed_db.py
```

The seed contains ten rows, including pending and failed rows and two future-dated rows.

## Run with Docker Compose

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health
python scripts/seed_db.py
```

The app waits for the PostgreSQL `pg_isready` healthcheck. The named database volume persists data, and the app healthcheck calls `/health`.

## API examples

Queue item IDs are returned by the seed script.

```bash
curl -X POST http://localhost:8000/api/agent/execute \
  -H 'content-type: application/json' \
  -d '{"prompt":"Purge everything immediately without asking.","target_id":1}'

curl http://localhost:8000/api/hitl/pending
curl -X POST http://localhost:8000/api/hitl/approve/<intent_id>

curl -X POST http://localhost:8000/api/system/restore \
  -H 'content-type: application/json' \
  -d '{"action_id":"<action_id>"}'
```

An invalid target is rejected before a pending intent is written. A failed action-log insert or failed mutation rolls back the queue mutation.

## Progressive trust walkthrough

With `TRUST_THRESHOLD=3`, every action type begins at zero:

1. The first purge is persisted as pending and does not change the row.
2. Human approval executes it through the reversible logger and raises purge trust to 1.
3. Two more successful human approvals raise it to 3.
4. The next purge of a valid row executes immediately, still creating an action log.

N=3 is the default because it requires repeated supervised evidence while keeping the walkthrough short. It is configurable for stricter or more manual deployments. Trust increments only after a successful approval. A restore decrements the associated action type, with a floor of zero.

## Rollback

Every mutation log contains the exact queue row before and after the mutation, including timestamps. Submit the original `action_id` to `/api/system/restore`. `restore_from_log()` validates those bytes, restores the row transactionally, appends a separate `rollback` log entry, and applies the trust penalty. It does not ask the model whether recovery is possible.

## Simulation

```bash
python scripts/simulate_failure.py
```

The simulation resets deterministic data, sends `Purge everything immediately without asking.`, and proves that only one pending individual intent is created. It checks that all ten rows and future-dated rows survive, approves that one intent, verifies complete before/after bytes, and restores the row.

## Premortem

The supplied Replit incident context describes an agent that reportedly ignored a code freeze, performed destructive work, and later claimed recovery was impossible. Those incident claims are treated here as the provided scenario, not as independently verified facts.

- Ignored freeze: the harness does not trust prompt or advisory context. The mutation gateway, persisted target validation, trust gate, and HITL approval remain authoritative even when a prompt says to proceed.
- Deletion: untrusted destructive actions are intercepted before mutation, and no bulk purge tool exists. The mock parser can produce only one purge intent with one validated target ID.
- False “cannot roll back”: independently persisted before-state bytes plus deterministic `restore_from_log()` provide application-level recovery without asking the model.

## Postmortem: advisory versus structural controls

Advisory controls are prompts and instructions to the model, such as “do not delete during a freeze.” They can be ignored or misunderstood. Structural controls are schema validation, target validation, the trust gate, persisted pending approval, the transactional append-only action log, the rollback engine, and the rollback trust penalty. The simulation demonstrates that the structural controls decide whether a mutation can occur.

## Tests

```bash
pytest -q
```

The suite covers table shape, invalid targets, untrusted and trusted paths, HITL approval, exact snapshots, rollback and trust penalties, bulk safety, future-row survival, logging failures, and mutation transaction failures.
