"""
Settings endpoints (§17):
  - Change password (admin can change either account's password)
  - Manage categories (add / rename / delete)
  - Manage quick-add templates: already handled in routes/templates.py
  - App version
"""
import sqlite3

import bcrypt
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required

bp = Blueprint("settings", __name__, url_prefix="/api/settings")


# ---------- meta ----------

@bp.get("/version")
@login_required
def get_version():
    db = get_db()
    row = db.execute("SELECT value FROM app_settings WHERE key = 'app_version'").fetchone()
    return jsonify(version=row["value"] if row else "unknown")


# ---------- accounts ----------

@bp.get("/accounts")
@admin_required
def list_accounts():
    db = get_db()
    rows = db.execute("SELECT id, username, role FROM users ORDER BY role").fetchall()
    return jsonify(accounts=[dict(r) for r in rows])


@bp.post("/change-password")
@admin_required
def change_password():
    """§17: admin can change either Admin's or Viewer's password."""
    data = request.get_json(silent=True) or {}
    target_username = (data.get("target_username") or "").strip()
    new_pw = data.get("new_password") or ""
    if not target_username or len(new_pw) < 6:
        return jsonify(error="target_username and new_password (>=6 chars) required"), 400

    db = get_db()
    user = db.execute("SELECT id, username FROM users WHERE username = ?", (target_username,)).fetchone()
    if not user:
        return jsonify(error="user not found"), 404

    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('password_change', ?, 'user', ?)",
        (f"Password changed for '{target_username}'", user["id"]),
    )
    db.commit()
    return jsonify(ok=True)


# ---------- categories ----------

@bp.get("/categories")
@login_required
def list_categories():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, is_default, is_deleted FROM categories ORDER BY is_deleted, name"
    ).fetchall()
    return jsonify(categories=[dict(r) for r in rows])


@bp.post("/categories")
@admin_required
def create_category():
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify(error="name required"), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        return jsonify(error="a category with that name already exists"), 409
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_create', ?, 'category', ?)",
        (f"Created category '{name}'", cur.lastrowid),
    )
    db.commit()
    return jsonify(id=cur.lastrowid), 201


@bp.put("/categories/<int:cid>")
@admin_required
def rename_category(cid: int):
    new_name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not new_name:
        return jsonify(error="name required"), 400
    db = get_db()
    row = db.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()
    if not row:
        return jsonify(error="not found"), 404
    try:
        db.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, cid))
    except sqlite3.IntegrityError:
        return jsonify(error="a category with that name already exists"), 409
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_rename', ?, 'category', ?)",
        (f"Renamed category '{row['name']}' → '{new_name}'", cid),
    )
    db.commit()
    return jsonify(ok=True)


@bp.delete("/categories/<int:cid>")
@admin_required
def delete_category(cid: int):
    """Soft-delete the category if it's referenced anywhere, otherwise hard-delete.
    Referenced transactions keep their category_id (they still show 'Uncategorised'
    in the UI because the join filters is_deleted; data is preserved)."""
    db = get_db()
    row = db.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()
    if not row:
        return jsonify(error="not found"), 404
    in_use = db.execute(
        "SELECT 1 FROM transactions WHERE category_id = ? LIMIT 1", (cid,)
    ).fetchone()
    if in_use:
        db.execute("UPDATE categories SET is_deleted = 1 WHERE id = ?", (cid,))
        action = "Soft-deleted"
    else:
        db.execute("DELETE FROM categories WHERE id = ?", (cid,))
        action = "Hard-deleted"
    db.execute(
        "INSERT INTO audit_log (event_type, description, related_type, related_id) "
        "VALUES ('category_delete', ?, 'category', ?)",
        (f"{action} category '{row['name']}'", cid),
    )
    db.commit()
    return jsonify(ok=True, action=action.lower())
