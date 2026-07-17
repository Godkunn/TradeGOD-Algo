"""
TradeGOD — Main Orchestrator
The central brain. Initializes all modules, runs the main trading loop,
and coordinates strategies → compliance → risk → execution → telemetry.

Run with: python main_orchestrator.py
"""

import time
import signal
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

from config.app_config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    ZMQ_PUSH_PORT, ZMQ_PULL_PORT,
    RISK, STRATEGY, SYMBOLS,
    MAGIC_NUMBER, ACCOUNT_SIZE, MAX_RISK_DOLLAR, DAILY_KILL_DOLLAR
)
from core.data_feed import MT5DataFeed
from core.risk_manager import RiskManager
from core.compliance_guard import ComplianceGuard
from core.news_filter import NewsFilter
from core.zmq_bridge import ZMQBridge
from strategies import (
    VolatilitySqueezeStrategy, SMCOrderBlockFVGStrategy,
    LiquiditySweepStrategy, GoldScalperStrategy,
    REMLiquidityStrategy, AsymmetricRiskStrategy
)
from telemetry.telegram_bot import TelegramNotifier
from utils.logger import get_logger
from utils.time_ops import now_utc, is_trading_allowed, is_weekend

log = get_logger("Orchestrator")


class TradeGODOrchestrator:
    """
    Main trading loop controller.
    Runs continuously during London–NY session (07:00–21:00 UTC),
    processes each symbol on every new bar close.
    """

    SYMBOLS_TO_TRADE = ["EURUSD", "GBPUSD", "XAUUSD", "NZDUSD", "AUDUSD"]
    TIMEFRAME        = "M15"
    BAR_SECONDS      = 900   # 15 minutes = 900 seconds
    HEARTBEAT_SEC    = 14400  # Send heartbeat every 4 hours (per LIVE.txt blueprint)

    def __init__(self):
        log.info("=" * 60)
        log.info("  TradeGOD Quant Fund — Initializing...")
        log.info("=" * 60)

        # ── Data Feed ─────────────────────────────────────────────────────────
        self.feed = MT5DataFeed(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH if MT5_PATH else None)

        # ── Risk Manager ──────────────────────────────────────────────────────
        self.risk = RiskManager(RISK)

        # ── Compliance ────────────────────────────────────────────────────────
        self.news = NewsFilter()
        self.compliance = ComplianceGuard(RISK, self.news)

        # ── ZMQ Bridge ────────────────────────────────────────────────────────
        self.zmq = ZMQBridge(ZMQ_PUSH_PORT, ZMQ_PULL_PORT)

        # ── Strategies ────────────────────────────────────────────────────────
        self.strategies = [
            VolatilitySqueezeStrategy(STRATEGY, SYMBOLS),
            SMCOrderBlockFVGStrategy(STRATEGY, SYMBOLS),
            LiquiditySweepStrategy(STRATEGY, SYMBOLS),
            GoldScalperStrategy(STRATEGY, SYMBOLS),
            REMLiquidityStrategy(STRATEGY, SYMBOLS),
        ]
        self.asymmetric = AsymmetricRiskStrategy(STRATEGY, SYMBOLS, ACCOUNT_SIZE)

        # ── Telegram ──────────────────────────────────────────────────────────
        self.tg = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

        self._running        = False
        self._last_bar_time  = {}
        self._last_heartbeat = 0.0

    # ══════════════════════════════════════════════════════════════════════════
    # STARTUP & SHUTDOWN
    # ══════════════════════════════════════════════════════════════════════════

    def start(self):
        """Connect all modules and begin trading loop."""
        log.info("Connecting to MT5...")
        if not self.feed.connect():
            log.critical("MT5 connection failed. Exiting.")
            sys.exit(1)

        log.info("Connecting ZMQ bridge...")
        if not self.zmq.connect():
            log.critical("ZMQ bridge failed. Is TradeGOD_EA.mq5 running in MT5?")
            sys.exit(1)

        account = self.feed.get_account_info()
        if account:
            log.info(f"💰 Account: ${account['balance']:.2f} | Equity=${account['equity']:.2f}")
            self.risk.on_new_day(account["balance"])

        self.tg.send_startup(ACCOUNT_SIZE, MAX_RISK_DOLLAR, DAILY_KILL_DOLLAR)
        self._running = True

        # Register graceful shutdown
        signal.signal(signal.SIGINT,  self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        log.info("✅ TradeGOD running. Press Ctrl+C to stop.")
        self._main_loop()

    def _shutdown_handler(self, sig, frame):
        log.warning("⚠️ Shutdown signal received. Stopping gracefully...")
        self._running = False
        self.zmq.send_close_all("graceful_shutdown")
        self.feed.disconnect()
        self.zmq.disconnect()
        self.tg.send_raw("🛑 <b>TradeGOD STOPPED</b> (graceful shutdown)")
        sys.exit(0)

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════════

    def _main_loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(5)  # 5-second polling interval

    def _tick(self):
        """Single iteration of the main loop."""
        # ── Account state ─────────────────────────────────────────────────────
        account = self.feed.get_account_info()
        if not account:
            log.warning("Cannot get account info from MT5")
            return

        balance = account["balance"]
        equity  = account["equity"]

        # ── Daily reset ───────────────────────────────────────────────────────
        self.risk.on_new_day(balance)

        # ── Weekend close ─────────────────────────────────────────────────────
        if self.compliance.should_close_for_weekend():
            positions = self.feed.get_open_positions(MAGIC_NUMBER)
            if positions:
                self.zmq.send_close_all("weekend_close")
                self.tg.send_weekend_close(len(positions))
            return

        # ── Kill-switch ───────────────────────────────────────────────────────
        if not self.risk.check_daily_drawdown(equity):
            return
        if not self.risk.check_max_drawdown(balance):
            return

        # ── Keep-alive check ──────────────────────────────────────────────────
        if self.compliance.should_keepalive():
            self.zmq.send_keepalive_trade("EURUSD", MAGIC_NUMBER)
            self.compliance.record_trade_activity()

        # ── Trading hours ─────────────────────────────────────────────────────
        if not is_trading_allowed():
            return

        # ── Heartbeat ─────────────────────────────────────────────────────────
        now_ts = time.time()
        if now_ts - self._last_heartbeat >= self.HEARTBEAT_SEC:
            positions = self.feed.get_open_positions(MAGIC_NUMBER)
            from utils.time_ops import get_current_session
            self.tg.send_heartbeat(
                balance=balance, equity=equity,
                daily_pnl=self.risk.daily_pnl,
                open_positions=len(positions),
                session=get_current_session(),
                kill_status=self.risk.is_killed
            )
            self._last_heartbeat = now_ts

        # ── Strategy scan (on new bar) ────────────────────────────────────────
        for symbol in self.SYMBOLS_TO_TRADE:
            self._scan_symbol(symbol, balance, equity)

    def _scan_symbol(self, symbol: str, balance: float, equity: float):
        """Run all applicable strategies on this symbol."""
        df = self.feed.get_ohlcv(symbol, self.TIMEFRAME, count=200)
        if df is None or len(df) < 50:
            return

        # New bar check — only analyze on bar close
        last_bar_time = df.index[-2]  # Use second-to-last (closed bar)
        if self._last_bar_time.get(symbol) == last_bar_time:
            return  # Same bar, skip
        self._last_bar_time[symbol] = last_bar_time

        # Get symbol config
        sym_config = SYMBOLS.get(symbol, {})
        allowed_strategies = sym_config.get("strategies_allowed", [])
        max_spread         = sym_config.get("max_spread_pips", 3.0)

        # ── Compliance check ──────────────────────────────────────────────────
        spread = self.feed.get_spread_pips(symbol)
        ok, reason = self.compliance.full_compliance_check(
            symbol=symbol,
            current_spread_pips=spread,
            max_spread_pips=max_spread
        )
        if not ok:
            log.debug(f"{symbol}: blocked — {reason}")
            return

        # ── Run each strategy ─────────────────────────────────────────────────
        signals = []
        for strategy in self.strategies:
            if strategy.name.split("_")[0].replace("S0", "S") not in allowed_strategies:
                if not any(s in allowed_strategies for s in ["S01", "S02", "S03", "S04", "S05"]):
                    pass  # Allow all if not specified
            try:
                signal = strategy.analyze(df.iloc[:-1], symbol, self.TIMEFRAME)
                if signal and signal.valid:
                    signals.append(signal)
            except Exception as e:
                log.error(f"Strategy {strategy.name} error on {symbol}: {e}")

        if not signals:
            return

        # ── Select highest priority signal ────────────────────────────────────
        best = min(signals, key=lambda s: s.priority)

        # ── Apply asymmetric risk mode ────────────────────────────────────────
        best = self.asymmetric.apply_risk_mode(best, balance)

        # ── Pre-trade risk check ──────────────────────────────────────────────
        open_positions = self.feed.get_open_positions(MAGIC_NUMBER)
        ok, reason = self.risk.pre_trade_check(
            symbol=symbol,
            direction=best.direction,
            current_equity=equity,
            current_balance=balance,
            open_positions_count=len(open_positions)
        )
        if not ok:
            log.warning(f"{symbol}: pre-trade blocked — {reason}")
            return

        # ── Calculate lot size ────────────────────────────────────────────────
        sl_pips = best.get_sl_pips(best.entry, best.sl, symbol) if hasattr(best, 'get_sl_pips') else \
                  abs(best.entry - best.sl) / sym_config.get("pip_size", 0.0001)
        pip_val = sym_config.get("pip_value_per_lot", 10.0)
        lot     = self.risk.calculate_lot_size(symbol, sl_pips, pip_val, balance)
        best.lot_size = lot

        # ── Execute via ZMQ ───────────────────────────────────────────────────
        risk_dollar = sl_pips * pip_val * lot
        self.zmq.send_open_buy(symbol, lot, best.entry, best.sl, best.tp,
                                MAGIC_NUMBER, best.comment) \
            if best.direction == "BUY" else \
        self.zmq.send_open_sell(symbol, lot, best.entry, best.sl, best.tp,
                                 MAGIC_NUMBER, best.comment)

        # ── Register & alert ──────────────────────────────────────────────────
        self.compliance.record_trade_activity()
        self.risk.log_trade_to_db(
            ticket=0, symbol=symbol, direction=best.direction,
            lot_size=lot, entry=best.entry, sl=best.sl, tp=best.tp,
            strategy=best.strategy, magic=MAGIC_NUMBER
        )

        self.tg.send_trade_opened(
            symbol=symbol, direction=best.direction, lot=lot,
            entry=best.entry, sl=best.sl, tp=best.tp,
            strategy=best.strategy, risk_dollar=risk_dollar
        )

        log.info(
            f"🎯 TRADE FIRED: {best.direction} {symbol} {lot:.2f} lots | "
            f"Strategy={best.strategy} | Risk=${risk_dollar:.2f}"
        )


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from config.app_config import validate_config
    validate_config()
    bot = TradeGODOrchestrator()
    bot.start()
