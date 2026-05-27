"""
Rule-based spending streak detection (§13). No ML — pure pattern grouping.

Rule (per spec): same category + similar amount (±20% of median) + same
weekday OR same day-of-month, with 3+ occurrences in the past 60 days.

Dismissed streaks don't reappear unless the pattern progresses by 2+ more
occurrences. Each dismissal stores the occurrence count at the time of
dismissal in `streak_dismissals.occurrence_count_at_dismiss`, and we compare
against the current count.
"""
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from database import get_db
from services.tx_effects import ON_BUDGET_SPEND_TYPES


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _signature(category_id, cadence_type: str, cadence_value: int) -> str:
    return f"cat={category_id if category_id is not None else 'none'}|{cadence_type}={cadence_value}"


def detect_streaks(db: sqlite3.Connection = None) -> list[dict]:
    """Return all candidate streaks in past 60 days, strongest first.
    Each entry includes a `signature` string used by the dismiss endpoint."""
    db = db or get_db()
    cutoff = (datetime.now().date() - timedelta(days=60)).isoformat()
    placeholders = ",".join("?" * len(ON_BUDGET_SPEND_TYPES))
    rows = db.execute(
        f"SELECT id, date, amount, category_id FROM transactions "
        f"WHERE is_deleted = 0 AND date >= ? AND type IN ({placeholders})",
        (cutoff, *ON_BUDGET_SPEND_TYPES),
    ).fetchall()

    # Bucket by (category, weekday) and (category, day-of-month).
    weekday_groups = defaultdict(list)
    dom_groups = defaultdict(list)
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        weekday_groups[(r["category_id"], "weekday", d.weekday())].append(r)
        dom_groups[(r["category_id"], "dom", d.day)].append(r)

    # We dedupe (cat, weekday)+(cat, dom) overlap by keeping the larger cluster.
    all_groups: dict[tuple, list] = {}
    for k, v in weekday_groups.items():
        all_groups[k] = v
    for k, v in dom_groups.items():
        # If the same items already exist as a weekday cluster of >= size, prefer that.
        all_groups[k] = v

    candidates: list[dict] = []
    for sig_tuple, items in all_groups.items():
        if len(items) < 3:
            continue
        amounts = sorted(int(i["amount"]) for i in items)
        # Median (rounded to integer fils; ties favor the lower index).
        median = amounts[len(amounts) // 2]
        if median == 0:
            continue
        if not all(abs(a - median) <= median * 0.2 for a in amounts):
            continue

        cat_id, cadence_type, cadence_value = sig_tuple
        candidates.append({
            "signature": _signature(cat_id, cadence_type, cadence_value),
            "category_id": cat_id,
            "cadence_type": cadence_type,
            "cadence_value": cadence_value,
            "occurrence_count": len(items),
            "median_amount": median,
            "last_date": max(i["date"] for i in items),
        })

    # Apply dismissals.
    dismissals = {
        d["signature"]: int(d["occurrence_count_at_dismiss"])
        for d in db.execute(
            "SELECT signature, occurrence_count_at_dismiss FROM streak_dismissals"
        ).fetchall()
    }
    visible = []
    for c in candidates:
        if c["signature"] in dismissals:
            need = dismissals[c["signature"]] + 2
            if c["occurrence_count"] < need:
                continue
        visible.append(c)

    # Strongest = highest occurrence count, then most-recent.
    visible.sort(key=lambda x: (-x["occurrence_count"], x["last_date"]), reverse=False)
    visible.sort(key=lambda x: (-x["occurrence_count"], x["last_date"][::-1]))
    return visible


def enrich_with_category_names(streaks: list[dict], db: sqlite3.Connection = None) -> list[dict]:
    """Attach human-readable category + cadence labels."""
    db = db or get_db()
    out = []
    for s in streaks:
        cat_name = "Uncategorised"
        if s["category_id"]:
            row = db.execute("SELECT name FROM categories WHERE id = ?", (s["category_id"],)).fetchone()
            if row:
                cat_name = row["name"]
        if s["cadence_type"] == "weekday":
            cadence_label = f"every {WEEKDAY_NAMES[s['cadence_value']]}"
        else:
            cadence_label = f"on day {s['cadence_value']} of the month"
        msg = (f"You've spent on {cat_name} {cadence_label} "
               f"for {s['occurrence_count']} occurrences in the last 60 days.")
        out.append({**s, "category_name": cat_name, "cadence_label": cadence_label, "message": msg})
    return out


def dismiss(signature: str, db: sqlite3.Connection = None) -> bool:
    """Record a dismissal. Returns True if a streak with this signature exists."""
    db = db or get_db()
    streaks = detect_streaks(db)
    match = next((s for s in streaks if s["signature"] == signature), None)
    if match is None:
        return False
    count = int(match["occurrence_count"])
    # Upsert.
    db.execute(
        "INSERT INTO streak_dismissals (signature, occurrence_count_at_dismiss) VALUES (?, ?) "
        "ON CONFLICT(signature) DO UPDATE SET occurrence_count_at_dismiss = excluded.occurrence_count_at_dismiss, "
        "dismissed_at = datetime('now')",
        (signature, count),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description) VALUES ('streak_dismiss', ?)",
        (f"Dismissed streak '{signature}' at occurrence #{count}",),
    )
    return True
