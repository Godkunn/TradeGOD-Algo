"""
TradeGOD — Telegram Bot
Sends real-time trade alerts, heartbeat, and PnL reports to your phone.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from utils.logger import get_logger
from utils.time_ops import now_ist, format_ist, now_utc

log = get_logger("TelegramBot")

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False
    log.warning("python-telegram-bot not installed. Telegram alerts disabled.")


class TelegramNotifier:
    """Sends trade alerts and system events to Telegram."""

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._bot: Optional[object] = None
        self._enabled = TG_AVAILABLE and bool(token) and bool(chat_id)

        if self._enabled:
            self._bot = Bot(token=token)
            log.info("✅ Telegram bot initialized")
        else:
            log.warning("Telegram disabled (token/chat_id missing or package unavailable)")

    def _run(self, coro):
        """Run async coroutine synchronously."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.ensure_future(coro)
            return loop.run_until_complete(coro)
        except Exception as e:
            log.error(f"Telegram send error: {e}")

    async def _send(self, text: str):
        if not self._enabled or not self._bot:
            return
        await self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode=ParseMode.HTML
        )

    async def _send_photo(self, photo_path: str, caption: str):
        if not self._enabled or not self._bot:
            return
        with open(photo_path, "rb") as f:
            await self._bot.send_photo(
                chat_id=self.chat_id,
                photo=f,
                caption=caption,
                parse_mode=ParseMode.HTML
            )

    # ══════════════════════════════════════════════════════════════════════════
    # TRADE ALERTS
    # ══════════════════════════════════════════════════════════════════════════

    def send_trade_opened(self, symbol: str, direction: str, lot: float,
                           entry: float, sl: float, tp: float,
                           strategy: str, risk_dollar: float):
        emoji = "🟢" if direction == "BUY" else "🔴"
        sl_pips = abs(entry - sl) / (0.0001 if "JPY" not in symbol else 0.01)
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        msg = (
            f"{emoji} <b>TRADE OPENED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{symbol}</b> {direction}\n"
            f"💰 Entry:  <code>{entry:.5f}</code>\n"
            f"🛑 SL:     <code>{sl:.5f}</code> ({sl_pips:.1f} pips)\n"
            f"🎯 TP:     <code>{tp:.5f}</code>\n"
            f"📦 Lot:    <code>{lot:.2f}</code>\n"
            f"⚡ Risk:   <code>${risk_dollar:.2f}</code>\n"
            f"📈 RR:     <code>1:{rr:.1f}</code>\n"
            f"🤖 Strat:  <code>{strategy}</code>\n"
            f"🕐 Time:   <code>{format_ist(now_utc())}</code>"
        )
        self._run(self._send(msg))

    def send_trade_closed(self, symbol: str, direction: str, ticket: int,
                           pnl: float, close_price: float,
                           daily_pnl: float, reason: str = "TP/SL"):
        emoji = "✅" if pnl >= 0 else "❌"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        msg = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{symbol}</b> {direction} #{ticket}\n"
            f"💵 PnL:    <code>{pnl_str}</code>\n"
            f"📍 Price:  <code>{close_price:.5f}</code>\n"
            f"📅 Daily:  <code>${daily_pnl:+.2f}</code>\n"
            f"📝 Reason: <code>{reason}</code>\n"
            f"🕐 Time:   <code>{format_ist(now_utc())}</code>"
        )
        self._run(self._send(msg))

    def send_kill_switch_alert(self, daily_loss: float,
                                kill_threshold: float, account_size: float):
        pct = (daily_loss / account_size) * 100
        msg = (
            f"🚨 <b>KILL-SWITCH TRIGGERED</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💀 Daily Loss: <code>${daily_loss:.2f} ({pct:.1f}%)</code>\n"
            f"🔒 Threshold:  <code>${kill_threshold:.2f} (4.5%)</code>\n"
            f"⛔ ALL TRADING DISABLED UNTIL MIDNIGHT UTC\n"
            f"🕐 Time: <code>{format_ist(now_utc())}</code>\n\n"
            f"<i>Bot will resume automatically at next daily reset.</i>"
        )
        self._run(self._send(msg))

    def send_partial_close(self, symbol: str, ticket: int,
                            closed_lot: float, remaining_lot: float,
                            pnl: float, new_sl: float):
        msg = (
            f"⚡ <b>PARTIAL CLOSE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{symbol}</b> #{ticket}\n"
            f"📦 Closed: <code>{closed_lot:.2f} lots</code>\n"
            f"📦 Remaining: <code>{remaining_lot:.2f} lots</code>\n"
            f"💵 Locked PnL: <code>${pnl:.2f}</code>\n"
            f"🛑 New SL (BE): <code>{new_sl:.5f}</code>"
        )
        self._run(self._send(msg))

    def send_news_blackout(self, symbol: str, event_title: str,
                            minutes_to_event: float):
        msg = (
            f"📰 <b>NEWS BLACKOUT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Symbol: <b>{symbol}</b>\n"
            f"📌 Event:  <code>{event_title}</code>\n"
            f"⏰ In:     <code>{minutes_to_event:.1f} minutes</code>\n"
            f"🔇 <i>Trading paused ±5min around event</i>"
        )
        self._run(self._send(msg))

    def send_heartbeat(self, balance: float, equity: float,
                        daily_pnl: float, open_positions: int,
                        session: str, kill_status: bool):
        status = "🟢 RUNNING" if not kill_status else "🔴 KILLED"
        msg = (
            f"💓 <b>TradeGOD Heartbeat</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${balance:.2f}</code>\n"
            f"📈 Equity:  <code>${equity:.2f}</code>\n"
            f"📅 Daily P&L: <code>${daily_pnl:+.2f}</code>\n"
            f"📊 Positions: <code>{open_positions}</code>\n"
            f"🌍 Session: <code>{session}</code>\n"
            f"🤖 Status:  {status}\n"
            f"🕐 <code>{format_ist(now_utc())}</code>"
        )
        self._run(self._send(msg))

    def send_weekend_close(self, closed_count: int):
        msg = (
            f"🗓️ <b>WEEKEND CLOSE</b>\n"
            f"Closed {closed_count} position(s) for the weekend.\n"
            f"Bot resumes Monday 07:00 UTC. 🌙"
        )
        self._run(self._send(msg))

    def send_startup(self, account_size: float, risk_per_trade: float,
                      kill_switch_level: float):
        msg = (
            f"🚀 <b>TradeGOD STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Account: <code>${account_size:.0f}</code>\n"
            f"⚡ Risk/Trade: <code>${risk_per_trade:.0f} (1%)</code>\n"
            f"🛡️ Kill-Switch: <code>${kill_switch_level:.0f} (4.5%)</code>\n"
            f"🎯 Strategies: S01 Squeeze | S02 SMC | S03 Sweep\n"
            f"              S04 Gold | S05 REM | S06 Asymmetric\n"
            f"🕐 <code>{format_ist(now_utc())}</code>\n\n"
            f"<i>Compliance: Funding Pips rules active ✅</i>"
        )
        self._run(self._send(msg))

    def send_raw(self, message: str):
        """Send any custom message."""
        self._run(self._send(message))

    def send_backtest_report(self, r, photo_path: str):
        """Send backtest visual report and stats."""
        emoji = "✅" if r.win_rate >= 55 else "⚠️"
        msg = (
            f"📈 <b>BACKTEST RESULTS: {r.strategy}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> {r.symbol} ({r.timeframe})\n"
            f"💵 <b>Total PnL:</b> ${r.total_pnl:+.2f}\n"
            f"📉 <b>Max Drawdown:</b> {r.max_dd_pct:.1f}%\n"
            f"🎯 <b>Win Rate:</b> {r.win_rate:.1f}% {emoji}\n"
            f"⚖️ <b>Avg RR:</b> 1:{r.avg_rr:.2f}\n"
            f"🔁 <b>Trades:</b> {r.total_trades} ({r.wins}W / {r.losses}L)\n"
            f"📈 <b>Profit Factor:</b> {r.profit_factor:.2f}\n"
            f"🚀 <b>Sharpe Ratio:</b> {r.sharpe_ratio:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Generated by TradeGOD Quant Engine</i>"
        )
        self._run(self._send_photo(photo_path, msg))
