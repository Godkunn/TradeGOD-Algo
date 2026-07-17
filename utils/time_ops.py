"""
TradeGOD — Session & Time Utilities
Handles UTC/IST conversions, killzone checks, session identification.
"""

from datetime import datetime, timezone, timedelta
from config.app_config import SESSIONS, FRIDAY_CLOSE_UTC_HOUR

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def utc_hour() -> int:
    return now_utc().hour


def utc_weekday() -> int:
    """0=Monday ... 4=Friday ... 6=Sunday"""
    return now_utc().weekday()


def is_weekend() -> bool:
    """Returns True if Saturday or Sunday UTC."""
    wd = utc_weekday()
    return wd >= 5


def is_friday_close() -> bool:
    """True if Friday >= 21:00 UTC — trigger weekend kill-switch."""
    return utc_weekday() == 4 and utc_hour() >= FRIDAY_CLOSE_UTC_HOUR


def is_monday() -> bool:
    return utc_weekday() == 0


def is_friday() -> bool:
    return utc_weekday() == 4


def is_blocked_day() -> bool:
    """Monday and Friday are low-liquidity/manipulation days."""
    return is_monday() or is_friday()


def get_current_session() -> str:
    """Return the name of the current dominant trading session."""
    h = utc_hour()
    # NY has priority during overlap
    if SESSIONS["NY"]["open"] <= h < SESSIONS["NY"]["close"]:
        return "NY"
    if SESSIONS["LONDON"]["open"] <= h < SESSIONS["LONDON"]["close"]:
        return "LONDON"
    if SESSIONS["ASIAN"]["open"] <= h < SESSIONS["ASIAN"]["close"]:
        return "ASIAN"
    return "CLOSED"


def is_session_active(session: str) -> bool:
    """Check if a specific session is currently active."""
    s = SESSIONS.get(session.upper())
    if not s:
        return False
    h = utc_hour()
    return s["open"] <= h < s["close"]


def is_trading_allowed() -> bool:
    """
    Master session gate:
    - London Open to NY Close: 07:00–21:00 UTC
    - Block weekends
    - Block Friday after 21:00 UTC
    """
    if is_weekend():
        return False
    if is_friday_close():
        return False
    h = utc_hour()
    return 7 <= h < 21


def is_london_killzone() -> bool:
    """London Open killzone: 07:00–09:00 UTC."""
    h = utc_hour()
    return 7 <= h < 9


def is_ny_killzone() -> bool:
    """New York Open killzone: 13:00–15:00 UTC."""
    h = utc_hour()
    return 13 <= h < 15


def seconds_since_midnight_utc() -> int:
    now = now_utc()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((now - midnight).total_seconds())


def format_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
