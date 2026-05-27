"""
Receivables blueprint — Section 7.
Off-budget while outstanding. Settlement creates an off-budget inflow into
the chosen wallet. Convert-to-expense retroactively books an expense in the
original month (with cascade-aware re-close of any already-sealed months).
"""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required
from services.money import aed_to_fils, fils_to_aed
from services.timeutil import today, month_key
from services import budget_service as bs

bp = Blueprint("receivables", __name__, url_prefix="/api/receivables")


def _serialize(r) -> dict:
    d = dict(r)
    d["amount_aed"] = str(fils_to_aed(d["amount"]))
    return d


@bp.get("")
@login_required
def list_receivables():
    db = get_db()
    rows = db.execute("SELECT * FROM receivables ORDER BY date_logged DESC, id DESC").fetchall()
    out = [_serialize(r) for r in rows]
    totals = {
        "outstanding": sum(r["amount"] for r in rows if r["status"] == "outstanding"),
        "settled": sum(r["amount"] for r in rows if r["status"] == "settled"),
        "converted": sum(r["amount"] for r in rows if r["status"] == "converted"),
    }
    return jsonify(receivables=out, totals={**totals,
        "outstanding_aed": str(fils_to_aed(totals["outstanding"])),
        "settled_aed": str(fils_to_aed(totals["settled"])),
        "converted_aed": str(fils_to_aed(totals["converted"])),
    })


@bp.post("")
@admin_required
def create_receivable():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify(error="description required"), 400
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount required"), 400
    if amount <= 0:
        return jsonify(error="amount must be positive"), 400

    date_logged = (data.get("date") or today().isoformat()).strip()
    month = (data.get("month") or "").strip() or date_logged[:7]

    db = get_db()
    cur = db.execute(
        "INSERT INTO receivables (description, amount, date_logged, month) VALUES (?, ?, ?, ?)",
        (description, amount, date_logged, month),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_create', ?, 'receivable', ?)",
        (f"Logged receivable '{description}' AED {amount/100:.2f} for {month}", cur.lastrowid),
    )
    db.commit()
    return jsonify(id=cur.lastrowid), 201


@bp.post("/<int:rid>/settle")
@admin_required
def settle_receivable(rid: int):
    """Settlement (§7.3) — full only. Destination chooses Bank or Petty."""
    data = request.get_json(silent=True) or {}
    destination = (data.get("destination") or "").strip().lower()
    if destination not in ("bank", "petty"):
        return jsonify(error="destination must be 'bank' or 'petty'"), 400

    db = get_db()
    r = db.execute("SELECT * FROM receivables WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404
    if r["status"] != "outstanding":
        return jsonify(error=f"cannot settle (current status: {r['status']})"), 400

    settle_date = (data.get("date") or today().isoformat()).strip()
    # Off-budget inflow into the chosen wallet.
    tx_type = "income_bank" if destination == "bank" else "income_petty_external"
    cur = db.execute(
        "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
        "VALUES (?, ?, ?, ?, ?, 'receivable', ?)",
        (settle_date, r["amount"], tx_type, destination,
         f"Receivable settled: {r['description']}", rid),
    )
    db.execute(
        "UPDATE receivables SET status = 'settled', settlement_date = ?, settlement_destination = ? "
        "WHERE id = ?",
        (settle_date, destination, rid),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_settle', ?, 'receivable', ?)",
        (f"Settled receivable #{rid} into {destination}", rid),
    )
    db.commit()
    return jsonify(ok=True, transaction_id=cur.lastrowid)


@bp.post("/<int:rid>/convert")
@admin_required
def convert_to_expense(rid: int):
    """§7.4 — convert an unreimbursed receivable into a retroactive expense.
    Books the expense in the receivable's original month and, if that month
    was already closed, replays the close-month cascade forward."""
    db = get_db()
    r = db.execute("SELECT * FROM receivables WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404
    if r["status"] != "outstanding":
        return jsonify(error=f"cannot convert (current status: {r['status']})"), 400

    # Land the expense on the receivable's logged date so it falls in its month.
    cur = db.execute(
        "INSERT INTO transactions (date, amount, type, source, description, linked_type, linked_id) "
        "VALUES (?, ?, 'expense', 'bank', ?, 'receivable', ?)",
        (r["date_logged"], r["amount"], f"Converted receivable: {r['description']}", rid),
    )
    tx_id = cur.lastrowid

    db.execute(
        "UPDATE receivables SET status = 'converted', converted_at = datetime('now') WHERE id = ?",
        (rid,),
    )

    # If the receivable's month is already closed, replay the cascade forward.
    closed = db.execute("SELECT 1 FROM budget_history WHERE month = ?", (r["month"],)).fetchone()
    replayed = []
    pot_went_negative = False
    if closed:
        old_pot = bs.savings_balance(db)
        replayed = bs.reapply_closed_months_from(r["month"], db)
        new_pot = bs.savings_balance(db)
        if new_pot < 0:
            pot_went_negative = True
            _log_negative_pot(db, rid, r, old_pot, new_pot, replayed)

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_convert', ?, 'receivable', ?)",
        (f"Converted receivable #{rid} to expense AED {r['amount']/100:.2f} for {r['month']}", rid),
    )
    db.commit()
    return jsonify(
        ok=True,
        transaction_id=tx_id,
        replayed_months=replayed,
        pot_went_negative=pot_went_negative,
    )


def _log_negative_pot(db, rid: int, r, old_pot: int, new_pot: int, replayed: list) -> None:
    """Rich audit entry written whenever a retro-convert pushes the pot below zero.

    Identifies any wishlist purchases whose savings_drawn contributed to the
    over-draw — i.e. purchases on or after the receivable's month, since those
    drew from a pot that included this month's now-shrunken savings credit.
    """
    draws = db.execute(
        """
        SELECT w.id, w.item_name, w.savings_drawn, w.transaction_id,
               t.date AS purchase_date
          FROM wishlist w
          LEFT JOIN transactions t ON t.id = w.transaction_id
         WHERE w.status = 'purchased' AND w.savings_drawn > 0
           AND (t.date IS NULL OR t.date >= ?)
         ORDER BY t.date, w.id
        """,
        (f"{r['month']}-01",),
    ).fetchall()

    draw_lines = [
        f"wishlist #{d['id']} '{d['item_name']}' drew AED {d['savings_drawn']/100:.2f}"
        + (f" on {d['purchase_date']}" if d['purchase_date'] else "")
        for d in draws
    ] or ["no contributing wishlist draws found"]
    desc = (
        f"Receivable #{rid} convert ({r['month']}) caused savings pot to go negative. "
        f"Old pot AED {old_pot/100:.2f} → new pot AED {new_pot/100:.2f}. "
        f"Replayed months: {', '.join(replayed) if replayed else 'none'}. "
        f"Contributing draws: " + "; ".join(draw_lines) + "."
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('savings_pot_negative', ?, 'receivable', ?)",
        (desc, rid),
    )


@bp.delete("/<int:rid>")
@admin_required
def delete_receivable(rid: int):
    db = get_db()
    r = db.execute("SELECT status FROM receivables WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404
    if r["status"] != "outstanding":
        return jsonify(error="only outstanding receivables can be deleted"), 400
    db.execute("DELETE FROM receivables WHERE id = ?", (rid,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_delete', ?, 'receivable', ?)",
        (f"Deleted outstanding receivable #{rid}", rid),
    )
    db.commit()
    return jsonify(ok=True)
