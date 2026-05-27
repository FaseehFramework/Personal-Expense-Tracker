"""
Loans blueprint — Section 8. Two directions:
  - direction='owed': others owe you. Lending = off-budget outflow.
    Repayments received = off-budget inflow.
  - direction='owe' : you owe others. Borrowing creates no transaction
    (the cash arrival is a separate income event the user logs themselves).
    Each repayment YOU make is an on-budget expense.

Both directions support partial repayments with a running balance.
"""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required
from services.money import aed_to_fils, fils_to_aed
from services.timeutil import today

bp = Blueprint("loans", __name__, url_prefix="/api/loans")


def _payments_total(db, loan_id: int) -> int:
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM loan_payments WHERE loan_id = ?",
        (loan_id,),
    ).fetchone()
    return int(row["s"] or 0)


def _serialize(db, l) -> dict:
    paid = _payments_total(db, l["id"])
    remaining = int(l["total_amount"]) - paid
    return {
        **dict(l),
        "amount_aed": str(fils_to_aed(l["total_amount"])),
        "paid": paid,
        "paid_aed": str(fils_to_aed(paid)),
        "remaining": remaining,
        "remaining_aed": str(fils_to_aed(remaining)),
        "progress_pct": (paid * 100 // int(l["total_amount"])) if l["total_amount"] else 0,
    }


def _update_status(db, loan_id: int) -> str:
    """Compute and persist loan status from payment total. Returns new status."""
    l = db.execute("SELECT total_amount FROM loans WHERE id = ?", (loan_id,)).fetchone()
    paid = _payments_total(db, loan_id)
    if paid <= 0:
        s = "outstanding"
    elif paid >= int(l["total_amount"]):
        s = "settled"
    else:
        s = "partial"
    db.execute("UPDATE loans SET status = ? WHERE id = ?", (s, loan_id))
    return s


# ---------- list ----------

@bp.get("")
@login_required
def list_loans():
    db = get_db()
    rows = db.execute("SELECT * FROM loans ORDER BY date DESC, id DESC").fetchall()
    out = [_serialize(db, r) for r in rows]
    summary = {
        "owed_to_me_outstanding": sum(r["remaining"] for r in out if r["direction"] == "owed" and r["status"] != "settled"),
        "i_owe_outstanding": sum(r["remaining"] for r in out if r["direction"] == "owe" and r["status"] != "settled"),
    }
    summary["owed_to_me_outstanding_aed"] = str(fils_to_aed(summary["owed_to_me_outstanding"]))
    summary["i_owe_outstanding_aed"] = str(fils_to_aed(summary["i_owe_outstanding"]))
    return jsonify(loans=out, summary=summary)


# ---------- create ----------

@bp.post("")
@admin_required
def create_loan():
    """Create a loan. For direction='owed' (others owe you), also book the
    off-budget outflow as a `loan_lend` transaction so wallet balances stay
    consistent. For direction='owe' we DON'T create a transaction — the user
    decides separately whether the money came in (e.g. as bank income)."""
    data = request.get_json(silent=True) or {}
    direction = (data.get("direction") or "").strip().lower()
    party = (data.get("party_description") or "").strip()
    notes = (data.get("notes") or "").strip() or None
    if direction not in ("owe", "owed") or not party:
        return jsonify(error="direction must be 'owe' or 'owed'; party_description required"), 400
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if amount <= 0:
        return jsonify(error="amount must be positive"), 400

    date_s = (data.get("date") or today().isoformat()).strip()
    source = (data.get("source") or "bank").strip().lower()
    if source not in ("bank", "petty"):
        return jsonify(error="source must be 'bank' or 'petty'"), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO loans (direction, party_description, total_amount, date, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (direction, party, amount, date_s, notes),
    )
    loan_id = cur.lastrowid

    # For lending OUT (someone now owes us), create the off-budget outflow.
    if direction == "owed":
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
            "VALUES (?, ?, 'loan_lend', ?, ?, 'loan', ?)",
            (date_s, amount, source, f"Lent to {party}", loan_id),
        )

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('loan_create', ?, 'loan', ?)",
        (f"Created {direction} loan: {party} AED {amount/100:.2f}", loan_id),
    )
    db.commit()
    return jsonify(id=loan_id), 201


# ---------- detail / payments ----------

