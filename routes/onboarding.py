"""First-launch onboarding: opening balances + initial monthly budget."""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import admin_required, onboarding_complete
from services.money import aed_to_fils
from services.timeutil import current_month_key, today

bp = Blueprint("onboarding", __name__, url_prefix="/api/onboarding")


@bp.get("/status")
@admin_required
def status():
    return jsonify(onboarded=onboarding_complete())


@bp.post("/complete")
@admin_required
def complete():
    if onboarding_complete():
        return jsonify(error="onboarding already complete"), 400

    data = request.get_json(silent=True) or {}
    try:
        bank_open = aed_to_fils(data.get("opening_bank", 0))
        petty_open = aed_to_fils(data.get("opening_petty", 0))
        monthly_budget = aed_to_fils(data.get("monthly_budget", 2500))
    except (ValueError, ArithmeticError) as e:
        return jsonify(error=f"invalid amount: {e}"), 400

    if bank_open < 0 or petty_open < 0 or monthly_budget <= 0:
        return jsonify(error="amounts must be non-negative; budget must be positive"), 400

    db = get_db()
    month = current_month_key()
    iso_today = today().isoformat()

    # Seed opening balances as off-budget income transactions so balance math
    # works through the same code path as everything else.
    if bank_open > 0:
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description) "
            "VALUES (?, ?, 'income_bank', 'bank', 'Opening bank balance')",
            (iso_today, bank_open),
        )
    if petty_open > 0:
        db.execute(
            "INSERT INTO transactions (date, amount, type, source, description) "
            "VALUES (?, ?, 'income_petty_external', 'petty', 'Opening petty cash balance')",
            (iso_today, petty_open),
        )

    db.execute(
        "INSERT OR REPLACE INTO monthly_budgets (month, amount) VALUES (?, ?)",
        (month, monthly_budget),
    )
    db.execute(
        "UPDATE app_settings SET value = '1' WHERE key = 'onboarded'"
    )
    db.execute(
        "INSERT INTO audit_log (event_type, description) VALUES "
        "('onboarding', 'First-launch onboarding completed')"
    )
    db.commit()
    return jsonify(ok=True, month=month)
