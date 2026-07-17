"""
TradeGOD — Backtesting Engine
Vectorized backtester using SMC strategy logic on historical OHLCV data.
Tracks: P&L, Drawdown, Win Rate, RR, Sharpe, Calmar.

Usage:
  python backtesting/backtest_engine.py --symbol EURUSD --strategy S02 --years 3
"""

import sys
import argparse
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import seaborn as sns
import os

from core.data_feed import DukascopyDataFeed
from strategies import (
    VolatilitySqueezeStrategy, SMCOrderBlockFVGStrategy,
    LiquiditySweepStrategy, GoldScalperStrategy, REMLiquidityStrategy
)
from strategies.base_strategy import Signal
from config.app_config import RISK, STRATEGY, SYMBOLS
from utils.logger import get_logger

log = get_logger("Backtest")

STRATEGY_MAP = {
    "S01": VolatilitySqueezeStrategy,
    "S02": SMCOrderBlockFVGStrategy,
    "S03": LiquiditySweepStrategy,
    "S04": GoldScalperStrategy,
    "S05": REMLiquidityStrategy,
}


@dataclass
class BacktestTrade:
    """Single simulated trade in backtest."""
    symbol:      str
    direction:   str
    entry:       float
    sl:          float
    tp:          float
    lot_size:    float
    open_idx:    int
    strategy:    str
    close_idx:   int = 0
    close_price: float = 0.0
    pnl:         float = 0.0
    sl_pips:     float = 0.0
    rr:          float = 0.0
    outcome:     str = "OPEN"  # "WIN" | "LOSS" | "BE" | "OPEN"


@dataclass
class BacktestResults:
    """Aggregated backtest statistics."""
    symbol:       str
    strategy:     str
    timeframe:    str
    total_trades: int
    wins:         int
    losses:       int
    breakevens:   int
    win_rate:     float
    avg_rr:       float
    total_pnl:    float
    max_drawdown: float
    max_dd_pct:   float
    sharpe_ratio: float
    calmar_ratio: float
    profit_factor: float
    largest_win:  float
    largest_loss: float
    trades:       List[BacktestTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "trades"}


