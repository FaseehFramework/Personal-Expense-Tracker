"""
Receivables blueprint — Section 7.
Off-budget while outstanding. Settlement creates an off-budget inflow into
the chosen wallet. Convert-to-expense retroactively books an expense in the
original month (with cascade-aware re-close of any already-sealed months).

Entry point: Receivables are no longer created here directly.
They are created by logging a transaction with type='receivable' in the
Transactions tab. The POST /api/receivables endpoint has been removed (§7 change).
This blueprint now handles: listing, settlement, conversion, and deletion only.
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
def create_receivable_removed():
    """§7 (updated): receivables are now created via the Transactions tab.
    Log a transaction with type='receivable' — the receivable row is auto-created.
    This endpoint is no longer an entry point and returns 410 Gone."""
    return jsonify(
        error=(
            "Direct receivable creation has been removed. "
            "Log a transaction with type='receivable' in the Transactions tab instead."
        )
    ), 410


@bp.post("/<int:rid>/settle")
@admin_required
def settle_receivable(rid: int):
    """Settlement (§7.3) — full only. Destination chooses Bank or Petty.
    Credits the reimbursement amount back to the chosen wallet as an off-budget
    income transaction."""
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

    # Off-budget inflow into the chosen wallet (reimbursement credit).
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
        (
            f"Settled receivable #{rid} '{r['description']}' "
            f"AED {r['amount']/100:.2f} into {destination} on {settle_date}",
            rid,
        ),
    )
    db.commit()
    return jsonify(ok=True, transaction_id=cur.lastrowid)


@bp.post("/<int:rid>/convert")
@admin_required
def convert_to_expense(rid: int):
    """§7.4 — convert an unreimbursed receivable into a retroactive expense.
    Books the expense in the receivable's original month and, if that month
    was already closed, replays the close-month cascade forward.
    If the receivable was created via a transaction (has transaction_id), that
    transaction's type is updated from 'receivable' to 'expense' so it appears
    as a budget-consuming item in the transaction list."""
    db = get_db()
    r = db.execute("SELECT * FROM receivables WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404
    if r["status"] != "outstanding":
        return jsonify(error=f"cannot convert (current status: {r['status']})"), 400

    # If this receivable was created via the Transactions tab, update the
    # existing transaction's type to 'expense' so the transaction list reflects
    # the reclassification. We also update source to the same source used when
    # the receivable was originally logged (preserved on the tx row).
    if r["transaction_id"]:
        db.execute(
            "UPDATE transactions SET type = 'expense', updated_at = datetime('now') WHERE id = ?",
            (r["transaction_id"],),
        )
        db.execute(
            "INSERT INTO transaction_edits (transaction_id, field_name, old_value, new_value) "
            "VALUES (?, 'type', 'receivable', 'expense')",
            (r["transaction_id"],),
        )
        tx_id = r["transaction_id"]
    else:
        # Legacy receivable (no linked transaction) — create a new expense transaction.
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

    cascade_triggered = bool(replayed)
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_convert', ?, 'receivable', ?)",
        (
            f"Converted receivable #{rid} '{r['description']}' to expense "
            f"AED {r['amount']/100:.2f} for {r['month']}; "
            f"cascade_triggered={'yes' if cascade_triggered else 'no'}; "
            f"affected_months={', '.join(replayed) if replayed else 'none'}",
            rid,
        ),
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
    """Delete an outstanding receivable.

    For new-style receivables (created via the Transactions tab, transaction_id set):
    the linked transaction is also soft-deleted; the user must delete from the
    Transactions tab to remove the transaction proper (or it can be done here).

    For legacy receivables (transaction_id = NULL): deletes the receivable record only.
    """
    db = get_db()
    r = db.execute("SELECT * FROM receivables WHERE id = ?", (rid,)).fetchone()
    if not r:
        return jsonify(error="not found"), 404
    if r["status"] != "outstanding":
        return jsonify(error="only outstanding receivables can be deleted"), 400

    # If linked to a transaction, soft-delete that transaction too.
    if r["transaction_id"]:
        db.execute(
            "UPDATE transactions SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ?",
            (r["transaction_id"],),
        )
        db.execute(
            "INSERT INTO audit_log (event_type, description, related_type, related_id) "
            "VALUES ('transaction_delete', ?, 'transaction', ?)",
            (f"Soft-deleted tx #{r['transaction_id']} (linked receivable #{rid} deleted)", r["transaction_id"]),
        )

    db.execute("DELETE FROM receivables WHERE id = ?", (rid,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('receivable_delete', ?, 'receivable', ?)",
        (f"Deleted outstanding receivable #{rid} '{r['description']}'", rid),
    )
    db.commit()
    return jsonify(ok=True)
