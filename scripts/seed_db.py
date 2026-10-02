from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db
from app.seed import reset_and_seed


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = reset_and_seed(db)
        print(f"Seeded {len(rows)} deterministic content rows (IDs {rows[0].id}-{rows[-1].id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
