"""
Budget blueprint:
  - Unified monthly bucket (read, force-change)
  - Category sub-buckets (CRUD + slider)
  - Recurring payments (CRUD + trigger detection + per-month confirmation)
  - Dashboard summary endpoint

All money in fils on the wire-to-frontend side too — service converts to AED.
"""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required
from services.money import aed_to_fils, fils_to_aed
from services.timeutil import current_month_key, today, days_in_month
from services import budget_service as bs

bp = Blueprint("budget", __name__, url_prefix="/api/budget")


# ---------- summary (powers Dashboard) ----------

@bp.get("/summary")
@login_required
def summary():
    db = get_db()
    month = request.args.get("month") or current_month_key()

    budget = bs.get_monthly_budget(month, db)
    spent = bs.month_spend(month, db)
    income = bs.month_budget_income(month, db)
    cascade = bs.cascade_into(month, db)
    remaining = bs.remaining_budget(month, db)
    per_day = bs.per_day_remaining(month, db) if month == current_month_key() else 0
    upcoming = bs.upcoming_recurring_total(month, db) if month == current_month_key() else 0

    bank = bs.bank_balance(db)
    petty = bs.petty_balance(db)
    savings = bs.savings_balance(db)

    # "Saved this month" — conceptual display: 90% of current month's projected positive remainder.
    # Per spec §4 this is "Savings accumulated this month". If the month is in progress,
    # we show the projection; once closed, budget_history has the real number.
    closed = db.execute("SELECT savings_amount FROM budget_history WHERE month = ?", (month,)).fetchone()
    if closed:
        saved_this_month = int(closed["savings_amount"])
    else:
        saved_this_month = max(0, (remaining * 9) // 10)

    return jsonify({
        "month": month,
        "monthly_budget": budget,
        "monthly_budget_aed": str(fils_to_aed(budget)),
        "spent": spent,
        "spent_aed": str(fils_to_aed(spent)),
        "budget_income": income,
        "budget_income_aed": str(fils_to_aed(income)),
        "cascade_in": cascade,
        "cascade_in_aed": str(fils_to_aed(cascade)),
        "remaining": remaining,
        "remaining_aed": str(fils_to_aed(remaining)),
        "per_day": per_day,
        "per_day_aed": str(fils_to_aed(per_day)),
        "upcoming_recurring": upcoming,
        "upcoming_recurring_aed": str(fils_to_aed(upcoming)),
        "bank": bank,
        "bank_aed": str(fils_to_aed(bank)),
        "petty": petty,
        "petty_aed": str(fils_to_aed(petty)),
        "savings": savings,
        "savings_aed": str(fils_to_aed(savings)),
        "saved_this_month": saved_this_month,
        "saved_this_month_aed": str(fils_to_aed(saved_this_month)),
    })


# ---------- monthly budget set / change ----------

@bp.get("/monthly")
@login_required
def get_monthly():
    month = request.args.get("month") or current_month_key()
    db = get_db()
    amount = bs.get_monthly_budget(month, db)
    return jsonify(month=month, amount=amount, amount_aed=str(fils_to_aed(amount)))


@bp.put("/monthly")
@admin_required
def set_monthly():
    """Force-change the unified monthly budget. §6.1: applies forward only."""
    data = request.get_json(silent=True) or {}
    month = (data.get("month") or current_month_key()).strip()
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if amount <= 0:
        return jsonify(error="amount must be positive"), 400

    db = get_db()
    existing = db.execute("SELECT amount FROM monthly_budgets WHERE month = ?", (month,)).fetchone()
    if existing:
        old = int(existing["amount"])
        db.execute("UPDATE monthly_budgets SET amount = ?, updated_at = datetime('now') WHERE month = ?",
                   (amount, month))
        db.execute(
            "INSERT INTO audit_log (event_type, description, related_type) "
            "VALUES ('budget_change', ?, 'monthly_budget')",
            (f"Budget for {month}: {old/100:.2f} → {amount/100:.2f}",),
        )
    else:
        db.execute("INSERT INTO monthly_budgets (month, amount) VALUES (?, ?)", (month, amount))
        db.execute(
            "INSERT INTO audit_log (event_type, description, related_type) "
            "VALUES ('budget_set', ?, 'monthly_budget')",
            (f"Budget set for {month}: {amount/100:.2f}",),
        )
    db.commit()
    return jsonify(ok=True, month=month, amount=amount)


# ---------- category sub-buckets (§6.2) ----------

@bp.get("/categories")
@login_required
def list_category_budgets():
    month = request.args.get("month") or current_month_key()
    db = get_db()
    rows = db.execute(
        "SELECT cb.id, cb.category_id, cb.allocated_amount, c.name "
        "FROM category_budgets cb JOIN categories c ON c.id = cb.category_id "
        "WHERE cb.month = ? ORDER BY c.name",
        (month,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # spend so far on this category this month (budget-consuming types only)
        spend = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE is_deleted = 0 AND strftime('%Y-%m', date) = ? "
            "AND type IN ('expense','recurring','loan_repay_owed') AND category_id = ?",
            (month, r["category_id"]),
        ).fetchone()["s"]
        d["spent"] = int(spend or 0)
        d["spent_aed"] = str(fils_to_aed(d["spent"]))
        d["allocated_aed"] = str(fils_to_aed(d["allocated_amount"]))
        out.append(d)
    return jsonify(month=month, allocations=out)


@bp.post("/categories")
@admin_required
def add_category_budget():
    """Create a category sub-bucket. Per §6.2 this *also reduces* the unified
    bucket by the allocated amount (the category bucket lives inside it)."""
    data = request.get_json(silent=True) or {}
    month = (data.get("month") or current_month_key()).strip()
    category_id = data.get("category_id")
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if not category_id or amount <= 0:
        return jsonify(error="category and positive amount required"), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM category_budgets WHERE month = ? AND category_id = ?",
        (month, category_id),
    ).fetchone()
    if existing:
        return jsonify(error="category budget already exists; use PUT to adjust"), 400

    # Reduce unified bucket.
    unified = db.execute("SELECT amount FROM monthly_budgets WHERE month = ?", (month,)).fetchone()
    if not unified:
        return jsonify(error="set the monthly budget first"), 400
    if int(unified["amount"]) < amount:
        return jsonify(error="not enough unified budget remaining to allocate this category"), 400
    db.execute("UPDATE monthly_budgets SET amount = amount - ?, updated_at = datetime('now') WHERE month = ?",
               (amount, month))
    db.execute("INSERT INTO category_budgets (month, category_id, allocated_amount) VALUES (?, ?, ?)",
               (month, category_id, amount))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_budget_create', ?, 'category', ?)",
        (f"Allocated {amount/100:.2f} to category #{category_id} for {month}", category_id),
    )
    db.commit()
    return jsonify(ok=True), 201


