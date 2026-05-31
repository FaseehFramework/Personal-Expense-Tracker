"""
Reports endpoints (§11):
  - Per-month summary
  - Budget history table
  - Up to 3-month comparison (data for bar chart)
  - Period analysis (data for pie chart) by category, source filterable
  - CSV exports (raw + summary)
  - SQLite DB backup download
"""
import csv
import io
import os
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, send_file

from config import Config
from database import get_db
from routes.auth import admin_required, login_required
from services import budget_service as bs
from services.money import fils_to_aed
from services.tx_effects import BUDGET_CONSUMING_TYPES, BUDGET_INCOME_TYPES

bp = Blueprint("reports", __name__, url_prefix="/api/reports")


# ---------- per-month summary (§11.1) ----------

@bp.get("/month-summary/<month>")
@login_required
def month_summary(month: str):
    db = get_db()
    if not _valid_month(month):
        return jsonify(error="month must be YYYY-MM"), 400

    budget = bs.get_monthly_budget(month, db)
    spent = bs.month_spend(month, db)
    income = bs.month_budget_income(month, db)
    cascade_in = bs.cascade_into(month, db)

    hist = db.execute("SELECT * FROM budget_history WHERE month = ?", (month,)).fetchone()
    closed = hist is not None
    rollover = int(hist["rollover_amount"]) if hist else 0
    savings = int(hist["savings_amount"]) if hist else 0
    cascade_out = int(hist["negative_cascade"]) if hist else 0

    # Outstanding receivables as of end of `month`.
    rec_out = db.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM receivables "
        "WHERE status='outstanding' AND month <= ?",
        (month,),
    ).fetchone()["s"]

    # Off-budget expenses for this month (informational — not part of budget figures).
    off_budget = db.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM transactions "
        "WHERE is_deleted=0 AND strftime('%Y-%m',date)=? AND type='expense_offbudget'",
        (month,),
    ).fetchone()["s"]
    off_budget_spend = int(off_budget or 0)

    # Loan balances at this point (we use current state — loans have no per-month
    # snapshot in v1).
    loans = db.execute("SELECT direction, total_amount, id FROM loans").fetchall()
    owed_to_me = 0
    i_owe = 0
    for l in loans:
        paid = db.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM loan_payments WHERE loan_id = ?",
            (l["id"],),
        ).fetchone()["s"]
        rem = int(l["total_amount"]) - int(paid)
        if rem <= 0:
            continue
        if l["direction"] == "owed":
            owed_to_me += rem
        else:
            i_owe += rem

    return jsonify({
        "month": month,
        "closed": closed,
        "budget": budget,
        "budget_aed": str(fils_to_aed(budget)),
        "spent": spent,
        "spent_aed": str(fils_to_aed(spent)),
        "budget_income": income,
        "budget_income_aed": str(fils_to_aed(income)),
        "variance": budget + income - spent,
        "variance_aed": str(fils_to_aed(budget + income - spent)),
        "rollover": rollover,
        "rollover_aed": str(fils_to_aed(rollover)),
        "savings": savings,
        "savings_aed": str(fils_to_aed(savings)),
        "cascade_in": cascade_in,
        "cascade_in_aed": str(fils_to_aed(cascade_in)),
        "cascade_out": cascade_out,
        "cascade_out_aed": str(fils_to_aed(cascade_out)),
        "outstanding_receivables": int(rec_out),
        "outstanding_receivables_aed": str(fils_to_aed(rec_out)),
        "off_budget_spend": off_budget_spend,
        "off_budget_spend_aed": str(fils_to_aed(off_budget_spend)),
        "loans_owed_to_me": owed_to_me,
        "loans_owed_to_me_aed": str(fils_to_aed(owed_to_me)),
        "loans_i_owe": i_owe,
        "loans_i_owe_aed": str(fils_to_aed(i_owe)),
    })


# ---------- budget history (§11.4) ----------

