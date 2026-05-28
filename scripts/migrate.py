"""
Apply pending SQLite migrations.

Usage:
    python -m scripts.migrate                # apply pending migrations
    python -m scripts.migrate --dry-run      # list what would run; apply nothing
    python -m scripts.migrate --status       # show applied history + pending count
    python -m scripts.migrate --backup       # snapshot DB to database/backups/ first
    python -m scripts.migrate --backup --yes # combine: backup then apply

Notes:
    - Safe to run repeatedly; already-applied migrations are skipped.
    - On a fresh device, this creates the DB and applies every migration.
    - On an existing Pi, this only applies migrations not yet in the
      `schema_migrations` table — your data is untouched.
    - Stop the Flask service before running risky migrations (column
      changes, backfills). Additive-only migrations are safe to apply hot.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config
from database.migrate import apply_pending, applied_history, list_pending


def _open_conn() -> sqlite3.Connection:
    Path(Config.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_backup() -> Path | None:
    db_path = Path(Config.DATABASE_PATH)
    if not db_path.exists():
        print("(no existing DB to back up — this is a fresh install)")
        return None
    backups_dir = ROOT / "database" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = backups_dir / f"pre_migrate_{stamp}.sqlite"
    shutil.copy2(db_path, dst)
    print(f"backup saved: {dst}")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply SQLite migrations.")
    ap.add_argument("--dry-run", action="store_true", help="show what would run; apply nothing")
    ap.add_argument("--status",  action="store_true", help="show applied history and pending count, then exit")
    ap.add_argument("--backup",  action="store_true", help="snapshot DB to database/backups/ before applying")
    ap.add_argument("--yes",     action="store_true", help="suppress the confirmation prompt")
    args = ap.parse_args()

    conn = _open_conn()
    try:
        if args.status:
            history = applied_history(conn)
            pending = list_pending(conn)
            print(f"applied ({len(history)}):")
            for name, when in history:
                print(f"  {name}  @ {when}")
            print(f"pending ({len(pending)}):")
            for p in pending:
                print(f"  {p.name}")
            return 0

        pending = list_pending(conn)
        if not pending:
            print("up to date — no migrations pending.")
            return 0

        print(f"{len(pending)} pending migration(s):")
        for p in pending:
            print(f"  {p.name}")

        if args.dry_run:
            print("(dry-run; nothing applied)")
            return 0

        if not args.yes:
            ans = input("Apply now? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("aborted.")
                return 1

        if args.backup:
            _make_backup()

        apply_pending(conn)
        print("done.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
