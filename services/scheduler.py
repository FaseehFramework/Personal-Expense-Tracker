"""
Background scheduler. Runs inside the Flask process (low-volume single-user app).
Only registers month-end rollover (§6.3) for v1; recurring-payment trigger
prompts are surfaced via the `pending_recurring` endpoint on app open.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from database import standalone_connection
from services import budget_service as bs
from services.timeutil import TZ, now, current_month_key

_scheduler: BackgroundScheduler | None = None


def _prev_month_key(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    m -= 1
    if m < 1:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def run_month_close_job() -> None:
    """Close the *previous* month — runs at 00:05 local on day 1 of each month."""
    month_to_close = _prev_month_key(current_month_key())
    with standalone_connection() as conn:
        bs.close_month(month_to_close, conn)


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone=TZ)
    # Fire 5 minutes past midnight on day 1 of every month, in Asia/Dubai.
    sched.add_job(
        run_month_close_job,
        CronTrigger(day=1, hour=0, minute=5, timezone=TZ),
        id="month_close",
        replace_existing=True,
        misfire_grace_time=60 * 60 * 24,  # if we missed it, run on app start
    )
    sched.start()
    _scheduler = sched
    return sched


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