@bp.put("/categories/<int:cb_id>")
@admin_required
def adjust_category_budget(cb_id: int):
    """Slider — only redistributes allocation within the unified bucket.
    Per §6.2 the unified total does NOT change here."""
    data = request.get_json(silent=True) or {}
    try:
        new_amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400

    db = get_db()
    row = db.execute("SELECT month, allocated_amount, category_id FROM category_budgets WHERE id = ?",
                     (cb_id,)).fetchone()
    if not row:
        return jsonify(error="not found"), 404

    delta = new_amount - int(row["allocated_amount"])
    # Slider must keep unified total constant: if we ADD to category, we take from unified;
    # if we REMOVE from category, we give back to unified.
    unified = db.execute("SELECT amount FROM monthly_budgets WHERE month = ?", (row["month"],)).fetchone()
    if delta > 0 and int(unified["amount"]) < delta:
        return jsonify(error="not enough unified remaining to increase this category"), 400
    db.execute("UPDATE monthly_budgets SET amount = amount - ?, updated_at = datetime('now') WHERE month = ?",
               (delta, row["month"]))
    db.execute("UPDATE category_budgets SET allocated_amount = ? WHERE id = ?", (new_amount, cb_id))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_budget_adjust', ?, 'category_budget', ?)",
        (f"Adjusted category budget #{cb_id} for {row['month']}: "
         f"{row['allocated_amount']/100:.2f} → {new_amount/100:.2f}", cb_id),
    )
    db.commit()
    return jsonify(ok=True)


