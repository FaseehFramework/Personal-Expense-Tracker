"""Read-only audit log endpoint (§12). Paginated, newest first, filter by type."""
from flask import Blueprint, jsonify, request

from database import get_db
from routes.auth import login_required

bp = Blueprint("audit", __name__, url_prefix="/api/audit-log")


@bp.get("")
@login_required
def list_audit_log():
    db = get_db()
    args = request.args
    where = []
    params = []
    if t := args.get("type"):
        where.append("event_type = ?")
        params.append(t)
    if q := args.get("q"):
        where.append("description LIKE ?")
        params.append(f"%{q}%")
    if df := args.get("from"):
        where.append("created_at >= ?")
        params.append(df)
    if dt := args.get("to"):
        where.append("created_at <= ?")
        params.append(dt + " 23:59:59")

    try:
        limit = min(max(1, int(args.get("limit", 50))), 500)
        offset = max(0, int(args.get("offset", 0)))
    except ValueError:
        limit, offset = 50, 0

    where_sql = " AND ".join(where) if where else "1=1"
    rows = db.execute(
        f"SELECT id, event_type, description, related_type, related_id, created_at "
        f"FROM audit_log WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    total = db.execute(
        f"SELECT COUNT(*) AS c FROM audit_log WHERE {where_sql}", params
    ).fetchone()["c"]
    types = db.execute(
        "SELECT event_type, COUNT(*) AS c FROM audit_log GROUP BY event_type ORDER BY event_type"
    ).fetchall()
    return jsonify(
        events=[dict(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        types=[{"event_type": t["event_type"], "count": t["c"]} for t in types],
    )
