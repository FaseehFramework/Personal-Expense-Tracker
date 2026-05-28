"""Test harness — isolated in-memory SQLite per test, schema applied fresh."""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _new_db() -> sqlite3.Connection:
    """Fresh in-memory SQLite with all migrations applied — same code path
    a real Pi boot uses, so tests catch migration bugs."""
    from database.migrate import apply_pending
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending(conn, log=lambda msg: None)
    return conn


@pytest.fixture
def db():
    """Fresh isolated DB for each test."""
    conn = _new_db()
    try:
        yield conn
    finally:
        conn.close()


# ---------- factories ----------

def set_budget(conn, month: str, amount_fils: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO monthly_budgets (month, amount) VALUES (?, ?)",
        (month, amount_fils),
    )


def add_tx(conn, *, date: str, amount: int, tx_type: str,
           source: str = "bank", category_id=None, description: str = "test"):
    """Insert a non-deleted transaction. `amount` in fils."""
    cur = conn.execute(
        "INSERT INTO transactions (date, amount, type, source, category_id, description) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, amount, tx_type, source, category_id, description),
    )
    return cur.lastrowid


def write_history(conn, month: str, *, budget=0, spent=0, rollover=0,
                  savings=0, negative_cascade=0) -> None:
    conn.execute(
        "INSERT INTO budget_history (month, budget_set, actual_spend, rollover_amount, "
        "savings_amount, negative_cascade) VALUES (?, ?, ?, ?, ?, ?)",
        (month, budget, spent, rollover, savings, negative_cascade),
    )
