"""
Budget engine. Pure read/calc functions + state-mutating month-end logic.

Money invariant: every value here is in integer fils.

The unified monthly budget is *separate* from wallet balances. The budget
is essentially a "spending allowance" for the month — it has nothing to
do with how much cash is in the bank. The dashboard tile "Remaining budget"
shows:

    monthly_budget(month)
    + sum(budget_delta for tx in month where tx is budget income)
    - sum(amount for tx in month where tx is budget-consuming)
    - sum(active_category_budget_total for month)? NO — category buckets
      live INSIDE the unified bucket; they don't change the total. See §6.2.
    - any negative cascade carried into this month

When a category sub-bucket is *created*, it deducts from the unified
remaining (see add_category_budget). The slider only redistributes.
"""
from typing import Optional
import sqlite3

from database import get_db
from services.timeutil import current_month_key, today, days_in_month, days_remaining_in_month
from services.tx_effects import (
    BUDGET_CONSUMING_TYPES,
    BUDGET_INCOME_TYPES,
)


# ---------- wallet balances ----------

def _sum_tx(db: sqlite3.Connection, where_sql: str, params=()) -> int:
    row = db.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        f"WHERE is_deleted = 0 AND {where_sql}",
        params,
    ).fetchone()
    return int(row["s"] or 0)


def bank_balance(db: sqlite3.Connection = None) -> int:
    """Compute current bank wallet balance from the transaction ledger."""
    db = db or get_db()
    # Inflows
    plus = _sum_tx(db, "(type='income_bank') OR (type='petty_to_bank') OR (type='loan_repay_received' AND source='bank')")
    # Outflows — includes off-budget types (expense_offbudget, receivable) that
    # deduct from the wallet even though they don't affect the monthly budget.
    minus = _sum_tx(
        db,
        "(type='transfer_bank_to_petty') OR "
        "(type IN ('expense','recurring','loan_repay_owed','loan_lend','expense_offbudget','receivable') AND source='bank')",
    )
    return plus - minus


def petty_balance(db: sqlite3.Connection = None) -> int:
    db = db or get_db()
    plus = _sum_tx(db, "(type='income_petty_external') OR (type='transfer_bank_to_petty') OR (type='loan_repay_received' AND source='petty')")
    minus = _sum_tx(
        db,
        "(type='petty_to_bank') OR "
        "(type IN ('expense','recurring','loan_repay_owed','loan_lend','expense_offbudget','receivable') AND source='petty')",
    )
    return plus - minus


# ---------- monthly budget reads ----------

def get_monthly_budget(month: str, db: sqlite3.Connection = None) -> int:
    """Return the unified monthly budget for `month`, in fils. 0 if not set."""
    db = db or get_db()
    row = db.execute("SELECT amount FROM monthly_budgets WHERE month = ?", (month,)).fetchone()
    return int(row["amount"]) if row else 0


def cascade_into(month: str, db: sqlite3.Connection = None) -> int:
    """How many fils of negative cascade have been carried INTO this month.
    Positive = nothing carried; >0 means the prior month was negative by this much.
    Returns absolute fils to deduct.
    """
    db = db or get_db()
    row = db.execute(
        "SELECT negative_cascade FROM budget_history "
        "WHERE month = (SELECT MAX(month) FROM budget_history WHERE month < ?)",
        (month,),
    ).fetchone()
    return int(row["negative_cascade"]) if row else 0


def month_spend(month: str, db: sqlite3.Connection = None) -> int:
    """Sum of all budget-consuming transactions in `month` (fils, positive)."""
    db = db or get_db()
    placeholders = ",".join("?" * len(BUDGET_CONSUMING_TYPES))
    row = db.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        f"WHERE is_deleted = 0 AND strftime('%Y-%m', date) = ? "
        f"AND type IN ({placeholders})",
        (month, *BUDGET_CONSUMING_TYPES),
    ).fetchone()
    return int(row["s"] or 0)


def month_budget_income(month: str, db: sqlite3.Connection = None) -> int:
    """Budget-positive transactions in `month` (e.g. petty-to-bank)."""
    db = db or get_db()
    placeholders = ",".join("?" * len(BUDGET_INCOME_TYPES))
    row = db.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
        f"WHERE is_deleted = 0 AND strftime('%Y-%m', date) = ? "
        f"AND type IN ({placeholders})",
        (month, *BUDGET_INCOME_TYPES),
    ).fetchone()
    return int(row["s"] or 0)