@bp.get("/<int:lid>")
@login_required
def loan_detail(lid: int):
    db = get_db()
    l = db.execute("SELECT * FROM loans WHERE id = ?", (lid,)).fetchone()
    if not l:
        return jsonify(error="not found"), 404
    payments = db.execute(
        "SELECT id, amount, date, is_budget_expense, transaction_id, notes "
        "FROM loan_payments WHERE loan_id = ? ORDER BY date, id",
        (lid,),
    ).fetchall()
    # Build running-balance history.
    history = []
    remaining = int(l["total_amount"])
    for p in payments:
        remaining -= int(p["amount"])
        history.append({
            **dict(p),
            "amount_aed": str(fils_to_aed(p["amount"])),
            "remaining_after": remaining,
            "remaining_after_aed": str(fils_to_aed(remaining)),
        })
    return jsonify(loan=_serialize(db, l), payments=history)


@bp.post("/<int:lid>/payments")
@admin_required
def add_payment(lid: int):
    data = request.get_json(silent=True) or {}
    db = get_db()
    l = db.execute("SELECT * FROM loans WHERE id = ?", (lid,)).fetchone()
    if not l:
        return jsonify(error="not found"), 404

    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if amount <= 0:
        return jsonify(error="amount must be positive"), 400

    paid = _payments_total(db, lid)
    if paid + amount > int(l["total_amount"]):
        return jsonify(error="payment would exceed remaining balance"), 400

    date_s = (data.get("date") or today().isoformat()).strip()
    source = (data.get("source") or "bank").strip().lower()
    if source not in ("bank", "petty"):
        return jsonify(error="source must be 'bank' or 'petty'"), 400
    notes = (data.get("notes") or "").strip() or None

    is_budget_expense = 1 if l["direction"] == "owe" else 0
    tx_type = "loan_repay_owed" if l["direction"] == "owe" else "loan_repay_received"
    desc = f"Repayment to {l['party_description']}" if l["direction"] == "owe" \
           else f"Repayment from {l['party_description']}"

    tx_cur = db.execute(
        "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
        "VALUES (?, ?, ?, ?, ?, 'loan', ?)",
        (date_s, amount, tx_type, source, desc, lid),
    )
    tx_id = tx_cur.lastrowid

    pay_cur = db.execute(
        "INSERT INTO loan_payments (loan_id, amount, date, is_budget_expense, transaction_id, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (lid, amount, date_s, is_budget_expense, tx_id, notes),
    )
    new_status = _update_status(db, lid)
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('loan_payment', ?, 'loan', ?)",
        (f"Loan #{lid} payment AED {amount/100:.2f} ({new_status})", lid),
    )
    db.commit()
    return jsonify(payment_id=pay_cur.lastrowid, transaction_id=tx_id, status=new_status), 201


@bp.delete("/<int:lid>/payments/<int:pid>")
@admin_required
def delete_payment(lid: int, pid: int):
    db = get_db()
    p = db.execute(
        "SELECT * FROM loan_payments WHERE id = ? AND loan_id = ?",
        (pid, lid),
    ).fetchone()
    if not p:
        return jsonify(error="not found"), 404
    # Soft-delete the linked transaction too, so wallet/budget math stays consistent.
    if p["transaction_id"]:
        db.execute(
            "UPDATE transactions SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ?",
            (p["transaction_id"],),
        )
    db.execute("DELETE FROM loan_payments WHERE id = ?", (pid,))
    _update_status(db, lid)
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('loan_payment_delete', ?, 'loan', ?)",
        (f"Deleted payment #{pid} on loan #{lid}", lid),
    )
    db.commit()
    return jsonify(ok=True)


@bp.delete("/<int:lid>")
@admin_required
def delete_loan(lid: int):
    db = get_db()
    l = db.execute("SELECT direction FROM loans WHERE id = ?", (lid,)).fetchone()
    if not l:
        return jsonify(error="not found"), 404
    # Soft-delete linked transactions (lend + repayments).
    db.execute(
        "UPDATE transactions SET is_deleted = 1, deleted_at = datetime('now') "
        "WHERE linked_type = 'loan' AND linked_id = ?",
        (lid,),
    )
    db.execute("DELETE FROM loans WHERE id = ?", (lid,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('loan_delete', ?, 'loan', ?)",
        (f"Deleted loan #{lid} and its linked transactions", lid),
    )
    db.commit()
    return jsonify(ok=True)
