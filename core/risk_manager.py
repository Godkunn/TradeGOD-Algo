"""
TradeGOD — Risk Manager
The single most important module. Enforces all Funding Pips compliance rules.

$5,000 Account Parameters:
  - Risk per trade:      $50  (1%)
  - Daily kill-switch:   $225 (4.5%)
  - Max drawdown:        $450 (9%)
  - Min hold time:       180 seconds
  - Directional cooldown: 600 seconds (10 min after SL hit)
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from utils.logger import get_logger
from utils.time_ops import now_utc, seconds_since_midnight_utc

log = get_logger("RiskManager")

# ── Database path ──────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "database" / "trade_logs.db"
DB_PATH.parent.mkdir(exist_ok=True)
STATE_PATH = Path(__file__).parent.parent / "database" / "system_state.json"


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket          INTEGER UNIQUE,
            symbol          TEXT,
            direction       TEXT,
            lot_size        REAL,
            entry_price     REAL,
            sl_price        REAL,
            tp_price        REAL,
            open_time       TEXT,
            close_time      TEXT,
            close_price     REAL,
            pnl             REAL,
            status          TEXT DEFAULT 'OPEN',
            strategy        TEXT,
            magic           INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date            TEXT PRIMARY KEY,
            start_balance   REAL,
            end_balance     REAL,
            trades_count    INTEGER,
            wins            INTEGER,
            losses          INTEGER,
            gross_pnl       REAL,
            kill_triggered  INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

_init_db()


class RiskManager:
    """
    Centralized risk controller. Stateful — tracks:
    - Daily P&L against kill-switch threshold
    - Per-symbol directional cooldowns
    - Trade count limits
    - Consistency rules (60% concentration cap)
    - Position hold timers
    """

    def __init__(self, config: dict):
        self.account_size    = config["account"]["starting_capital"]  # $5000
        self.risk_pct        = config["risk_per_trade"]["percent"]     # 1.0
        self.max_risk_dollar = config["risk_per_trade"]["max_dollar_amount"]  # $50

        self.daily_kill      = config["daily_limits"]["kill_switch_dollar"]   # $225
        self.daily_hard      = config["daily_limits"]["hard_limit_dollar"]     # $250
        self.max_dd_kill     = config["max_drawdown"]["kill_switch_dollar"]    # $450

        self.min_hold_sec    = config["position_rules"]["min_hold_time_seconds"]  # 180
        self.cooldown_sec    = config["position_rules"]["directional_cooldown_seconds"]  # 600
        self.max_slippage    = config["position_rules"]["max_slippage_pips"]  # 3
        self.max_positions   = config["position_rules"]["max_positions_per_symbol"]  # 1
        self.max_total_pos   = config["position_rules"]["max_total_open_positions"]  # 3
        self.max_daily_trades = 2  # One Good Trade rule

        self.phase_target    = config["evaluation_targets"]["phase1_profit_target_dollar"]  # $400
        self.conc_cap_pct    = config["consistency_rules"]["single_trade_max_profit_pct_of_phase_target"]  # 60%

        # Runtime state
        self._start_of_day_balance: float = self.account_size
        self._last_day: int = -1
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._kill_triggered: bool = False
        self._cooldowns: dict = {}   # symbol -> (direction, expire_time_utc)
        self._open_positions: dict = {}  # ticket -> open_time

        self._load_state()
        log.info(f"RiskManager initialized — $5K account, ${self.max_risk_dollar}/trade cap")

    # ══════════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════════

    def _save_state(self):
        state = {
            "start_of_day_balance": self._start_of_day_balance,
            "daily_pnl": self._daily_pnl,
            "daily_trades": self._daily_trades,
            "kill_triggered": self._kill_triggered,
            "last_day": self._last_day,
            "cooldowns": {k: [v[0], v[1].isoformat()]
                          for k, v in self._cooldowns.items()},
        }
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self):
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH) as f:
                    state = json.load(f)
                self._start_of_day_balance = state.get("start_of_day_balance", self.account_size)
                self._daily_pnl   = state.get("daily_pnl", 0.0)
                self._daily_trades= state.get("daily_trades", 0)
                self._kill_triggered = state.get("kill_triggered", False)
                self._last_day    = state.get("last_day", -1)
                raw_cd = state.get("cooldowns", {})
                utc = timezone.utc
                self._cooldowns = {
                    k: (v[0], datetime.fromisoformat(v[1]).replace(tzinfo=utc))
                    for k, v in raw_cd.items()
                }
            except Exception as e:
                log.warning(f"State load failed: {e}. Starting fresh.")

    # ══════════════════════════════════════════════════════════════════════════
    # DAILY RESET
    # ══════════════════════════════════════════════════════════════════════════

    def on_new_day(self, current_balance: float):
        """Call this at the start of each trading day (after daily reset)."""
        today = now_utc().timetuple().tm_yday
        if today != self._last_day:
            log.info(f"📅 New trading day. Balance snapshot: ${current_balance:.2f}")
            self._start_of_day_balance = current_balance
            self._daily_pnl    = 0.0
            self._daily_trades = 0
            self._kill_triggered = False
            self._last_day = today
            self._save_state()

    # ══════════════════════════════════════════════════════════════════════════
    # LOT SIZE CALCULATOR
    # ══════════════════════════════════════════════════════════════════════════

    def calculate_lot_size(self, symbol: str, sl_pips: float,
                            pip_value_per_lot: float = 10.0,
                            current_balance: Optional[float] = None) -> float:
        """
        Institutional lot sizing formula:
            Lot = Risk_Amount / (SL_Pips × Pip_Value_Per_Lot)

        Hard cap: never exceed $50 risk per trade on $5K account.
        """
        balance = current_balance or self._start_of_day_balance
        risk_amount = min(
            balance * (self.risk_pct / 100),   # 1% of balance
            self.max_risk_dollar                 # $50 hard cap
        )

        if sl_pips <= 0:
            log.warning(f"Invalid SL pips ({sl_pips}) — defaulting to 0.01 lots")
            return 0.01

        lot = risk_amount / (sl_pips * pip_value_per_lot)

        # Round down to nearest 0.01 (always err conservative)
        lot = max(0.01, round(lot, 2))

        # Cap at 2.0 lots for safety
        lot = min(lot, 2.0)

        log.info(f"💰 Lot size for {symbol}: {lot:.2f} "
                 f"(Risk=${risk_amount:.2f}, SL={sl_pips:.1f} pips)")
        return lot

    # ══════════════════════════════════════════════════════════════════════════
    # DAILY DRAWDOWN KILL-SWITCH
    # ══════════════════════════════════════════════════════════════════════════

    def check_daily_drawdown(self, current_equity: float) -> bool:
        """
        Returns True if trading is ALLOWED.
        Triggers kill-switch if floating equity loss >= $225 (4.5%).
        Leaves 0.5% buffer before Funding Pips hard 5% / $250 limit.
        """
        if self._kill_triggered:
            log.warning("⛔ Kill-switch already triggered. No trading today.")
            return False

        daily_loss = self._start_of_day_balance - current_equity
        if daily_loss >= self.daily_kill:
            self._kill_triggered = True
            self._save_state()
            log.critical(
                f"🚨 DAILY KILL-SWITCH TRIGGERED! "
                f"Loss=${daily_loss:.2f} >= ${self.daily_kill}. "
                f"All trading disabled until midnight UTC."
            )
            return False

        remaining = self.daily_kill - daily_loss
        log.debug(f"Daily DD check: loss=${daily_loss:.2f}, buffer=${remaining:.2f} remaining")
        return True

    def check_max_drawdown(self, current_balance: float) -> bool:
        """
        Returns True if account total drawdown is safe.
        Kill at -$450 (9%) to buffer before Funding Pips -$500 (10%) limit.
        """
        total_loss = self.account_size - current_balance
        if total_loss >= self.max_dd_kill:
            log.critical(
                f"🚨 MAX DRAWDOWN KILL-SWITCH! "
                f"Total loss=${total_loss:.2f} >= ${self.max_dd_kill}. "
                f"ACCOUNT CRITICAL — Manual review required."
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # TRADE COUNT LIMITS
    # ══════════════════════════════════════════════════════════════════════════

    def check_daily_trade_limit(self) -> bool:
        """Max 2 trades per day (One Good Trade rule)."""
        if self._daily_trades >= self.max_daily_trades:
            log.warning(f"📊 Daily trade limit reached ({self._daily_trades}/{self.max_daily_trades}). No more trades today.")
            return False
        return True

    def on_trade_opened(self, ticket: int, symbol: str):
        """Register a new open trade."""
        self._daily_trades += 1
        self._open_positions[ticket] = {
            "symbol": symbol,
            "open_time": now_utc()
        }
        self._save_state()
        log.info(f"📈 Trade #{ticket} on {symbol} registered. Daily trades: {self._daily_trades}")

    def on_trade_closed(self, ticket: int, pnl: float, sl_hit: bool,
                         symbol: str, direction: str):
        """Update daily P&L and set directional cooldown if SL was hit."""
        self._daily_pnl += pnl
        if ticket in self._open_positions:
            del self._open_positions[ticket]

        if sl_hit:
            expire = now_utc() + timedelta(seconds=self.cooldown_sec)
            self._cooldowns[f"{symbol}_{direction}"] = (direction, expire)
            log.warning(
                f"⏰ 10-min cooldown set for {symbol} {direction} "
                f"after SL hit. Expires: {expire.strftime('%H:%M:%S')} UTC"
            )

        self._save_state()
        emoji = "✅" if pnl > 0 else "❌"
        log.info(f"{emoji} Trade #{ticket} closed. PnL=${pnl:.2f}. Daily total=${self._daily_pnl:.2f}")

    # ══════════════════════════════════════════════════════════════════════════
    # DIRECTIONAL COOLDOWN CHECK
    # ══════════════════════════════════════════════════════════════════════════

    def is_cooldown_active(self, symbol: str, direction: str) -> bool:
        """Check if 10-min directional cooldown is active for symbol+direction."""
        key = f"{symbol}_{direction}"
        if key not in self._cooldowns:
            return False
        _, expire = self._cooldowns[key]
        if now_utc() < expire:
            remaining = (expire - now_utc()).seconds
            log.warning(f"⏰ Cooldown active for {symbol} {direction}: {remaining}s remaining")
            return True
        del self._cooldowns[key]  # Expired
        return False

    # ══════════════════════════════════════════════════════════════════════════
    # HOLD TIME COMPLIANCE
    # ══════════════════════════════════════════════════════════════════════════

    def can_close_trade(self, ticket: int) -> bool:
        """True only if position has been open >= 180 seconds (HFT compliance)."""
        if ticket not in self._open_positions:
            return True  # Not tracked, allow
        pos = self._open_positions[ticket]
        elapsed = (now_utc() - pos["open_time"]).total_seconds()
        if elapsed < self.min_hold_sec:
            remaining = self.min_hold_sec - elapsed
            log.warning(
                f"🔒 Trade #{ticket} hold-time compliance: {remaining:.0f}s remaining "
                f"before allowed to close (HFT rule: min {self.min_hold_sec}s)"
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # CONSISTENCY RULE (60% Concentration Cap)
    # ══════════════════════════════════════════════════════════════════════════

    def should_partial_close(self, current_pnl: float) -> Tuple[bool, float]:
        """
        If a single trade's profit exceeds 60% of phase target ($240),
        force a 50% partial close to satisfy Funding Pips concentration rule.
        Returns (should_close, close_percent)
        """
        max_single_profit = self.phase_target * (self.conc_cap_pct / 100)  # $240
        if current_pnl >= max_single_profit:
            log.warning(
                f"💰 Profit concentration cap hit! PnL=${current_pnl:.2f} >= ${max_single_profit:.2f}. "
                f"Partial closing 50% to comply with Funding Pips consistency rule."
            )
            return True, 50.0
        return False, 0.0

    # ══════════════════════════════════════════════════════════════════════════
    # MASTER GATE: ALL-IN-ONE PRE-TRADE CHECK
    # ══════════════════════════════════════════════════════════════════════════

    def pre_trade_check(self, symbol: str, direction: str,
                         current_equity: float,
                         current_balance: float,
                         open_positions_count: int) -> Tuple[bool, str]:
        """
        Single call to validate ALL risk rules before firing an order.
        Returns (allowed: bool, reason: str)
        """
        checks = [
            (self.check_daily_drawdown(current_equity),
             f"Daily drawdown kill-switch (loss >= ${self.daily_kill})"),
            (self.check_max_drawdown(current_balance),
             f"Max drawdown kill-switch (loss >= ${self.max_dd_kill})"),
            (self.check_daily_trade_limit(),
             f"Daily trade limit ({self.max_daily_trades} trades/day)"),
            (not self.is_cooldown_active(symbol, direction),
             f"10-min directional cooldown active for {symbol} {direction}"),
            (open_positions_count < self.max_total_pos,
             f"Max open positions reached ({self.max_total_pos})"),
        ]

        for allowed, reason in checks:
            if not allowed:
                log.warning(f"🚫 Trade BLOCKED: {reason}")
                return False, reason

        return True, "OK"

    # ══════════════════════════════════════════════════════════════════════════
    # DATABASE LOGGING
    # ══════════════════════════════════════════════════════════════════════════

    def log_trade_to_db(self, ticket: int, symbol: str, direction: str,
                         lot_size: float, entry: float, sl: float, tp: float,
                         strategy: str, magic: int):
        """Persist trade open to SQLite."""
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO trades
                (ticket, symbol, direction, lot_size, entry_price, sl_price,
                 tp_price, open_time, status, strategy, magic)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (ticket, symbol, direction, lot_size, entry, sl, tp,
                  now_utc().isoformat(), "OPEN", strategy, magic))
            conn.commit()
        finally:
            conn.close()

    def close_trade_in_db(self, ticket: int, close_price: float, pnl: float):
        """Update trade record on close."""
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                UPDATE trades
                SET close_time=?, close_price=?, pnl=?, status=?
                WHERE ticket=?
            """, (now_utc().isoformat(), close_price, pnl, "CLOSED", ticket))
            conn.commit()
        finally:
            conn.close()

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def is_killed(self) -> bool:
        return self._kill_triggered