def remaining_budget(month: str = None, db: sqlite3.Connection = None) -> int:
    """Remaining unified budget for `month` (fils, may be negative)."""
    db = db or get_db()
    month = month or current_month_key()
    budget = get_monthly_budget(month, db)
    if budget == 0:
        return 0
    spent = month_spend(month, db)
    income = month_budget_income(month, db)
    deducted_cascade = cascade_into(month, db)
    return budget + income - spent - deducted_cascade


def upcoming_recurring_total(month: str = None, db: sqlite3.Connection = None) -> int:
    """Sum of recurring payments that still need to be confirmed THIS month
    on or before month-end (used for the smoothed per-day calculation)."""
    db = db or get_db()
    month = month or current_month_key()
    rows = db.execute(
        """
        SELECT r.id, r.base_amount, r.start_date
        FROM recurring_payments r
        WHERE r.is_active = 1
        """
    ).fetchall()
    if not rows:
        return 0

    total = 0
    today_d = today()
    y, m = (int(x) for x in month.split("-"))
    dim = days_in_month(y, m)

    for r in rows:
        # Determine this month's trigger day = min(start_day, dim).
        start_day = int(r["start_date"].split("-")[2])
        trigger_day = min(start_day, dim)
        # Has this month been confirmed?
        ov = db.execute(
            "SELECT confirmed, override_amount FROM recurring_overrides "
            "WHERE recurring_id = ? AND month = ?",
            (r["id"], month),
        ).fetchone()
        if ov and ov["confirmed"]:
            continue  # already happened — already counted in month_spend
        # Only count if trigger day is today or in the future for this month.
        if trigger_day < today_d.day and month == current_month_key():
            # In the past for the current month and not yet confirmed —
            # still counts as upcoming (overdue prompt will fire on next open).
            pass
        amount = int(ov["override_amount"]) if (ov and ov["override_amount"] is not None) else int(r["base_amount"])
        total += amount
    return total


def per_day_remaining(month: str = None, db: sqlite3.Connection = None) -> int:
    """Smoothed per-day remaining budget for `month` (fils).
    Section 4: (remaining_budget - upcoming_recurring) / days_remaining_in_month.
    """
    db = db or get_db()
    month = month or current_month_key()
    if month != current_month_key():
        return 0
    rem = remaining_budget(month, db)
    upcoming = upcoming_recurring_total(month, db)
    days = max(1, days_remaining_in_month())
    return (rem - upcoming) // days  # integer floor in fils — fine for display


# ---------- savings pot ----------

def savings_balance(db: sqlite3.Connection = None) -> int:
    db = db or get_db()
    row = db.execute("SELECT balance FROM savings_pot WHERE id = 1").fetchone()
    return int(row["balance"]) if row else 0


def credit_savings(amount: int, description: str, event_type: str = "rollover_credit",
                   db: sqlite3.Connection = None) -> None:
    """Add to savings pot + log a savings event."""
    db = db or get_db()
    if amount <= 0:
        return
    db.execute(
        "UPDATE savings_pot SET balance = balance + ?, updated_at = datetime('now') WHERE id = 1",
        (amount,),
    )
    db.execute(
        "INSERT INTO savings_events (event_type, amount, description) VALUES (?, ?, ?)",
        (event_type, amount, description),
    )


def debit_savings(amount: int, description: str, event_type: str = "wishlist_debit",
                  db: sqlite3.Connection = None) -> int:
    """Reduce savings pot by up to `amount` (capped at current balance).
    Returns the actual amount drawn down."""
    db = db or get_db()
    current = savings_balance(db)
    drawn = min(current, amount)
    if drawn > 0:
        db.execute(
            "UPDATE savings_pot SET balance = balance - ?, updated_at = datetime('now') WHERE id = 1",
            (drawn,),
        )
        db.execute(
            "INSERT INTO savings_events (event_type, amount, description) VALUES (?, ?, ?)",
            (event_type, -drawn, description),
        )
    return drawn


# ---------- month-end rollover (§6.3, §6.4) ----------

