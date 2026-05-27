"""Spending streaks API — §13. Dashboard shows the top streak as a dismissable card."""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, login_required
from services import streak_service
from services.money import fils_to_aed

bp = Blueprint("streaks", __name__, url_prefix="/api/streaks")


@bp.get("")
@login_required
def list_streaks():
    db = get_db()
    streaks = streak_service.enrich_with_category_names(streak_service.detect_streaks(db), db)
    for s in streaks:
        s["median_amount_aed"] = str(fils_to_aed(s["median_amount"]))
    return jsonify(streaks=streaks)


@bp.post("/dismiss")
@admin_required
def dismiss_streak():
    data = request.get_json(silent=True) or {}
    sig = (data.get("signature") or "").strip()
    if not sig:
        return jsonify(error="signature required"), 400
    db = get_db()
    ok = streak_service.dismiss(sig, db)
    if not ok:
        return jsonify(error="no active streak with that signature"), 404
    db.commit()
    return jsonify(ok=True)
