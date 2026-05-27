"""Quick-add templates (§5.5). Saved combinations of amount+source+category+description."""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import login_required, admin_required
from services.money import aed_to_fils, fils_to_aed

bp = Blueprint("templates", __name__, url_prefix="/api/templates")


@bp.get("")
@login_required
def list_templates():
    db = get_db()
    rows = db.execute(
        "SELECT t.id, t.description, t.amount, t.source, t.category_id, c.name AS category_name "
        "FROM quick_add_templates t LEFT JOIN categories c ON c.id = t.category_id "
        "ORDER BY t.created_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["amount_aed"] = str(fils_to_aed(d["amount"]))
        out.append(d)
    return jsonify(templates=out)


@bp.post("")
@admin_required
def create_template():
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    source = (data.get("source") or "").strip().lower()
    if not description or source not in ("bank", "petty"):
        return jsonify(error="description and valid source required"), 400
    try:
        amount = aed_to_fils(data.get("amount"))
    except (ValueError, ArithmeticError):
        return jsonify(error="amount invalid"), 400
    category_id = data.get("category_id") or None

    db = get_db()
    # Avoid exact duplicates.
    existing = db.execute(
        "SELECT id FROM quick_add_templates WHERE description = ? AND amount = ? "
        "AND source = ? AND IFNULL(category_id,0) = IFNULL(?,0)",
        (description, amount, source, category_id),
    ).fetchone()
    if existing:
        return jsonify(template_id=existing["id"], duplicate=True)

    cur = db.execute(
        "INSERT INTO quick_add_templates (description, amount, source, category_id) VALUES (?, ?, ?, ?)",
        (description, amount, source, category_id),
    )
    db.commit()
    return jsonify(template_id=cur.lastrowid), 201


@bp.delete("/<int:tpl_id>")
@admin_required
def delete_template(tpl_id: int):
    db = get_db()
    db.execute("DELETE FROM quick_add_templates WHERE id = ?", (tpl_id,))
    db.commit()
    return jsonify(ok=True)