@bp.get("/budget-history")
@login_required
def budget_history():
    db = get_db()
    rows = db.execute(
        "SELECT month, budget_set, actual_spend, rollover_amount, savings_amount, "
        "negative_cascade, closed_at FROM budget_history ORDER BY month DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["variance"] = int(r["budget_set"]) - int(r["actual_spend"])
        d["variance_aed"] = str(fils_to_aed(d["variance"]))
        d["budget_set_aed"] = str(fils_to_aed(r["budget_set"]))
        d["actual_spend_aed"] = str(fils_to_aed(r["actual_spend"]))
        d["rollover_amount_aed"] = str(fils_to_aed(r["rollover_amount"]))
        d["savings_amount_aed"] = str(fils_to_aed(r["savings_amount"]))
        d["negative_cascade_aed"] = str(fils_to_aed(r["negative_cascade"]))
        out.append(d)
    return jsonify(rows=out)


# ---------- month comparison (§11.2) ----------

@bp.get("/compare")
@login_required
def compare_months():
    months_param = request.args.get("months", "")
    months = [m.strip() for m in months_param.split(",") if m.strip()]
    months = [m for m in months if _valid_month(m)][:3]
    if not months:
        return jsonify(error="provide up to 3 months as ?months=YYYY-MM,YYYY-MM"), 400

    db = get_db()
    data = []
    for m in months:
        budget = bs.get_monthly_budget(m, db)
        spent = bs.month_spend(m, db)
        hist = db.execute("SELECT savings_amount FROM budget_history WHERE month = ?", (m,)).fetchone()
        savings = int(hist["savings_amount"]) if hist else 0
        off_b_row = db.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM transactions "
            "WHERE is_deleted=0 AND strftime('%Y-%m',date)=? AND type='expense_offbudget'",
            (m,),
        ).fetchone()
        off_budget = int(off_b_row["s"] or 0)
        data.append({
            "month": m,
            "budget": budget, "budget_aed": str(fils_to_aed(budget)),
            "spent": spent, "spent_aed": str(fils_to_aed(spent)),
            "savings": savings, "savings_aed": str(fils_to_aed(savings)),
            "off_budget_spend": off_budget, "off_budget_spend_aed": str(fils_to_aed(off_budget)),
        })
    return jsonify(months=data)


# ---------- period analysis (§11.3) ----------

@bp.get("/period")
@login_required
def period_analysis():
    db = get_db()
    args = request.args
    where = ["t.is_deleted = 0"]
    params: list = []

    df = args.get("from")
    dt = args.get("to")
    source = args.get("source")
    cat_id = args.get("category_id")
    if df: where.append("t.date >= ?"); params.append(df)
    if dt: where.append("t.date <= ?"); params.append(dt)
    if source in ("bank", "petty"): where.append("t.source = ?"); params.append(source)
    if cat_id: where.append("t.category_id = ?"); params.append(cat_id)

    # Include both budget-consuming and off-budget expense types in the pie.
    pie_types = (*BUDGET_CONSUMING_TYPES, "expense_offbudget")
    placeholders = ",".join("?" * len(pie_types))
    where.append(f"t.type IN ({placeholders})")
    params.extend(pie_types)

    where_sql = " AND ".join(where)
    rows = db.execute(
        f"SELECT t.category_id, c.name AS category_name, t.type, "
        f"  COALESCE(SUM(t.amount), 0) AS total, COUNT(*) AS n "
        f"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
        f"WHERE {where_sql} GROUP BY t.category_id, c.name, t.type ORDER BY total DESC",
        params,
    ).fetchall()

    slices = []
    grand_total = 0
    off_budget_total = 0
    for r in rows:
        amt = int(r["total"])
        grand_total += amt
        is_offbudget = r["type"] == "expense_offbudget"
        if is_offbudget:
            off_budget_total += amt
        slices.append({
            "category_id": r["category_id"],
            "category_name": r["category_name"] or "Uncategorised",
            "amount": amt,
            "amount_aed": str(fils_to_aed(amt)),
            "count": int(r["n"]),
            "is_offbudget": is_offbudget,
        })
    return jsonify(
        slices=slices,
        total=grand_total,
        total_aed=str(fils_to_aed(grand_total)),
        off_budget_total=off_budget_total,
        off_budget_total_aed=str(fils_to_aed(off_budget_total)),
    )


# ---------- CSV exports (§11.5) ----------

