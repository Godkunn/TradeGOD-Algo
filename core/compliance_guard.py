"""
TradeGOD — Compliance Guard
The outer firewall. Combines all Funding Pips rules into one gatekeeper.
Checks: session hours, weekend rules, news blackouts, HFT blocks, cooldowns.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from utils.logger import get_logger
from utils.time_ops import (is_trading_allowed, is_weekend, is_friday_close,
                             is_blocked_day, get_current_session, is_london_killzone,
                             is_ny_killzone, now_utc)
from core.news_filter import NewsFilter

log = get_logger("ComplianceGuard")


class ComplianceGuard:
    """
    Single compliance checkpoint before every trade action.
    All rules from RULEBOOK.txt and Funding Pips policy.
    """

    def __init__(self, risk_config: dict, news_filter: Optional[NewsFilter] = None):
        self._news = news_filter or NewsFilter()
        self._weekend_close_done = False
        self._spread_limit_pips = risk_config["position_rules"]["max_slippage_pips"]
        # Track keepalive state
        self._last_trade_time: Optional[datetime] = None
        self._keepalive_enabled = True
        self._idle_days_trigger = 8

    # ══════════════════════════════════════════════════════════════════════════
    # SESSION GATE
    # ══════════════════════════════════════════════════════════════════════════

    def is_session_valid(self, require_killzone: bool = False) -> Tuple[bool, str]:
        """
        Trading is allowed only during London Open to NY Close (07:00–21:00 UTC).
        Optionally require being in a killzone for higher precision entries.
        """
        if is_weekend():
            return False, "Weekend — markets closed"

        if is_friday_close():
            return False, "Friday close (after 21:00 UTC) — weekend kill"

        if not is_trading_allowed():
            h = now_utc().hour
            return False, f"Outside trading hours (UTC {h}:00). Valid: 07:00–21:00"

        if is_blocked_day():
            day = now_utc().strftime("%A")
            return False, f"{day} is blocked (Mon/Fri = low liquidity/fakeouts)"

        if require_killzone:
            in_kz = is_london_killzone() or is_ny_killzone()
            if not in_kz:
                return False, "Not in a killzone (London 07–09 / NY 13–15 UTC)"

        return True, get_current_session()

    # ══════════════════════════════════════════════════════════════════════════
    # NEWS GATE
    # ══════════════════════════════════════════════════════════════════════════

    def is_news_safe(self, symbol: str,
                      position_open_time: Optional[datetime] = None) -> Tuple[bool, str]:
        """Check news blackout for this symbol."""
        if self._news.is_blackout(symbol, position_open_time):
            return False, f"News blackout active for {symbol}"
        return True, "No news conflict"

    # ══════════════════════════════════════════════════════════════════════════
    # SPREAD GATE
    # ══════════════════════════════════════════════════════════════════════════

    def is_spread_acceptable(self, symbol: str,
                              current_spread_pips: float,
                              max_spread_pips: float) -> Tuple[bool, str]:
        """Block trades if spread is too wide (common during news/sweeps)."""
        if current_spread_pips > max_spread_pips:
            return False, (
                f"Spread too wide: {current_spread_pips:.1f} pips "
                f"> limit {max_spread_pips:.1f} pips for {symbol}"
            )
        return True, f"Spread OK ({current_spread_pips:.1f} pips)"

    # ══════════════════════════════════════════════════════════════════════════
    # WEEKEND CLOSE (Friday 21:00 UTC)
    # ══════════════════════════════════════════════════════════════════════════

    def should_close_for_weekend(self) -> bool:
        """True on Friday >= 21:00 UTC — trigger close all positions."""
        triggered = is_friday_close()
        if triggered and not self._weekend_close_done:
            log.warning("🗓️ Friday 21:00 UTC — Weekend close triggered. All positions will close.")
            self._weekend_close_done = True
        if not is_friday_close():
            self._weekend_close_done = False  # Reset for next week
        return triggered

    # ══════════════════════════════════════════════════════════════════════════
    # KEEP-ALIVE (Account Activity Maintenance)
    # ══════════════════════════════════════════════════════════════════════════

    def should_keepalive(self) -> bool:
        """
        True if account has been idle for >= 8 days.
        Triggers a 0.01-lot maintenance trade to prevent account dormancy.
        This is legitimate account maintenance within Funding Pips rules:
        - Min hold time (185s > 180s) satisfied
        - Not HFT (single trade, not automated scalping)
        """
        if not self._keepalive_enabled:
            return False
        if self._last_trade_time is None:
            return False  # Never traded — no keepalive needed
        idle_days = (now_utc() - self._last_trade_time).total_seconds() / 86400
        if idle_days >= self._idle_days_trigger:
            log.info(f"💤 Account idle {idle_days:.1f} days — keepalive trade needed")
            return True
        return False

    def record_trade_activity(self):
        """Call this whenever any trade is opened to reset idle timer."""
        self._last_trade_time = now_utc()

    # ══════════════════════════════════════════════════════════════════════════
    # MASTER COMPLIANCE CHECK
    # ══════════════════════════════════════════════════════════════════════════

    def full_compliance_check(self, symbol: str,
                               current_spread_pips: float,
                               max_spread_pips: float,
                               require_killzone: bool = False,
                               position_open_time: Optional[datetime] = None
                               ) -> Tuple[bool, str]:
        """
        Full compliance check. Returns (allowed: bool, reason: str).
        Call this before every OrderSend() attempt.
        """
        # 0. Global Mon/Fri block (per RULEBOOK.txt + LIVE.txt)
        if is_blocked_day():
            day = now_utc().strftime("%A")
            return False, f"[DAY_BLOCK] {day} — Low liquidity/fakeouts. Tue/Wed/Thu only."

        # 1. Session time
        ok, reason = self.is_session_valid(require_killzone)
        if not ok:
            return False, f"[SESSION] {reason}"

        # 2. News blackout
        ok, reason = self.is_news_safe(symbol, position_open_time)
        if not ok:
            return False, f"[NEWS] {reason}"

        # 3. Spread check
        ok, reason = self.is_spread_acceptable(symbol, current_spread_pips, max_spread_pips)
        if not ok:
            return False, f"[SPREAD] {reason}"

        return True, "✅ Compliance OK"

    def get_status_report(self) -> dict:
        """Returns a dict of current compliance status for dashboard display."""
        return {
            "session":          get_current_session(),
            "trading_allowed":  is_trading_allowed(),
            "is_weekend":       is_weekend(),
            "is_blocked_day":   is_blocked_day(),
            "in_london_kz":     is_london_killzone(),
            "in_ny_kz":         is_ny_killzone(),
            "friday_close":     is_friday_close(),
            "utc_time":         now_utc().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
