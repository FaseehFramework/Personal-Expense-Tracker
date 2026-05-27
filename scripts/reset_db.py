"""
Wipe the SQLite database and all uploaded attachments back to a fresh state.

USE WITH CARE — this is destructive and irreversible.

Usage:
    python -m scripts.reset_db                # prompts for confirmation
    python -m scripts.reset_db --yes          # no prompt
    python -m scripts.reset_db --yes --backup # take a timestamped backup first

After reset:
    - Next visit to the app shows the first-launch setup screen
    - All transactions, loans, receivables, wishlist, budgets, audit log → gone
    - All uploaded attachments → gone
    - Default categories and savings pot reseeded by schema.sql on first boot
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config


def main() -> int:
    ap = argparse.ArgumentParser(description="Wipe DB + uploads to factory state.")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--backup", action="store_true",
                    help="copy the current DB to database/backups/ before wiping")
    args = ap.parse_args()

    db_path = Path(Config.DATABASE_PATH)
    uploads_dir = Path(Config.UPLOAD_DIR)

    print(f"DB file : {db_path}")
    print(f"Uploads : {uploads_dir}")

    if not args.yes:
        ans = input("This will permanently delete all data. Type 'WIPE' to continue: ")
        if ans.strip() != "WIPE":
            print("Aborted.")
            return 1

    if args.backup and db_path.exists():
        backup_dir = ROOT / "database" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        dst = backup_dir / f"pre_wipe_{stamp}.sqlite"
        shutil.copy2(db_path, dst)
        print(f"Backup saved: {dst}")

    if db_path.exists():
        db_path.unlink()
        print(f"Deleted: {db_path}")
    else:
        print(f"(no DB file at {db_path}; nothing to delete)")

    # Also drop any WAL/journal siblings.
    for suffix in ("-journal", "-wal", "-shm"):
        sib = db_path.with_name(db_path.name + suffix)
        if sib.exists():
            sib.unlink()
            print(f"Deleted: {sib}")

    if uploads_dir.exists():
        removed = 0
        for f in uploads_dir.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                f.unlink()
                removed += 1
        print(f"Removed {removed} attachment file(s) from {uploads_dir}")

    print("Done. Next app start will create a fresh DB and show first-launch setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
