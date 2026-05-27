"""
Wishlist blueprint — Section 9.
Items planned for a target month. When target arrives (or at purchase),
the savings pot covers what it can; any shortfall reduces that month's
unified budget (with explicit user confirmation).

State on a wishlist row:
  status          : 'active' | 'purchased' | 'abandoned'
  savings_drawn   : fils actually debited from the savings pot at purchase
  budget_charged  : fils deducted from the target month's unified budget
                     at purchase (only > 0 if the user confirmed the shortfall)
"""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required
from services.money import aed_to_fils, fils_to_aed
from services.timeutil import current_month_key
from services import budget_service as bs

bp = Blueprint("wishlist", __name__, url_prefix="/api/wishlist")


def _serialize(w) -> dict:
    d = dict(w)
    d["estimated_amount_aed"] = str(fils_to_aed(d["estimated_amount"]))
    d["savings_drawn_aed"] = str(fils_to_aed(d["savings_drawn"]))
    d["budget_charged_aed"] = str(fils_to_aed(d["budget_charged"]))
    return d


def _next_priority(db) -> int:
    row = db.execute("SELECT COALESCE(MAX(priority_order), 0) AS m FROM wishlist").fetchone()
    return int(row["m"] or 0) + 1


def _projected_savings_coverage(db, item) -> tuple[int, int, int]:
    """For an active item: compute (already_reserved_by_earlier_items,
    available_for_this_item, shortfall_for_this_item)."""
    pot = bs.savings_balance(db)
    earlier = db.execute(
        "SELECT COALESCE(SUM(estimated_amount), 0) AS s FROM wishlist "
        "WHERE status = 'active' AND priority_order < ?",
        (item["priority_order"],),
    ).fetchone()["s"]
    available = max(0, pot - int(earlier))
    cover = min(int(item["estimated_amount"]), available)
    shortfall = max(0, int(item["estimated_amount"]) - cover)
    return int(earlier), cover, shortfall


# ---------- list ----------

@bp.get("")
@login_required
def list_wishlist():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM wishlist ORDER BY status = 'active' DESC, priority_order, id"
    ).fetchall()
    out = []
    pot = bs.savings_balance(db)
    # For preview math we walk active items in priority order, draining the pot.
    remaining_pot = pot
    for r in rows:
        d = _serialize(r)
        if r["status"] == "active":
            cover = min(int(r["estimated_amount"]), max(0, remaining_pot))
            shortfall = max(0, int(r["estimated_amount"]) - cover)
            remaining_pot -= cover
            d["projected_savings_cover"] = cover
            d["projected_savings_cover_aed"] = str(fils_to_aed(cover))
            d["projected_shortfall"] = shortfall
            d["projected_shortfall_aed"] = str(fils_to_aed(shortfall))
        out.append(d)
    return jsonify(items=out, savings_pot=pot, savings_pot_aed=str(fils_to_aed(pot)))


# ---------- create / abandon ----------

@bp.post("")
@admin_required
def create_item():
    data = request.get_json(silent=True) or {}
    name = (data.get("item_name") or "").strip()
    target_month = (data.get("target_month") or "").strip()
    if not name or not target_month:
        return jsonify(error="item_name and target_month required"), 400
    try:
        amount = aed_to_fils(data.get("estimated_amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="estimated_amount required"), 400
    if amount <= 0:
        return jsonify(error="estimated_amount must be positive"), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO wishlist (item_name, estimated_amount, target_month, notes, priority_order) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, amount, target_month, (data.get("notes") or "").strip() or None, _next_priority(db)),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('wishlist_create', ?, 'wishlist', ?)",
        (f"Wishlist '{name}' AED {amount/100:.2f} for {target_month}", cur.lastrowid),
    )
    db.commit()
    return jsonify(id=cur.lastrowid), 201


