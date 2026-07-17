"""
TradeGOD — News Filter
Fetches ForexFactory economic calendar and creates blackout windows
around high-impact (red-folder) news events.

Blackout window: 5 minutes before AND 5 minutes after each event.
Trades opened 5+ hours before news are exempt (positions stay open).
"""

import requests
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from utils.logger import get_logger
from utils.time_ops import now_utc

log = get_logger("NewsFilter")

# ForexFactory free JSON calendar (weekly)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Currency pairs and their relevant currencies
SYMBOL_CURRENCIES = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "XAUUSD": ["USD"],  # Gold follows DXY
    "NZDUSD": ["NZD", "USD"],
    "AUDUSD": ["AUD", "USD"],
    "USOUSD": ["USD"],  # Oil follows USD
}

HIGH_IMPACT_EVENTS = [
    "Non-Farm", "NFP", "CPI", "FOMC", "GDP", "PMI", "Retail Sales",
    "Interest Rate", "Federal Reserve", "Fed", "ECB", "BOE", "BOJ",
    "Employment", "Unemployment", "Inflation", "Trade Balance",
    "Consumer Price", "Producer Price", "ISM"
]


class NewsFilter:
    """
    Polls ForexFactory weekly calendar and caches high-impact events.
    Provides is_blackout() check before any order execution.
    """

    def __init__(self, blackout_minutes: int = 5,
                 exempt_if_opened_hours_before: float = 5.0):
        self.blackout_sec = blackout_minutes * 60  # 300 seconds
        self.exempt_hours = exempt_if_opened_hours_before
        self._events: List[dict] = []
        self._last_fetch: Optional[datetime] = None
        self._fetch_interval_hours = 6  # Refresh calendar every 6 hours

    def _fetch_calendar(self) -> bool:
        """Download ForexFactory calendar. Returns True on success."""
        try:
            resp = requests.get(FF_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            high_impact = []
            for event in data:
                if event.get("impact", "").upper() != "HIGH":
                    continue
                # Parse event datetime (format: "2024-01-15T08:30:00-05:00")
                event_time_str = event.get("date", "")
                if not event_time_str:
                    continue
                try:
                    # ForexFactory returns Eastern Time typically
                    event_time = datetime.fromisoformat(event_time_str)
                    # Normalize to UTC
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=timezone.utc)
                    else:
                        event_time = event_time.astimezone(timezone.utc)
                    high_impact.append({
                        "title":    event.get("title", "Unknown"),
                        "currency": event.get("country", ""),
                        "time_utc": event_time,
                    })
                except Exception as e:
                    log.debug(f"Skipping malformed event: {e}")

            self._events = high_impact
            self._last_fetch = now_utc()
            log.info(f"📰 News calendar refreshed: {len(self._events)} high-impact events this week")
            return True

        except requests.RequestException as e:
            log.warning(f"⚠️ News calendar fetch failed: {e}. Using cached data.")
            return False

    def refresh_if_needed(self):
        """Refresh calendar if stale (> 6 hours old)."""
        if self._last_fetch is None:
            self._fetch_calendar()
        elif (now_utc() - self._last_fetch).total_seconds() > self._fetch_interval_hours * 3600:
            self._fetch_calendar()

    def is_blackout(self, symbol: str,
                     position_open_time: Optional[datetime] = None) -> bool:
        """
        Returns True if trading is blocked due to upcoming/recent high-impact news.

        Args:
            symbol: Trading pair (e.g., "EURUSD")
            position_open_time: If set and position was opened 5+ hours ago,
                                 the blackout only blocks NEW orders, not existing trades.
        """
        self.refresh_if_needed()

        # Get relevant currencies for this symbol
        relevant_currencies = SYMBOL_CURRENCIES.get(symbol.upper(), ["USD"])
        now = now_utc()

        for event in self._events:
            event_currency = event["currency"].upper()
            event_time     = event["time_utc"]

            # Only check events relevant to this symbol's currencies
            if event_currency not in [c.upper() for c in relevant_currencies]:
                continue

            seconds_to_event = (event_time - now).total_seconds()
            seconds_since_event = (now - event_time).total_seconds()

            # Pre-event blackout: 5 minutes before
            in_pre_blackout = 0 < seconds_to_event <= self.blackout_sec

            # Post-event blackout: 5 minutes after
            in_post_blackout = 0 < seconds_since_event <= self.blackout_sec

            if in_pre_blackout or in_post_blackout:
                # Check exemption: position opened 5+ hours ago stays open
                if position_open_time is not None:
                    hours_open = (now - position_open_time).total_seconds() / 3600
                    if hours_open >= self.exempt_hours:
                        log.debug(
                            f"📰 News blackout for {symbol} ({event['title']}) — "
                            f"existing position exempt (opened {hours_open:.1f}h ago)"
                        )
                        return False  # Existing old position is exempt

                direction = "in" if in_pre_blackout else "just after"
                log.warning(
                    f"📰 NEWS BLACKOUT: {symbol} blocked — {event['title']} "
                    f"({event_currency}) {direction} {abs(seconds_to_event/60):.1f}min"
                )
                return True  # Blackout active

        return False  # Safe to trade

    def next_news_event(self, symbol: str) -> Optional[dict]:
        """Return the next high-impact event affecting this symbol."""
        self.refresh_if_needed()
        relevant = SYMBOL_CURRENCIES.get(symbol.upper(), ["USD"])
        now = now_utc()
        upcoming = [
            e for e in self._events
            if e["currency"].upper() in [c.upper() for c in relevant]
            and e["time_utc"] > now
        ]
        if not upcoming:
            return None
        upcoming.sort(key=lambda x: x["time_utc"])
        return upcoming[0]

    def get_todays_events(self) -> List[dict]:
        """Return all high-impact events for today."""
        self.refresh_if_needed()
        today = now_utc().date()
        return [e for e in self._events if e["time_utc"].date() == today]