@bp.delete("/categories/<int:cb_id>")
@admin_required
def delete_category_budget(cb_id: int):
    """Releases the allocation back to the unified bucket."""
    db = get_db()
    row = db.execute("SELECT month, allocated_amount FROM category_budgets WHERE id = ?", (cb_id,)).fetchone()
    if not row:
        return jsonify(error="not found"), 404
    db.execute("UPDATE monthly_budgets SET amount = amount + ?, updated_at = datetime('now') WHERE month = ?",
               (int(row["allocated_amount"]), row["month"]))
    db.execute("DELETE FROM category_budgets WHERE id = ?", (cb_id,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_budget_delete', ?, 'category_budget', ?)",
        (f"Released {row['allocated_amount']/100:.2f} from category budget #{cb_id}", cb_id),
    )
    db.commit()
    return jsonify(ok=True)


# ---------- recurring payments (§6.5) ----------

def _serialize_recurring(db, r) -> dict:
    d = dict(r)
    d["base_amount_aed"] = str(fils_to_aed(d["base_amount"]))
    if r["category_id"]:
        cat = db.execute("SELECT name FROM categories WHERE id = ?", (r["category_id"],)).fetchone()
        d["category_name"] = cat["name"] if cat else None
    else:
        d["category_name"] = None
    return d


@bp.get("/recurring")
@login_required
def list_recurring():
    db = get_db()
    rows = db.execute("SELECT * FROM recurring_payments ORDER BY is_active DESC, description").fetchall()
    return jsonify(recurring=[_serialize_recurring(db, r) for r in rows])


@bp.post("/recurring")
@admin_required
def create_recurring():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    source = (data.get("source") or "").strip().lower()
    start_date = (data.get("start_date") or today().isoformat()).strip()
    if not description or source not in ("bank", "petty"):
        return jsonify(error="description and valid source required"), 400
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if amount <= 0:
        return jsonify(error="amount must be positive"), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO recurring_payments (description, base_amount, source, category_id, start_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (description, amount, source, data.get("category_id") or None, start_date),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('recurring_create', ?, 'recurring', ?)",
        (f"Created recurring '{description}' AED {amount/100:.2f} from {source}", cur.lastrowid),
    )
    db.commit()
    return jsonify(id=cur.lastrowid), 201


@bp.delete("/recurring/<int:rid>")
@admin_required
def delete_recurring(rid: int):
    """§6.5: 'cannot be paused — must be deleted and re-created'."""
    db = get_db()
    db.execute("DELETE FROM recurring_payments WHERE id = ?", (rid,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('recurring_delete', ?, 'recurring', ?)",
        (f"Deleted recurring #{rid}", rid),
    )
    db.commit()
    return jsonify(ok=True)


@bp.get("/recurring/pending")
@login_required
def pending_recurring():
    """Return any recurring payments whose trigger date is on/before today this month
    and which have not been confirmed yet."""
    db = get_db()
    month = current_month_key()
    today_d = today()
    rows = db.execute("SELECT * FROM recurring_payments WHERE is_active = 1").fetchall()
    pending = []
    y, m = today_d.year, today_d.month
    dim = days_in_month(y, m)
    for r in rows:
        start_day = int(r["start_date"].split("-")[2])
        trigger_day = min(start_day, dim)
        if trigger_day > today_d.day:
            continue  # not due yet this month
        ov = db.execute(
            "SELECT * FROM recurring_overrides WHERE recurring_id = ? AND month = ?",
            (r["id"], month),
        ).fetchone()
        if ov and ov["confirmed"]:
            continue
        pre_fill = int(ov["override_amount"]) if (ov and ov["override_amount"] is not None) else int(r["base_amount"])
        # Last month's override (if any) for "pre-fill last month's amount" rule.
        prev = db.execute(
            "SELECT override_amount FROM recurring_overrides "
            "WHERE recurring_id = ? AND month < ? AND override_amount IS NOT NULL "
            "ORDER BY month DESC LIMIT 1",
            (r["id"], month),
        ).fetchone()
        if not ov and prev and prev["override_amount"] is not None:
            pre_fill = int(prev["override_amount"])
        pending.append({
            "recurring_id": r["id"],
            "description": r["description"],
            "category_id": r["category_id"],
            "source": r["source"],
            "trigger_day": trigger_day,
            "month": month,
            "pre_fill_amount": pre_fill,
            "pre_fill_amount_aed": str(fils_to_aed(pre_fill)),
            "base_amount": int(r["base_amount"]),
            "base_amount_aed": str(fils_to_aed(r["base_amount"])),
        })
    return jsonify(pending=pending)


@bp.post("/recurring/<int:rid>/confirm")
@admin_required
def confirm_recurring(rid: int):
    """Create a 'recurring' transaction for the current month, optionally with an override amount."""
    data = request.get_json(silent=True) or {}
    month = (data.get("month") or current_month_key()).strip()
    db = get_db()
    r = db.execute("SELECT * FROM recurring_payments WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404

    override = data.get("amount")
    if override is None:
        amount = int(r["base_amount"])
        override_db = None
    else:
        try:
            amount = aed_to_fils(override)
        except (ValueError, ArithmeticError):
            return jsonify(error="invalid override amount"), 400
        if amount <= 0:
            return jsonify(error="amount must be positive"), 400
        override_db = amount

    # Idempotency: check if already confirmed.
    existing = db.execute(
        "SELECT * FROM recurring_overrides WHERE recurring_id = ? AND month = ?",
        (rid, month),
    ).fetchone()
    if existing and existing["confirmed"]:
        return jsonify(error="already confirmed for this month"), 400

    # Create the actual transaction.
    today_d = today()
    y, m = (int(x) for x in month.split("-"))
    dim = days_in_month(y, m)
    start_day = int(r["start_date"].split("-")[2])
    trigger_day = min(start_day, dim)
    tx_date = f"{y:04d}-{m:02d}-{trigger_day:02d}"
    cur = db.execute(
        "INSERT INTO transactions (date, amount, type, source, category_id, description, linked_type, linked_id) "
        "VALUES (?, ?, 'recurring', ?, ?, ?, 'recurring', ?)",
        (tx_date, amount, r["source"], r["category_id"], r["description"], rid),
    )
    tx_id = cur.lastrowid

    if existing:
        db.execute(
            "UPDATE recurring_overrides SET override_amount = ?, confirmed = 1, confirmed_at = datetime('now'), "
            "transaction_id = ? WHERE id = ?",
            (override_db, tx_id, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO recurring_overrides (recurring_id, month, override_amount, confirmed, confirmed_at, transaction_id) "
            "VALUES (?, ?, ?, 1, datetime('now'), ?)",
            (rid, month, override_db, tx_id),
        )

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('recurring_confirm', ?, 'recurring', ?)",
        (f"Confirmed recurring '{r['description']}' AED {amount/100:.2f} for {month}", rid),
    )
    db.commit()
    return jsonify(ok=True, transaction_id=tx_id)


# ---------- manual month-close (debugging / fallback when scheduler missed it) ----------

@bp.post("/close-month")
@admin_required
def close_month_now():
    data = request.get_json(silent=True) or {}
    month = (data.get("month") or "").strip()
    if not month:
        return jsonify(error="month required (YYYY-MM)"), 400
    db = get_db()
    result = bs.close_month(month, db)
    db.commit()
    return jsonify(result)
