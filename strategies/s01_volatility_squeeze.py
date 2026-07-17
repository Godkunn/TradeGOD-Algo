"""
TradeGOD — Strategy S01: Volatility Squeeze
Bollinger Bands vs Keltner Channels squeeze breakout.

Session: London Open + NY Open killzones
Timeframe: H1 signal, M15 execution  
RR: 1:2 minimum
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.indicators import (
    squeeze_detector, atr, ema_200, volume_above_average,
    is_bullish_candle, is_bearish_candle, anti_fomo_filter
)
from utils.time_ops import is_london_killzone, is_ny_killzone


class VolatilitySqueezeStrategy(BaseStrategy):
    """
    Module S01: The Volatility Squeeze
    State Machine:
    1. Detect BB inside KC (squeeze = True) for >= min_squeeze_bars
    2. Squeeze releases (squeeze flips False)
    3. Confirm breakout: ERC candle + volume above average
    4. 200 EMA trend filter
    5. Entry at close. SL at opposite KC. TP at 2x SL distance.
    """

    def __init__(self, config: dict, symbols_config: dict):
        super().__init__("S01_Squeeze", config, symbols_config)
        p = config["volatility_squeeze"]
        self.bb_period   = p["bb_period"]           # 20
        self.bb_mult     = p["bb_multiplier"]        # 2.0
        self.kc_period   = p["kc_period"]            # 20
        self.kc_mult     = p["kc_atr_multiplier"]    # 1.5
        self.min_squeeze = p["min_squeeze_bars"]      # 3
        self.rr_target   = 2.0
        self._squeeze_count = {}

    def analyze(self, df: pd.DataFrame, symbol: str,
                 timeframe: str = "H1") -> Optional[Signal]:
        if not self._active or len(df) < 30:
            return None

        # Only trade in killzones
        if not (is_london_killzone() or is_ny_killzone()):
            return None

        squeeze = squeeze_detector(df, self.bb_period, self.bb_mult,
                                    self.kc_period, self.kc_mult)
        _atr   = atr(df)
        ema200 = ema_200(df)

        curr_sq = squeeze.iloc[-1]
        key     = symbol

        if curr_sq:
            self._squeeze_count[key] = self._squeeze_count.get(key, 0) + 1
            return None  # Still in squeeze

        squeeze_bars = self._squeeze_count.pop(key, 0)
        if squeeze_bars < self.min_squeeze:
            return None  # Squeeze too short — unreliable

        current = df.iloc[-1]
        prev    = df.iloc[-2]
        atr_val = _atr.iloc[-1]
        e200    = ema200.iloc[-1]

        # Anti-FOMO filter
        if not anti_fomo_filter(df).iloc[-1]:
            return None

        # Volume confirmation
        if not volume_above_average(df).iloc[-1]:
            return None

        # ── Bullish breakout ──────────────────────────────────────────────────
        if (is_bullish_candle(df).iloc[-1] and
            current["close"] > prev["high"] and
            current["close"] > e200):

            entry   = current["close"]
            sl      = current["low"] - atr_val
            tp      = self.get_tp_from_rr(entry, sl, self.rr_target, "BUY")
            sl_pips = self.get_sl_pips(entry, sl, symbol)

            if sl_pips < 3:
                return None

            self.log.info(
                f"🔥 S01 BUY {symbol} @ {entry:.5f} | "
                f"SL={sl:.5f} TP={tp:.5f} | Squeeze={squeeze_bars}bars"
            )
            return Signal(
                symbol=symbol, direction="BUY",
                entry=entry, sl=sl, tp=tp,
                strategy="S01_Squeeze", priority=2, score=7,
                comment=f"Squeeze_{squeeze_bars}bars"
            )

        # ── Bearish breakout ──────────────────────────────────────────────────
        if (is_bearish_candle(df).iloc[-1] and
            current["close"] < prev["low"] and
            current["close"] < e200):

            entry   = current["close"]
            sl      = current["high"] + atr_val
            tp      = self.get_tp_from_rr(entry, sl, self.rr_target, "SELL")
            sl_pips = self.get_sl_pips(entry, sl, symbol)

            if sl_pips < 3:
                return None

            self.log.info(
                f"🔥 S01 SELL {symbol} @ {entry:.5f} | "
                f"SL={sl:.5f} TP={tp:.5f} | Squeeze={squeeze_bars}bars"
            )
            return Signal(
                symbol=symbol, direction="SELL",
                entry=entry, sl=sl, tp=tp,
                strategy="S01_Squeeze", priority=2, score=7,
                comment=f"Squeeze_{squeeze_bars}bars"
            )

        return None
