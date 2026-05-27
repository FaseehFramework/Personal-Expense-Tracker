"""
Transactions blueprint — CRUD, splits, attachments, duplicates, soft delete,
edit history, quick-add templates.

All money in fils. The frontend sends/receives AED decimals via api/money.py
conversion. We do the conversion here so route handlers always work in fils.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from config import Config
from database import get_db
from routes.auth import login_required, admin_required
from services.money import aed_to_fils, fils_to_aed
from services.tx_effects import TX_TYPES
from services.timeutil import today

bp = Blueprint("transactions", __name__, url_prefix="/api/transactions")

QUICK_ADD_THRESHOLD = 3       # §5.5
DUPLICATE_DAY_WINDOW = 2      # §5.6 (±2 days)
TRASH_RETENTION_DAYS = 5      # §5.7


# ----- helpers -----

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "amount" in d and d["amount"] is not None:
        d["amount_aed"] = str(fils_to_aed(d["amount"]))
    return d


def _serialize_tx(db: sqlite3.Connection, tx: sqlite3.Row, include_splits=True, include_edits=False) -> dict:
    out = _row_to_dict(tx)
    cat = None
    if tx["category_id"]:
        crow = db.execute("SELECT id, name FROM categories WHERE id = ?", (tx["category_id"],)).fetchone()
        if crow:
            cat = {"id": crow["id"], "name": crow["name"]}
    out["category"] = cat
    if include_splits:
        splits = db.execute(
            "SELECT s.id, s.category_id, s.amount, s.memo, c.name AS category_name "
            "FROM transaction_splits s LEFT JOIN categories c ON c.id = s.category_id "
            "WHERE s.transaction_id = ? ORDER BY s.id",
            (tx["id"],),
        ).fetchall()
        out["splits"] = [
            {**dict(s), "amount_aed": str(fils_to_aed(s["amount"]))} for s in splits
        ]
    if include_edits:
        edits = db.execute(
            "SELECT id, field_name, old_value, new_value, changed_at "
            "FROM transaction_edits WHERE transaction_id = ? ORDER BY changed_at DESC, id DESC",
            (tx["id"],),
        ).fetchall()
        out["edits"] = [dict(e) for e in edits]
    return out


def _validate_payload(data: dict) -> tuple[dict, list[str]]:
    """Coerce + validate. Returns (clean, errors)."""
    errors = []
    out = {}

    # Date
    date_s = (data.get("date") or "").strip() or today().isoformat()
    try:
        datetime.strptime(date_s, "%Y-%m-%d")
        out["date"] = date_s
    except ValueError:
        errors.append("invalid date (expected YYYY-MM-DD)")

    # Amount
    try:
        out["amount"] = aed_to_fils(data.get("amount"))
        if out["amount"] <= 0:
            errors.append("amount must be greater than zero")
    except (ValueError, ArithmeticError):
        errors.append("amount must be a number")

    # Type / source
    tx_type = (data.get("type") or "").strip()
    if tx_type not in TX_TYPES:
        errors.append(f"invalid type (allowed: {', '.join(TX_TYPES)})")
    out["type"] = tx_type

    source = (data.get("source") or "").strip().lower()
    if source not in ("bank", "petty"):
        errors.append("source must be 'bank' or 'petty'")
    out["source"] = source

    # Description required
    description = (data.get("description") or "").strip()
    if not description:
        errors.append("description required")
    out["description"] = description

    # Optional
    out["category_id"] = data.get("category_id") or None
    out["memo"] = (data.get("memo") or "").strip() or None
    out["linked_type"] = data.get("linked_type") or None
    out["linked_id"] = data.get("linked_id") or None

    # Splits (optional)
    splits = data.get("splits") or []
    if splits:
        parsed_splits = []
        total = 0
        for s in splits:
            try:
                amt = aed_to_fils(s.get("amount"))
            except (ValueError, ArithmeticError):
                errors.append("split amount invalid")
                continue
            if amt <= 0:
                errors.append("each split amount must be greater than zero")
                continue
            parsed_splits.append({
                "amount": amt,
                "category_id": s.get("category_id") or None,
                "memo": (s.get("memo") or "").strip() or None,
            })
            total += amt
        out["splits"] = parsed_splits
        if "amount" in out and out["amount"] is not None and total != out["amount"]:
            errors.append(
                f"split amounts ({total/100:.2f}) must equal total ({out['amount']/100:.2f})"
            )
    else:
        out["splits"] = []

    return out, errors


def _find_duplicate(db: sqlite3.Connection, amount: int, category_id, date_s: str) -> sqlite3.Row | None:
    """§5.6: same amount + same category + within ±2 days."""
    d = datetime.strptime(date_s, "%Y-%m-%d").date()
    lo = (d - timedelta(days=DUPLICATE_DAY_WINDOW)).isoformat()
    hi = (d + timedelta(days=DUPLICATE_DAY_WINDOW)).isoformat()
    if category_id:
        return db.execute(
            "SELECT id, date, amount, description FROM transactions "
            "WHERE is_deleted = 0 AND amount = ? AND category_id = ? AND date BETWEEN ? AND ? "
            "ORDER BY date DESC, id DESC LIMIT 1",
            (amount, category_id, lo, hi),
        ).fetchone()
    return db.execute(
        "SELECT id, date, amount, description FROM transactions "
        "WHERE is_deleted = 0 AND amount = ? AND category_id IS NULL AND date BETWEEN ? AND ? "
        "ORDER BY date DESC, id DESC LIMIT 1",
        (amount, lo, hi),
    ).fetchone()


def _maybe_offer_quick_add(db: sqlite3.Connection, clean: dict) -> bool:
    """§5.5: after 3+ identical (amount + category + source + description) tx exist,
    suggest saving as a template (if no template already exists for that combo)."""
    n = db.execute(
        "SELECT COUNT(*) AS c FROM transactions "
        "WHERE is_deleted = 0 AND amount = ? AND IFNULL(category_id,0) = IFNULL(?,0) "
        "AND source = ? AND description = ?",
        (clean["amount"], clean["category_id"], clean["source"], clean["description"]),
    ).fetchone()["c"]
    if n < QUICK_ADD_THRESHOLD:
        return False
    exists = db.execute(
        "SELECT 1 FROM quick_add_templates WHERE amount = ? AND IFNULL(category_id,0) = IFNULL(?,0) "
        "AND source = ? AND description = ?",
        (clean["amount"], clean["category_id"], clean["source"], clean["description"]),
    ).fetchone()
    return exists is None


def _purge_old_trash(db: sqlite3.Connection) -> int:
    """Hard-delete soft-deleted rows older than TRASH_RETENTION_DAYS."""
    cutoff = (datetime.utcnow() - timedelta(days=TRASH_RETENTION_DAYS)).isoformat()
    cur = db.execute(
        "DELETE FROM transactions WHERE is_deleted = 1 AND deleted_at IS NOT NULL AND deleted_at < ?",
        (cutoff,),
    )
    return cur.rowcount


# ----- LIST -----

@bp.get("")
@login_required
def list_transactions():
    """List transactions grouped by day (newest first). Supports filters:
        q, type, source, category_id, date_from, date_to, amount_min, amount_max,
        include_deleted=1 to view trash, limit, offset
    """
    db = get_db()
    _purge_old_trash(db)

    args = request.args
    where = []
    params = []
    if args.get("include_deleted") == "1":
        where.append("t.is_deleted = 1")
    else:
        where.append("t.is_deleted = 0")
    if q := args.get("q"):
        where.append("(t.description LIKE ? OR IFNULL(t.memo,'') LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if tx_type := args.get("type"):
        where.append("t.type = ?")
        params.append(tx_type)
    if source := args.get("source"):
        where.append("t.source = ?")
        params.append(source)
    if cat := args.get("category_id"):
        where.append("t.category_id = ?")
        params.append(cat)
    if df := args.get("date_from"):
        where.append("t.date >= ?")
        params.append(df)
    if dt := args.get("date_to"):
        where.append("t.date <= ?")
        params.append(dt)
    if amin := args.get("amount_min"):
        try:
            where.append("t.amount >= ?"); params.append(aed_to_fils(amin))
        except Exception: pass
    if amax := args.get("amount_max"):
        try:
            where.append("t.amount <= ?"); params.append(aed_to_fils(amax))
        except Exception: pass

    limit = min(int(args.get("limit", 200)), 1000)
    offset = int(args.get("offset", 0))

    where_sql = " AND ".join(where) if where else "1=1"
    rows = db.execute(
        f"SELECT t.*, c.name AS category_name FROM transactions t "
        f"LEFT JOIN categories c ON c.id = t.category_id "
        f"WHERE {where_sql} "
        f"ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    # Day grouping for the UI (newest first).
    days = {}
    for r in rows:
        d = r["date"]
        days.setdefault(d, []).append(_serialize_tx(db, r, include_splits=True))
    grouped = []
    for d in sorted(days.keys(), reverse=True):
        items = days[d]
        # Day subtotal: sum on-budget spend minus on-budget income.
        from services.tx_effects import BUDGET_CONSUMING_TYPES, BUDGET_INCOME_TYPES
        sub = 0
        for it in items:
            if it["type"] in BUDGET_CONSUMING_TYPES: sub += it["amount"]
            elif it["type"] in BUDGET_INCOME_TYPES: sub -= it["amount"]
        grouped.append({
            "date": d,
            "subtotal": sub,
            "subtotal_aed": str(fils_to_aed(sub)),
            "items": items,
        })

    # Filter summary bar (§5.4).
    summary = None
    if any(k in args for k in ("q","type","source","category_id","date_from","date_to","amount_min","amount_max")):
        total = sum(it["amount"] for grp in grouped for it in grp["items"])
        count = sum(len(grp["items"]) for grp in grouped)
        summary = {"count": count, "total": total, "total_aed": str(fils_to_aed(total))}

    return jsonify(days=grouped, summary=summary)


# ----- CREATE -----

@bp.post("")
@admin_required
def create_transaction():
    data = request.get_json(silent=True) or {}
    clean, errors = _validate_payload(data)
    if errors:
        return jsonify(error="; ".join(errors)), 400

    db = get_db()

    # §5.6 duplicate detection — unless caller explicitly confirms.
    if not data.get("confirm_duplicate"):
        dupe = _find_duplicate(db, clean["amount"], clean["category_id"], clean["date"])
        if dupe is not None:
            return jsonify(
                duplicate=True,
                existing={
                    "id": dupe["id"], "date": dupe["date"],
                    "amount_aed": str(fils_to_aed(dupe["amount"])),
                    "description": dupe["description"],
                },
            ), 409

    cur = db.execute(
        "INSERT INTO transactions "
        "(date, amount, type, source, category_id, description, memo, linked_type, linked_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (clean["date"], clean["amount"], clean["type"], clean["source"],
         clean["category_id"], clean["description"], clean["memo"],
         clean["linked_type"], clean["linked_id"]),
    )
    tx_id = cur.lastrowid

    for s in clean["splits"]:
        db.execute(
            "INSERT INTO transaction_splits (transaction_id, category_id, amount, memo) "
            "VALUES (?, ?, ?, ?)",
            (tx_id, s["category_id"], s["amount"], s["memo"]),
        )

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_create', ?, 'transaction', ?)",
        (f"{clean['type']} {clean['amount']/100:.2f} — {clean['description']}", tx_id),
    )

    offer_template = _maybe_offer_quick_add(db, clean)

    db.commit()
    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return jsonify(
        transaction=_serialize_tx(db, tx),
        offer_template=offer_template,
    ), 201


# ----- GET ONE -----

@bp.get("/<int:tx_id>")
@login_required
def get_transaction(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        return jsonify(error="not found"), 404
    out = _serialize_tx(db, tx, include_splits=True, include_edits=True)
    # Linked records summary (used by delete-warning UI).
    out["linked_records"] = _linked_records_summary(db, tx)
    return jsonify(transaction=out)


def _linked_records_summary(db, tx) -> list:
    """For §5.7: enumerate records that reference this transaction."""
    links = []
    # loan payments referencing this tx
    lp = db.execute("SELECT id, loan_id FROM loan_payments WHERE transaction_id = ?", (tx["id"],)).fetchall()
    for r in lp:
        links.append({"kind": "loan_payment", "id": r["id"], "loan_id": r["loan_id"]})
    # recurring overrides
    ro = db.execute("SELECT id, recurring_id, month FROM recurring_overrides WHERE transaction_id = ?", (tx["id"],)).fetchall()
    for r in ro:
        links.append({"kind": "recurring_confirmation", "id": r["id"], "recurring_id": r["recurring_id"], "month": r["month"]})
    # wishlist purchase
    wl = db.execute("SELECT id, item_name FROM wishlist WHERE transaction_id = ?", (tx["id"],)).fetchall()
    for r in wl:
        links.append({"kind": "wishlist_purchase", "id": r["id"], "item_name": r["item_name"]})
    return links


# ----- UPDATE -----

@bp.put("/<int:tx_id>")
@admin_required
def update_transaction(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        return jsonify(error="not found"), 404
    if tx["is_deleted"]:
        return jsonify(error="restore before editing"), 400

    data = request.get_json(silent=True) or {}
    clean, errors = _validate_payload(data)
    if errors:
        return jsonify(error="; ".join(errors)), 400

    # §5.8 edit history — log each changed field.
    tracked_fields = ("date", "amount", "type", "source", "category_id", "description", "memo")
    for f in tracked_fields:
        old = tx[f]
        new = clean[f] if f in clean else old
        if str(old or "") != str(new or ""):
            db.execute(
                "INSERT INTO transaction_edits (transaction_id, field_name, old_value, new_value) "
                "VALUES (?, ?, ?, ?)",
                (tx_id, f, str(old) if old is not None else None, str(new) if new is not None else None),
            )

    db.execute(
        "UPDATE transactions SET date=?, amount=?, type=?, source=?, category_id=?, "
        "description=?, memo=?, updated_at=datetime('now') WHERE id = ?",
        (clean["date"], clean["amount"], clean["type"], clean["source"],
         clean["category_id"], clean["description"], clean["memo"], tx_id),
    )

    # Replace splits.
    db.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (tx_id,))
    for s in clean["splits"]:
        db.execute(
            "INSERT INTO transaction_splits (transaction_id, category_id, amount, memo) "
            "VALUES (?, ?, ?, ?)",
            (tx_id, s["category_id"], s["amount"], s["memo"]),
        )

    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_edit', ?, 'transaction', ?)",
        (f"Edited transaction #{tx_id}", tx_id),
    )
    db.commit()
    fresh = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return jsonify(transaction=_serialize_tx(db, fresh, include_edits=True))


@bp.post("/<int:tx_id>/revert/<int:edit_id>")
@admin_required
def revert_to_edit(tx_id: int, edit_id: int):
    """Revert a single field to its pre-edit value (§5.8 'revert to any previous version').
    Implemented as a per-field revert — the user picks which historical edit to undo."""
    db = get_db()
    edit = db.execute(
        "SELECT * FROM transaction_edits WHERE id = ? AND transaction_id = ?",
        (edit_id, tx_id),
    ).fetchone()
    if not edit:
        return jsonify(error="edit not found"), 404

    field = edit["field_name"]
    if field not in ("date", "amount", "type", "source", "category_id", "description", "memo"):
        return jsonify(error="cannot revert this field"), 400

    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    old_value = tx[field]
    new_value = edit["old_value"]

    # Coerce types
    if field in ("amount", "category_id") and new_value is not None and new_value != "":
        try: new_value = int(new_value)
        except ValueError: return jsonify(error="invalid revert value"), 400
    if new_value == "": new_value = None

    db.execute(
        f"UPDATE transactions SET {field} = ?, updated_at = datetime('now') WHERE id = ?",
        (new_value, tx_id),
    )
    db.execute(
        "INSERT INTO transaction_edits (transaction_id, field_name, old_value, new_value) "
        "VALUES (?, ?, ?, ?)",
        (tx_id, field, str(old_value) if old_value is not None else None,
         str(new_value) if new_value is not None else None),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_revert', ?, 'transaction', ?)",
        (f"Reverted {field} on tx #{tx_id}", tx_id),
    )
    db.commit()
    return jsonify(ok=True)


# ----- DELETE (soft) -----

@bp.delete("/<int:tx_id>")
@admin_required
def soft_delete(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        return jsonify(error="not found"), 404
    if tx["is_deleted"]:
        return jsonify(error="already deleted"), 400

    # §5.7 — warn about linked records unless caller confirms.
    links = _linked_records_summary(db, tx)
    if links and not (request.get_json(silent=True) or {}).get("confirm_linked"):
        return jsonify(linked=True, linked_records=links), 409

    db.execute(
        "UPDATE transactions SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ?",
        (tx_id,),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_delete', ?, 'transaction', ?)",
        (f"Soft-deleted tx #{tx_id}", tx_id),
    )
    db.commit()
    return jsonify(ok=True)


@bp.post("/<int:tx_id>/restore")
@admin_required
def restore(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or not tx["is_deleted"]:
        return jsonify(error="not in trash"), 404
    db.execute("UPDATE transactions SET is_deleted = 0, deleted_at = NULL WHERE id = ?", (tx_id,))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_restore', ?, 'transaction', ?)",
        (f"Restored tx #{tx_id}", tx_id),
    )
    db.commit()
    return jsonify(ok=True)


# ----- ATTACHMENTS -----

@bp.post("/<int:tx_id>/attachment")
@admin_required
def upload_attachment(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT id, attachment_path FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        return jsonify(error="not found"), 404
    if "file" not in request.files:
        return jsonify(error="no file uploaded"), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify(error="empty filename"), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in Config.ALLOWED_IMAGE_EXTENSIONS:
        return jsonify(error=f"unsupported file type (allowed: {', '.join(Config.ALLOWED_IMAGE_EXTENSIONS)})"), 400

    new_name = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = Path(Config.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / new_name
    f.save(dest)

    # Remove old attachment, if any.
    if tx["attachment_path"]:
        old = upload_dir / tx["attachment_path"]
        if old.exists():
            try: old.unlink()
            except OSError: pass

    db.execute("UPDATE transactions SET attachment_path = ?, updated_at = datetime('now') WHERE id = ?",
               (new_name, tx_id))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('transaction_attachment', ?, 'transaction', ?)",
        (f"Attached image to tx #{tx_id}", tx_id),
    )
    db.commit()
    return jsonify(ok=True, attachment_url=f"/api/transactions/{tx_id}/attachment")


@bp.get("/<int:tx_id>/attachment")
@login_required
def serve_attachment(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT attachment_path FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or not tx["attachment_path"]:
        return jsonify(error="no attachment"), 404
    return send_from_directory(Config.UPLOAD_DIR, tx["attachment_path"])


@bp.delete("/<int:tx_id>/attachment")
@admin_required
def delete_attachment(tx_id: int):
    db = get_db()
    tx = db.execute("SELECT attachment_path FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx or not tx["attachment_path"]:
        return jsonify(error="no attachment"), 404
    p = Path(Config.UPLOAD_DIR) / tx["attachment_path"]
    if p.exists():
        try: p.unlink()
        except OSError: pass
    db.execute("UPDATE transactions SET attachment_path = NULL WHERE id = ?", (tx_id,))
    db.commit()
    return jsonify(ok=True)


# ----- CATEGORIES (read-only here; mgmt lives under /api/settings) -----

@bp.get("/categories")
@login_required
def list_categories():
    db = get_db()
    rows = db.execute("SELECT id, name, is_default FROM categories WHERE is_deleted = 0 ORDER BY name").fetchall()
    return jsonify(categories=[dict(r) for r in rows])