class BacktestEngine:
    """
    Event-driven backtester. Runs bar-by-bar simulation.
    Uses realistic assumptions:
    - Market orders: fills at bar open of next bar (no look-ahead)
    - Slippage: 1 pip on entry (configurable)
    - Commission: 0 (prop firm covers)
    - Spread: from config (3 pips EURUSD etc.)
    """

    def __init__(self, symbol: str, timeframe: str = "M15",
                  initial_capital: float = 5000.0,
                  risk_pct: float = 1.0,
                  max_risk_dollar: float = 50.0,
                  slippage_pips: float = 1.0,
                  lookback_bars: int = 200):
        self.symbol        = symbol
        self.timeframe     = timeframe
        self.capital       = initial_capital
        self.risk_pct      = risk_pct
        self.max_risk_dollar = max_risk_dollar
        self.slippage_pips = slippage_pips
        self.lookback      = lookback_bars
        self.sym_config    = SYMBOLS.get(symbol, {})
        self.pip_size      = self.sym_config.get("pip_size", 0.0001)
        self.pip_val       = self.sym_config.get("pip_value_per_lot", 10.0)

    def run(self, df: pd.DataFrame, strategy_cls,
             strategy_name: str = "unknown",
             backtesting: bool = True) -> BacktestResults:
        """
        Run full backtest on OHLCV DataFrame.
        Requires at least lookback_bars + 100 rows.
        """
        log.info(f"Running {strategy_name} on {self.symbol} "
                 f"({len(df)} bars, {self.timeframe})...")

        strat  = strategy_cls(STRATEGY, SYMBOLS)
        trades: List[BacktestTrade] = []
        equity_curve: List[float]   = [self.capital]
        balance = self.capital

        # Tracking state
        open_trade: Optional[BacktestTrade] = None
        last_bar_date = None
        daily_trades_today = 0

        for i in range(self.lookback, len(df) - 1):
            if i % 5000 == 0:
                log.info(f"Progress: Processed {i}/{len(df)} bars ({(i/len(df)*100):.1f}%)")
                
            bar = df.iloc[i]
            next_bar = df.iloc[i + 1]

            # Day limit: max 2 trades per day
            bar_date = bar.name.date() if hasattr(bar.name, 'date') else None
            if bar_date != last_bar_date:
                daily_trades_today = 0
                last_bar_date = bar_date
                
            if daily_trades_today >= 2 and not open_trade:
                continue

            # ── Check if open trade hit SL or TP ─────────────────────────────
            if open_trade:
                close_price, outcome = self._check_exit(open_trade, bar)
                if outcome != "OPEN":
                    # Close the trade
                    open_trade.close_idx   = i
                    open_trade.close_price = close_price
                    open_trade.outcome     = outcome
                    sl_dist  = abs(open_trade.entry - open_trade.sl)
                    pnl      = self._calc_pnl(open_trade, close_price)
                    open_trade.pnl = pnl
                    balance += pnl
                    equity_curve.append(balance)
                    trades.append(open_trade)
                    open_trade = None
                    daily_trades_today += 1
                    log.debug(f"Trade closed: {outcome}, PnL=${pnl:.2f}, Balance=${balance:.2f}")
                    continue

            # ── Generate signal ───────────────────────────────────────────────
            if open_trade is None:
                window = df.iloc[i - self.lookback: i + 1]
                try:
                    # Inject backtest flag so strategies skip live session checks
                    signal = strat.analyze(window, self.symbol, self.timeframe,
                                           backtesting=backtesting)
                except TypeError:
                    # Older strategy signatures without backtesting param
                    signal = strat.analyze(window, self.symbol, self.timeframe)
                except Exception as e:
                    log.debug(f"Strategy error at bar {i}: {e}")
                    continue

                if signal and signal.valid:
                    lot = self._calc_lot(signal.entry, signal.sl, balance)
                    # Apply slippage to entry
                    slip = self.slippage_pips * self.pip_size
                    actual_entry = (signal.entry + slip if signal.direction == "BUY"
                                    else signal.entry - slip)
                    sl_pips = abs(actual_entry - signal.sl) / self.pip_size
                    rr = (abs(signal.tp - actual_entry) /
                           abs(actual_entry - signal.sl)
                           if abs(actual_entry - signal.sl) > 0 else 0)

                    open_trade = BacktestTrade(
                        symbol=self.symbol,
                        direction=signal.direction,
                        entry=actual_entry,
                        sl=signal.sl,
                        tp=signal.tp,
                        lot_size=lot,
                        open_idx=i,
                        strategy=signal.strategy,
                        sl_pips=sl_pips,
                        rr=rr
                    )

        # Close any remaining open trade at last bar
        if open_trade:
            last_close = df["close"].iloc[-1]
            open_trade.close_price = last_close
            open_trade.close_idx   = len(df) - 1
            open_trade.outcome     = "OPEN"
            open_trade.pnl         = self._calc_pnl(open_trade, last_close)
            trades.append(open_trade)

        return self._compute_results(trades, equity_curve, strategy_name)

    def _check_exit(self, trade: BacktestTrade, bar) -> tuple:
        """Check if bar hit SL or TP. Returns (exit_price, outcome)."""
        if trade.direction == "BUY":
            if bar["low"] <= trade.sl:
                return trade.sl, "LOSS"
            if bar["high"] >= trade.tp:
                return trade.tp, "WIN"
        else:
            if bar["high"] >= trade.sl:
                return trade.sl, "LOSS"
            if bar["low"] <= trade.tp:
                return trade.tp, "WIN"
        return 0.0, "OPEN"

    def _calc_lot(self, entry: float, sl: float, balance: float) -> float:
        """Calculate lot size from risk math."""
        sl_pips = abs(entry - sl) / self.pip_size
        if sl_pips <= 0:
            return 0.01
        risk_amount = min(balance * (self.risk_pct / 100), self.max_risk_dollar)
        lot = risk_amount / (sl_pips * self.pip_val)
        return max(0.01, min(round(lot, 2), 2.0))

    def _calc_pnl(self, trade: BacktestTrade, close_price: float) -> float:
        """Calculate realized PnL in USD."""
        price_diff = close_price - trade.entry
        if trade.direction == "SELL":
            price_diff = -price_diff
        pips = price_diff / self.pip_size
        return pips * self.pip_val * trade.lot_size

    def _compute_results(self, trades: List[BacktestTrade],
                          equity_curve: List[float],
                          strategy_name: str) -> BacktestResults:
        """Compute all performance statistics."""
        closed = [t for t in trades if t.outcome in ("WIN", "LOSS")]
        wins   = [t for t in closed if t.outcome == "WIN"]
        losses = [t for t in closed if t.outcome == "LOSS"]

        total_pnl    = sum(t.pnl for t in closed)
        win_rate     = len(wins) / max(len(closed), 1) * 100
        avg_rr       = np.mean([t.rr for t in closed]) if closed else 0

        # Drawdown
        equity_arr   = np.array(equity_curve)
        peak         = np.maximum.accumulate(equity_arr)
        drawdowns    = (peak - equity_arr) / np.maximum(peak, 1)
        max_dd_pct   = float(np.max(drawdowns)) * 100
        max_dd       = float(np.max(peak - equity_arr))

        # Sharpe Ratio (annualized, assuming M15 data)
        daily_returns = pd.Series(equity_curve).pct_change().dropna()
        sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                  if daily_returns.std() > 0 else 0)

        # Calmar
        calmar = ((total_pnl / self.capital) / (max_dd_pct / 100)
                   if max_dd_pct > 0 else 0)

        # Profit Factor
        gross_profit = sum(t.pnl for t in wins)
        gross_loss   = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return BacktestResults(
            symbol=self.symbol,
            strategy=strategy_name,
            timeframe=self.timeframe,
            total_trades=len(closed),
            wins=len(wins),
            losses=len(losses),
            breakevens=0,
            win_rate=win_rate,
            avg_rr=avg_rr,
            total_pnl=total_pnl,
            max_drawdown=max_dd,
            max_dd_pct=max_dd_pct,
            sharpe_ratio=float(sharpe),
            calmar_ratio=calmar,
            profit_factor=profit_factor,
            largest_win=max((t.pnl for t in wins), default=0),
            largest_loss=min((t.pnl for t in losses), default=0),
            trades=trades
        )


