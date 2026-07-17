"""
TradeGOD — Strategy S03: Liquidity Sweep + Reversal
Detects Equal Highs/Lows stop hunts, enters on wick rejection.

Session: NY only
Timeframe: M15/M5
RR: 1:2
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.smc_logic import detect_equal_highs_lows
from utils.indicators import atr, upper_wick, lower_wick, candle_body
from utils.time_ops import get_current_session


class LiquiditySweepStrategy(BaseStrategy):
    """
    Module S03: Liquidity Sweep Sniper
    1. Mark Equal Highs/Lows (retail S/R)
    2. Wait for wick to pierce level (stop hunt)
    3. Body closes BACK INSIDE level (rejection)
    4. Enter on next candle open
    5. SL: beyond sweep wick + ATR buffer
    6. TP: 2x SL distance
    """

    def __init__(self, config: dict, symbols_config: dict):
        super().__init__("S03_LiqSweep", config, symbols_config)
        p = config["liquidity_sweep"]
        self.lookback       = p["lookback_bars"]         # 50
        self.sweep_tol_pips = p["sweep_tolerance_pips"]  # 3.0
        self.rr_target      = p["target_rr"]             # 2.0

    def analyze(self, df: pd.DataFrame, symbol: str,
                 timeframe: str = "M15",
                 backtesting: bool = False) -> Optional[Signal]:
        if not self._active or len(df) < self.lookback + 5:
            return None

        # S03 is NY session only (skip in backtest mode)
        if not backtesting and get_current_session() not in ["NY"]:
            return None

        ps      = self.pip_size(symbol)
        tol     = self.sweep_tol_pips * ps
        _atr    = atr(df)
        atr_val = _atr.iloc[-1]

        scan_df = df.iloc[-self.lookback:]
        eq      = detect_equal_highs_lows(scan_df, self.sweep_tol_pips, ps)

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        # ── Bullish sweep (wick below EQL, close above) ───────────────────────
        for eql_price, _, __ in eq.get("EQL", []):
            swept_below  = prev["low"] < eql_price - tol
            closed_above = prev["close"] > eql_price
            big_wick     = lower_wick(df).iloc[-2] > candle_body(df).iloc[-2]

            if swept_below and closed_above and big_wick:
                entry   = curr["open"]
                sl      = prev["low"] - atr_val * 0.5
                tp      = self.get_tp_from_rr(entry, sl, self.rr_target, "BUY")
                sl_pips = self.get_sl_pips(entry, sl, symbol)

                if sl_pips < 2:
                    continue

                self.log.info(f"🌊 S03 BUY sweep {symbol} @ {entry:.5f} EQL={eql_price:.5f}")
                return Signal(
                    symbol=symbol, direction="BUY",
                    entry=entry, sl=sl, tp=tp,
                    strategy="S03_LiqSweep", priority=2,
                    comment=f"EQL_sweep@{eql_price:.5f}"
                )

        # ── Bearish sweep (wick above EQH, close below) ───────────────────────
        for eqh_price, _, __ in eq.get("EQH", []):
            swept_above  = prev["high"] > eqh_price + tol
            closed_below = prev["close"] < eqh_price
            big_wick     = upper_wick(df).iloc[-2] > candle_body(df).iloc[-2]

            if swept_above and closed_below and big_wick:
                entry   = curr["open"]
                sl      = prev["high"] + atr_val * 0.5
                tp      = self.get_tp_from_rr(entry, sl, self.rr_target, "SELL")
                sl_pips = self.get_sl_pips(entry, sl, symbol)

                if sl_pips < 2:
                    continue

                self.log.info(f"🌊 S03 SELL sweep {symbol} @ {entry:.5f} EQH={eqh_price:.5f}")
                return Signal(
                    symbol=symbol, direction="SELL",
                    entry=entry, sl=sl, tp=tp,
                    strategy="S03_LiqSweep", priority=2,
                    comment=f"EQH_sweep@{eqh_price:.5f}"
                )

        return None