def close_month(month: str, db: sqlite3.Connection = None) -> dict:
    """Run rollover for the given month. Idempotent — skips if already closed."""
    db = db or get_db()
    existing = db.execute("SELECT id FROM budget_history WHERE month = ?", (month,)).fetchone()
    if existing:
        return {"already_closed": True, "month": month}

    budget = get_monthly_budget(month, db)
    spent = month_spend(month, db)
    income = month_budget_income(month, db)
    cascade_in = cascade_into(month, db)

    net_remaining = budget + income - spent - cascade_in  # may be negative

    rollover_amount = 0
    savings_amount = 0
    negative_cascade = 0

    if net_remaining > 0:
        # 10% rolls into next month's budget, 90% to savings pot.
        rollover_amount = net_remaining // 10
        savings_amount = net_remaining - rollover_amount
        # Apply rollover to next-month budget; create the row if missing.
        next_month = _next_month_key(month)
        existing_next = db.execute(
            "SELECT amount FROM monthly_budgets WHERE month = ?", (next_month,)
        ).fetchone()
        if existing_next:
            db.execute(
                "UPDATE monthly_budgets SET amount = amount + ?, updated_at = datetime('now') WHERE month = ?",
                (rollover_amount, next_month),
            )
        else:
            # Spec §6.3 says rollover is "added on top" — when no next-month
            # row exists, seed with just the rollover, not a phantom default
            # budget. Admin sets the real next-month budget separately, and
            # this credit stacks onto it. Keeping the seed minimal also makes
            # reopen_closed_month trivially reversible.
            db.execute(
                "INSERT INTO monthly_budgets (month, amount) VALUES (?, ?)",
                (next_month, rollover_amount),
            )
        credit_savings(savings_amount, f"Month-end savings from {month}", db=db)
    else:
        negative_cascade = -net_remaining  # positive number for the deficit

    db.execute(
        "INSERT INTO budget_history (month, budget_set, actual_spend, rollover_amount, savings_amount, negative_cascade) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (month, budget, spent, rollover_amount, savings_amount, negative_cascade),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('month_close', ?, 'month', NULL)",
        (f"Closed {month}: budget={budget}, spent={spent}, rollover={rollover_amount}, "
         f"savings={savings_amount}, cascade={negative_cascade}",),
    )
    return {
        "month": month, "budget": budget, "spent": spent,
        "rollover": rollover_amount, "savings": savings_amount,
        "cascade": negative_cascade,
    }


def _next_month_key(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    m += 1
    if m > 12:
        m = 1
        y += 1
    return f"{y:04d}-{m:02d}"


# ---------- reopen + retro-reconcile (for receivable convert-to-expense) ----------

def reopen_closed_month(month: str, db: sqlite3.Connection = None) -> bool:
    """Reverse a month-close so it can be redone with new data.
    Reverses the savings credit and the rollover bump to next month, then
    deletes the budget_history row. Idempotent — returns False if not closed.
    """
    db = db or get_db()
    row = db.execute("SELECT * FROM budget_history WHERE month = ?", (month,)).fetchone()
    if not row:
        return False

    if row["savings_amount"] > 0:
        db.execute(
            "UPDATE savings_pot SET balance = balance - ?, updated_at = datetime('now') WHERE id = 1",
            (row["savings_amount"],),
        )
        db.execute(
            "INSERT INTO savings_events (event_type, amount, description) VALUES (?, ?, ?)",
            ("reopen_debit", -int(row["savings_amount"]),
             f"Reversed month-close credit for {month}"),
        )

    if row["rollover_amount"] > 0:
        nxt = _next_month_key(month)
        # Only debit next-month if a row exists (and only if it could plausibly
        # still contain that rollover).
        nb = db.execute("SELECT amount FROM monthly_budgets WHERE month = ?", (nxt,)).fetchone()
        if nb is not None:
            db.execute(
                "UPDATE monthly_budgets SET amount = amount - ?, updated_at = datetime('now') WHERE month = ?",
                (row["rollover_amount"], nxt),
            )

    db.execute("DELETE FROM budget_history WHERE month = ?", (month,))
    db.execute(
        "INSERT INTO audit_log (event_type, description) VALUES (?, ?)",
        ("month_reopen", f"Reopened {month} for retroactive change"),
    )
    return True


def reapply_closed_months_from(month: str, db: sqlite3.Connection = None) -> list[str]:
    """Reopen-and-reclose `month` and every later already-closed month, in order.
    Used when a retroactive change (receivable -> expense conversion) lands in
    a month whose history is already sealed."""
    db = db or get_db()
    rows = db.execute(
        "SELECT month FROM budget_history WHERE month >= ? ORDER BY month",
        (month,),
    ).fetchall()
    months = [r["month"] for r in rows]
    if not months:
        return []
    # Reopen latest-first so cascade lookups stay consistent during teardown.
    for m in reversed(months):
        reopen_closed_month(m, db)
    # Re-close oldest-first so each close sees the correct cascade_into().
    for m in months:
        close_month(m, db)
    return months