def print_results(r: BacktestResults):
    """Pretty-print backtest results."""
    print("\n" + "═" * 60)
    print(f"  BACKTEST RESULTS: {r.strategy} on {r.symbol} {r.timeframe}")
    print("═" * 60)
    print(f"  Total Trades:   {r.total_trades}")
    print(f"  Wins:           {r.wins} | Losses: {r.losses}")
    print(f"  Win Rate:       {r.win_rate:.1f}%")
    print(f"  Avg RR:         1:{r.avg_rr:.2f}")
    print(f"  Total PnL:      ${r.total_pnl:+.2f}")
    print(f"  Max Drawdown:   ${r.max_drawdown:.2f} ({r.max_dd_pct:.1f}%)")
    print(f"  Profit Factor:  {r.profit_factor:.2f}")
    print(f"  Sharpe Ratio:   {r.sharpe_ratio:.2f}")
    print(f"  Calmar Ratio:   {r.calmar_ratio:.2f}")
    print("═" * 60)

    if r.win_rate >= 55:
        print(f"  ✅ WIN RATE TARGET MET ({r.win_rate:.1f}% >= 55%)")
    else:
        print(f"  ⚠️  Win rate below 55% ({r.win_rate:.1f}%). Review parameters.")

    if r.max_dd_pct <= 10:
        print(f"  ✅ DRAWDOWN SAFE ({r.max_dd_pct:.1f}% <= 10%)")
    else:
        print(f"  🚨 DRAWDOWN TOO HIGH ({r.max_dd_pct:.1f}%) — Do NOT deploy!")
    print()

