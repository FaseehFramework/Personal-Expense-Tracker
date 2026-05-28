"""
Lightweight SQLite migration runner.

Reads `.sql` files from `database/migrations/`, applies any not yet recorded
in the `schema_migrations` tracking table, and records each on success.

Design choices:
  - Migrations are plain SQL — no Python step, no ORM, no autogen.
  - Each migration runs as one `executescript()` call. SQLite issues an
    implicit COMMIT before executescript, so each file is committed as a
    whole; if a migration fails partway, only the statements before the
    failing one took effect. Write idempotent SQL whenever possible
    (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, etc.).
  - Filenames are sorted lexicographically and must start with a 4-digit
    sequence number, e.g. `0001_initial.sql`, `0002_add_streak_index.sql`.
  - The schema_migrations table is bootstrapped on first run.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{4})_[\w\-]+\.sql$")


def _bootstrap_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  filename   TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.commit()


def _list_migration_files() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    files = []
    for p in MIGRATIONS_DIR.iterdir():
        if not p.is_file() or p.suffix != ".sql":
            continue
        if not _FILENAME_RE.match(p.name):
            raise RuntimeError(
                f"Migration filename '{p.name}' must match NNNN_name.sql "
                "(four-digit prefix, snake_case body)."
            )
        files.append(p)
    files.sort(key=lambda p: p.name)
    return files


def list_pending(conn: sqlite3.Connection) -> list[Path]:
    """Return migration files that have not been applied to this connection."""
    _bootstrap_tracking_table(conn)
    applied = {
        r[0] for r in conn.execute("SELECT filename FROM schema_migrations")
    }
    return [p for p in _list_migration_files() if p.name not in applied]


def apply_pending(conn: sqlite3.Connection, log=print) -> list[str]:
    """Apply every migration not yet recorded. Returns names of applied files."""
    pending = list_pending(conn)
    applied_names: list[str] = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
        except sqlite3.Error as e:
            # The migration that errored may have partially committed earlier
            # statements (executescript autocommits). We leave the marker
            # unset so a fix-up rerun will retry; the operator must inspect.
            log(f"FAILED applying {path.name}: {e}")
            raise
        conn.execute(
            "INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,)
        )
        conn.commit()
        applied_names.append(path.name)
        log(f"applied {path.name}")
    return applied_names


def applied_history(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (filename, applied_at) tuples in application order."""
    _bootstrap_tracking_table(conn)
    return [
        (r["filename"], r["applied_at"])
        for r in conn.execute(
            "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"
        )
    ]
