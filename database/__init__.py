import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import g

from config import Config


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db() -> sqlite3.Connection:
    """Per-request DB connection, attached to Flask's `g`."""
    if "db" not in g:
        g.db = _connect(Config.DATABASE_PATH)
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Ensure storage dirs exist and apply any pending migrations.

    Runs on every Flask boot. The migration runner is idempotent — files
    already recorded in `schema_migrations` are skipped, so existing Pi
    deployments pick up new migrations transparently on next start.
    """
    Path(Config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(Config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    from database.migrate import apply_pending
    conn = _connect(Config.DATABASE_PATH)
    try:
        apply_pending(conn, log=lambda msg: None)  # quiet on normal boot
    finally:
        conn.close()


@contextmanager
def standalone_connection():
    """Use this from background jobs that run outside a Flask request."""
    conn = _connect(Config.DATABASE_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