@bp.post("/<int:wid>/abandon")
@admin_required
def abandon_item(wid: int):
    db = get_db()
    w = db.execute("SELECT * FROM wishlist WHERE id = ?", (wid,)).fetchone()
    if not w:
        return jsonify(error="not found"), 404
    if w["status"] != "active":
        return jsonify(error=f"cannot abandon (status: {w['status']})"), 400
    # §9.4: pot unaffected (it was never drawn down for an active item).
    db.execute("UPDATE wishlist SET status = 'abandoned' WHERE id = ?", (wid,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('wishlist_abandon', ?, 'wishlist', ?)",
        (f"Abandoned wishlist '{w['item_name']}'", wid),
    )
    db.commit()
    return jsonify(ok=True)


@bp.delete("/<int:wid>")
@admin_required
def delete_item(wid: int):
    db = get_db()
    w = db.execute("SELECT status FROM wishlist WHERE id = ?", (wid,)).fetchone()
    if not w:
        return jsonify(error="not found"), 404
    if w["status"] == "purchased":
        return jsonify(error="cannot delete a purchased item; it preserves reconciliation history"), 400
    db.execute("DELETE FROM wishlist WHERE id = ?", (wid,))
    db.commit()
    return jsonify(ok=True)


# ---------- preview + purchase ----------

@bp.get("/<int:wid>/preview")
@login_required
def preview_purchase(wid: int):
    """§9.2 — show user what the savings pot will cover and the shortfall
    (if any) that will be charged to the target month's budget. UI shows this
    before asking the user to confirm."""
    db = get_db()
    w = db.execute("SELECT * FROM wishlist WHERE id = ?", (wid,)).fetchone()
    if not w:
        return jsonify(error="not found"), 404
    if w["status"] != "active":
        return jsonify(error=f"item not active (status: {w['status']})"), 400

    earlier, cover, shortfall = _projected_savings_coverage(db, w)
    return jsonify(
        item=_serialize(w),
        savings_pot=bs.savings_balance(db),
        savings_pot_aed=str(fils_to_aed(bs.savings_balance(db))),
        reserved_for_earlier=earlier,
        reserved_for_earlier_aed=str(fils_to_aed(earlier)),
        will_cover=cover,
        will_cover_aed=str(fils_to_aed(cover)),
        shortfall=shortfall,
        shortfall_aed=str(fils_to_aed(shortfall)),
        target_month=w["target_month"],
        needs_confirmation=shortfall > 0,
    )


@bp.post("/<int:wid>/purchase")
@admin_required
def purchase_item(wid: int):
    """§9.3 — log a normal expense transaction AND reconcile the savings pot.
    Body:
      {
        "date":   "YYYY-MM-DD" (optional; defaults to today),
        "source": "bank"|"petty",
        "actual_amount": AED (optional; defaults to estimated_amount),
        "category_id": <int|null>,
        "confirm_shortfall": true (REQUIRED if shortfall > 0)
      }
    """
    data = request.get_json(silent=True) or {}
    db = get_db()
    w = db.execute("SELECT * FROM wishlist WHERE id = ?", (wid,)).fetchone()
    if not w:
        return jsonify(error="not found"), 404
    if w["status"] != "active":
        return jsonify(error=f"item not active (status: {w['status']})"), 400

    source = (data.get("source") or "bank").strip().lower()
    if source not in ("bank", "petty"):
        return jsonify(error="source must be 'bank' or 'petty'"), 400

    actual_aed = data.get("actual_amount")
    if actual_aed is None:
        actual = int(w["estimated_amount"])
    else:
        try:
            actual = aed_to_fils(actual_aed)
        except (ValueError, ArithmeticError):
            return jsonify(error="actual_amount invalid"), 400
        if actual <= 0:
            return jsonify(error="actual_amount must be positive"), 400

    from services.timeutil import today
    purchase_date = (data.get("date") or today().isoformat()).strip()
    target_month = w["target_month"]

    # How much can savings cover, given the item's priority among other actives.
    earlier, cover_estimated, _ = _projected_savings_coverage(db, w)
    # The pot is drawn against the *actual* amount, but limited by what's left
    # for this item after earlier-priority items are reserved.
    available = max(0, bs.savings_balance(db) - earlier)
    drawn = min(actual, available)
    shortfall = max(0, actual - drawn)

    if shortfall > 0 and not data.get("confirm_shortfall"):
        return jsonify(
            error="shortfall_requires_confirmation",
            shortfall=shortfall,
            shortfall_aed=str(fils_to_aed(shortfall)),
            target_month=target_month,
        ), 409

    # 1. Insert the expense transaction.
    cur = db.execute(
        "INSERT INTO transactions (date, amount, type, source, category_id, description, linked_type, linked_id) "
        "VALUES (?, ?, 'expense', ?, ?, ?, 'wishlist', ?)",
        (purchase_date, actual, source, data.get("category_id") or None,
         f"Wishlist: {w['item_name']}", wid),
    )
    tx_id = cur.lastrowid

    # 2. Reconcile savings pot.
    if drawn > 0:
        bs.debit_savings(drawn, f"Wishlist purchase: {w['item_name']}", db=db)

    # 3. Apply shortfall to target month's unified budget (negative if it
    #    pushes it below zero — cascade will pick it up at month close).
    if shortfall > 0:
        existing = db.execute(
            "SELECT amount FROM monthly_budgets WHERE month = ?", (target_month,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE monthly_budgets SET amount = amount - ?, updated_at = datetime('now') "
                "WHERE month = ?",
                (shortfall, target_month),
            )
        else:
            # If no budget set yet for target month, create a row at -shortfall
            # so the deduction is recorded.
            db.execute(
                "INSERT INTO monthly_budgets (month, amount) VALUES (?, ?)",
                (target_month, -shortfall),
            )
        db.execute(
            "INSERT INTO audit_log (event_type, description, related_type, related_id) "
            "VALUES ('wishlist_budget_charge', ?, 'wishlist', ?)",
            (f"Wishlist '{w['item_name']}' charged AED {shortfall/100:.2f} to {target_month}", wid),
        )

    # 4. Update wishlist row.
    db.execute(
        "UPDATE wishlist SET status = 'purchased', transaction_id = ?, "
        "savings_drawn = ?, budget_charged = ? WHERE id = ?",
        (tx_id, drawn, shortfall, wid),
    )

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('wishlist_purchase', ?, 'wishlist', ?)",
        (f"Purchased '{w['item_name']}': savings {drawn/100:.2f}, budget {shortfall/100:.2f}", wid),
    )
    db.commit()
    return jsonify(
        ok=True,
        transaction_id=tx_id,
        savings_drawn=drawn,
        savings_drawn_aed=str(fils_to_aed(drawn)),
        budget_charged=shortfall,
        budget_charged_aed=str(fils_to_aed(shortfall)),
    )