@bp.get("/export/raw")
@login_required
def export_raw():
    db = get_db()
    args = request.args
    where = ["t.is_deleted = 0"]
    params: list = []
    if df := args.get("from"): where.append("t.date >= ?"); params.append(df)
    if dt := args.get("to"):   where.append("t.date <= ?"); params.append(dt)
    if cat := args.get("category_id"): where.append("t.category_id = ?"); params.append(cat)
    where_sql = " AND ".join(where)

    rows = db.execute(
        f"SELECT t.id, t.date, t.amount, t.type, t.source, c.name AS category, "
        f"  t.description, t.memo, t.linked_type, t.linked_id "
        f"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
        f"WHERE {where_sql} ORDER BY t.date, t.id",
        params,
    ).fetchall()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "date", "amount_AED", "type", "source", "category",
                "description", "memo", "split_details", "linked_type", "linked_id"])
    for r in rows:
        splits = db.execute(
            "SELECT s.amount, c.name FROM transaction_splits s "
            "LEFT JOIN categories c ON c.id = s.category_id "
            "WHERE s.transaction_id = ?", (r["id"],)
        ).fetchall()
        split_str = "; ".join(f"{s['name'] or 'Uncategorised'}: AED {int(s['amount'])/100:.2f}"
                              for s in splits)
        w.writerow([
            r["id"], r["date"], f"{int(r['amount'])/100:.2f}",
            r["type"], r["source"], r["category"] or "Uncategorised",
            r["description"], r["memo"] or "", split_str,
            r["linked_type"] or "", r["linked_id"] or "",
        ])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions_raw.csv"},
    )


@bp.get("/export/summary")
@login_required
def export_summary():
    db = get_db()
    args = request.args
    where = ["t.is_deleted = 0"]
    params: list = []
    if df := args.get("from"): where.append("t.date >= ?"); params.append(df)
    if dt := args.get("to"):   where.append("t.date <= ?"); params.append(dt)
    if cat := args.get("category_id"): where.append("t.category_id = ?"); params.append(cat)
    where_sql = " AND ".join(where)

    consuming_in = ",".join("?" * len(BUDGET_CONSUMING_TYPES))
    income_in = ",".join("?" * len(BUDGET_INCOME_TYPES))

    rows = db.execute(
        f"SELECT strftime('%Y-%m', t.date) AS month, t.source, IFNULL(c.name,'Uncategorised') AS category, "
        f"  SUM(CASE WHEN t.type IN ({consuming_in}) THEN t.amount ELSE 0 END) AS spend, "
        f"  SUM(CASE WHEN t.type IN ({income_in}) THEN t.amount ELSE 0 END) AS budget_income, "
        f"  SUM(CASE WHEN t.type = 'expense_offbudget' THEN t.amount ELSE 0 END) AS offbudget_spend, "
        f"  COUNT(*) AS n "
        f"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
        f"WHERE {where_sql} "
        f"GROUP BY month, source, category ORDER BY month, source, category",
        (*BUDGET_CONSUMING_TYPES, *BUDGET_INCOME_TYPES, *params),
    ).fetchall()

    # Pull per-month budget set + actual to add a "budget vs actual" pair row.
    budgets = {
        r["month"]: int(r["amount"]) for r in
        db.execute("SELECT month, amount FROM monthly_budgets").fetchall()
    }
    history = {
        r["month"]: r for r in
        db.execute("SELECT month, budget_set, actual_spend FROM budget_history").fetchall()
    }

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["month", "source", "category", "spend_AED", "budget_income_AED", "offbudget_spend_AED", "transactions"])
    for r in rows:
        w.writerow([
            r["month"], r["source"], r["category"],
            f"{int(r['spend'])/100:.2f}",
            f"{int(r['budget_income'])/100:.2f}",
            f"{int(r['offbudget_spend'])/100:.2f}",
            r["n"],
        ])
    # Trailing budget-vs-actual block.
    w.writerow([])
    w.writerow(["month", "budget_set_AED", "actual_spend_AED", "current_budget_AED"])
    seen_months = sorted(set([*history.keys(), *budgets.keys()]))
    for m in seen_months:
        h = history.get(m)
        w.writerow([
            m,
            f"{int(h['budget_set'])/100:.2f}" if h else "",
            f"{int(h['actual_spend'])/100:.2f}" if h else "",
            f"{budgets.get(m, 0)/100:.2f}",
        ])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=summary.csv"},
    )


# ---------- SQLite backup download (§11.6) ----------

@bp.get("/backup-db")
@admin_required
def backup_db():
    path = Config.DATABASE_PATH
    if not os.path.exists(path):
        return jsonify(error="database file not found"), 404
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return send_file(
        path,
        as_attachment=True,
        download_name=f"expense_tracker_{ts}.sqlite",
        mimetype="application/x-sqlite3",
    )


# ---------- helpers ----------

def _valid_month(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m")
        return True
    except (ValueError, TypeError):
        return False
