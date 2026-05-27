"""Timezone-aware date helpers. All app-level time math runs in Asia/Dubai."""
from datetime import datetime, date
from calendar import monthrange

import pytz

from config import Config

TZ = pytz.timezone(Config.TIMEZONE)


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def current_month_key() -> str:
    return now().strftime("%Y-%m")


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def days_remaining_in_month(d: date = None) -> int:
    """Inclusive of today."""
    d = d or today()
    return days_in_month(d.year, d.month) - d.day + 1
