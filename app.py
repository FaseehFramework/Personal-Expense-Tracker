"""Flask entry point for the Personal Expense Tracker."""
import atexit
import os
from datetime import timedelta

from flask import Flask, redirect, render_template, session, url_for

from config import Config
from database import close_db, init_db
from routes.auth import bp as auth_bp, current_user, onboarding_complete, login_required
from routes.onboarding import bp as onboarding_bp
from routes.transactions import bp as transactions_bp
from routes.templates import bp as templates_bp
from routes.budget import bp as budget_bp
from routes.receivables import bp as receivables_bp
from routes.loans import bp as loans_bp
from routes.wishlist import bp as wishlist_bp
from routes.audit import bp as audit_bp
from routes.settings_routes import bp as settings_bp
from routes.reports import bp as reports_bp
from routes.streaks import bp as streaks_bp
from services import scheduler as scheduler_service


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_BYTES

    init_db()

    app.teardown_appcontext(close_db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(receivables_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(wishlist_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(streaks_bp)

    # Background scheduler — only in the real run, not under the autoreloader's parent.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        scheduler_service.start()
        atexit.register(scheduler_service.shutdown)

    @app.get("/")
    @login_required
    def index():
        if not onboarding_complete() and session.get("role") == "admin":
            return render_template("index.html", user=current_user(), needs_onboarding=True)
        if not onboarding_complete():
            return render_template("index.html", user=current_user(), needs_onboarding=False, awaiting_admin=True)
        return render_template("index.html", user=current_user(), needs_onboarding=False)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("auth.login_page"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
