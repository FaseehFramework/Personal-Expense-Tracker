import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # Override SECRET_KEY in production via environment variable.
    SECRET_KEY = os.environ.get("EXPENSE_TRACKER_SECRET", "dev-secret-change-me")
    DATABASE_PATH = os.environ.get(
        "EXPENSE_TRACKER_DB",
        str(BASE_DIR / "database" / "expense_tracker.sqlite"),
    )
    SCHEMA_PATH = str(BASE_DIR / "database" / "schema.sql")
    UPLOAD_DIR = str(BASE_DIR / "static" / "uploads")
    MAX_UPLOAD_BYTES = 5 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}

    # All month-end / recurring logic runs in this timezone.
    TIMEZONE = "Asia/Dubai"

    # Money is stored as integer fils (1 AED = 100 fils) everywhere in the DB
    # and converted to/from decimal AED only at API/UI boundaries.
    FILS_PER_AED = 100

    DEFAULT_MONTHLY_BUDGET_AED = 2500

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
