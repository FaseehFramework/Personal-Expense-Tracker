"""Authentication, session management, role gating, and first-launch state."""
from functools import wraps

import bcrypt
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from database import get_db

bp = Blueprint("auth", __name__)


# ----- session helpers -----

def _set_session(user_row) -> None:
    session.clear()
    session["user_id"] = user_row["id"]
    session["username"] = user_row["username"]
    session["role"] = user_row["role"]
    session.permanent = True


def current_user():
    if "user_id" not in session:
        return None
    return {
        "id": session["user_id"],
        "username": session["username"],
        "role": session["role"],
    }


def is_admin() -> bool:
    return session.get("role") == "admin"


def setup_complete() -> bool:
    """True once both admin + viewer accounts exist."""
    db = get_db()
    count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    return count >= 2


def onboarding_complete() -> bool:
    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE key = 'onboarded'"
    ).fetchone()
    return row is not None and row["value"] == "1"


# ----- decorators -----

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="auth required"), 401
            return redirect(url_for("auth.login_page"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="auth required"), 401
            return redirect(url_for("auth.login_page"))
        if session.get("role") != "admin":
            return jsonify(error="admin only"), 403
        return view(*args, **kwargs)
    return wrapper


# ----- routes -----

@bp.get("/login")
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template(
        "login.html",
        first_run=not setup_complete(),
    )


@bp.post("/api/auth/first-run")
def first_run_setup():
    """Create the admin + viewer accounts on first launch. Idempotent guard."""
    db = get_db()
    if setup_complete():
        return jsonify(error="setup already complete"), 400

    data = request.get_json(silent=True) or {}
    admin_username = (data.get("admin_username") or "").strip()
    admin_password = data.get("admin_password") or ""
    viewer_username = (data.get("viewer_username") or "").strip()
    viewer_password = data.get("viewer_password") or ""

    if not all([admin_username, admin_password, viewer_username, viewer_password]):
        return jsonify(error="all four fields required"), 400
    if len(admin_password) < 6 or len(viewer_password) < 6:
        return jsonify(error="passwords must be at least 6 characters"), 400
    if admin_username == viewer_username:
        return jsonify(error="admin and viewer usernames must differ"), 400

    admin_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
    viewer_hash = bcrypt.hashpw(viewer_password.encode(), bcrypt.gensalt()).decode()

    db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
        (admin_username, admin_hash),
    )
    db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'viewer')",
        (viewer_username, viewer_hash),
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description) VALUES "
        "('setup', 'Initial admin and viewer accounts created')"
    )
    db.commit()
    return jsonify(ok=True)


@bp.post("/api/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify(error="username and password required"), 400

    db = get_db()
    row = db.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return jsonify(error="invalid credentials"), 401

    _set_session(row)
    return jsonify(
        user={"username": row["username"], "role": row["role"]},
        onboarded=onboarding_complete(),
    )


@bp.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/api/auth/me")
def api_me():
    user = current_user()
    if user is None:
        return jsonify(authenticated=False), 200
    return jsonify(
        authenticated=True,
        user=user,
        onboarded=onboarding_complete(),
        setup_complete=setup_complete(),
    )