def plot_and_send_results(r: BacktestResults, initial_capital: float):
    """Plot backtest results and send via Telegram."""
    sns.set_theme(style="darkgrid")
    fig = plt.figure(figsize=(12, 8))
    fig.suptitle(f"Backtest: {r.strategy} on {r.symbol} ({r.timeframe})", fontsize=16)

    # 1. Equity Curve
    ax1 = plt.subplot(2, 2, (1, 2))
    equity = [initial_capital]
    for t in r.trades:
        if t.outcome in ["WIN", "LOSS"]:
            equity.append(equity[-1] + t.pnl)

    if len(equity) < 2:
        # No trades executed — inform clearly
        ax1.text(0.5, 0.5, f'NO TRADES GENERATED\n({r.total_trades} trades found)\n\nPossible causes:\n- Session filter blocked all signals\n- Insufficient lookback data\n- Strategy params too strict',
                 ha='center', va='center', transform=ax1.transAxes, fontsize=12,
                 color='#ff4454')
        ax1.set_title("Equity Curve — No Trades")
    else:
        ax1.plot(equity, color='#00d4aa', linewidth=2)
        ax1.set_title(f"Equity Curve ({len(equity)-1} closed trades)")
        ax1.set_ylabel("Balance ($)")
        ax1.axhline(y=initial_capital, color='#888', linestyle='--', alpha=0.5, label='Start')
        ax1.axhline(y=initial_capital - 225, color='#ff4454', linestyle=':', alpha=0.6, label='Kill-Switch')
        ax1.fill_between(range(len(equity)), equity, initial_capital,
                         where=[e > initial_capital for e in equity],
                         color='#00d4aa', alpha=0.1)
        ax1.fill_between(range(len(equity)), equity, initial_capital,
                         where=[e < initial_capital for e in equity],
                         color='#ff4454', alpha=0.1)
        ax1.legend(fontsize=8)

    # 2. Win/Loss Pie
    ax2 = plt.subplot(2, 2, 3)
    if r.total_trades > 0:
        ax2.pie([r.wins, r.losses], labels=["Wins", "Losses"], autopct='%1.1f%%',
                colors=['#00d4aa', '#ff4454'], startangle=90)
        ax2.set_title("Win/Loss Ratio")
    
    # 3. PnL Distribution
    ax3 = plt.subplot(2, 2, 4)
    pnls = [t.pnl for t in r.trades if t.outcome in ["WIN", "LOSS"]]
    if pnls:
        sns.histplot(pnls, bins=20, kde=True, ax=ax3, color='#00d4aa')
        ax3.set_title("Trade PnL Distribution")
        ax3.set_xlabel("PnL ($)")
    
    plt.tight_layout()

    # Save Image
    save_dir = Path(__file__).parent / "data"
    save_dir.mkdir(exist_ok=True)
    img_path = save_dir / f"backtest_{r.symbol}_{r.strategy}.png"
    plt.savefig(img_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"📊 Visual report saved to: {img_path}")

    # Also save CSV results for easy review
    csv_path = save_dir / f"backtest_{r.symbol}_{r.strategy}_trades.csv"
    if r.trades:
        trades_data = [{
            'open_idx': t.open_idx,
            'direction': t.direction,
            'entry': t.entry,
            'sl': t.sl,
            'tp': t.tp,
            'lot_size': t.lot_size,
            'close_price': t.close_price,
            'pnl': t.pnl,
            'outcome': t.outcome,
            'sl_pips': t.sl_pips,
            'rr': t.rr
        } for t in r.trades if t.outcome in ('WIN', 'LOSS')]
        if trades_data:
            pd.DataFrame(trades_data).to_csv(csv_path, index=False)
            print(f"📋 Trade log saved to:    {csv_path}")

    # Print absolute paths clearly
    print(f"\n📁 Results directory: {save_dir.resolve()}")

    # Send to Telegram
    try:
        from config.app_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        from telemetry.telegram_bot import TelegramNotifier
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            print("🚀 Sending report to Telegram...")
            tg = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
            tg.send_backtest_report(r, str(img_path))
    except Exception as e:
        print(f"⚠️ Could not send Telegram report: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradeGOD Backtester")
    parser.add_argument("--symbol",    default="EURUSD", help="Symbol to backtest")
    parser.add_argument("--strategy",  default="S02",    help="Strategy: S01-S05")
    parser.add_argument("--csv",       default="",       help="Path to Dukascopy CSV file")
    parser.add_argument("--timeframe", default="M15",    help="Timeframe")
    args = parser.parse_args()

    if not args.csv:
        print("ERROR: Provide --csv path to Dukascopy historical data file")
        print("Example: python backtesting/backtest_engine.py "
              "--symbol EURUSD --strategy S02 "
              "--csv backtesting/data/EURUSD_M15.csv")
        sys.exit(1)

    df = DukascopyDataFeed.load_csv(args.csv, args.symbol)
    if df.empty:
        print("ERROR: No data loaded from CSV")
        sys.exit(1)

    strat_cls = STRATEGY_MAP.get(args.strategy.upper())
    if strat_cls is None:
        print(f"ERROR: Unknown strategy {args.strategy}")
        sys.exit(1)

    engine = BacktestEngine(symbol=args.symbol, timeframe=args.timeframe)
    results = engine.run(df, strat_cls, args.strategy)
    print_results(results)
    plot_and_send_results(results, engine.capital)
