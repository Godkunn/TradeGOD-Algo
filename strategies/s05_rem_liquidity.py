"""
TradeGOD — Strategy S05: REM Liquidity Bot
HTF (H4) liquidity sweep + M15 FVG entry model.

Assets: NZDUSD, AUDUSD, USOUSD
Days: Tuesday, Wednesday, Thursday ONLY
Session: London + NY
RR: 1:2
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.smc_logic import detect_swing_points, detect_fvg
from utils.indicators import atr
from utils.time_ops import now_utc


class REMLiquidityStrategy(BaseStrategy):
    """
    Module S05: REM Liquidity Bot
    1. H4: identify major swing highs/lows (external liquidity)
    2. Price sweeps H4 level (wick past, body closes back inside)
    3. M15: FVG forms in reversal direction
    4. Entry at FVG proximal
    5. SL: beyond sweep candle + ATR buffer
    6. TP: 1:2 RR
    7. Only Tue/Wed/Thu (blocked Mon/Fri)
    """

    ALLOWED_WEEKDAYS = {1, 2, 3}  # 1=Tue, 2=Wed, 3=Thu (0=Mon)
    ALLOWED_SYMBOLS  = {"NZDUSD", "AUDUSD", "USOUSD"}

    def __init__(self, config: dict, symbols_config: dict):
        super().__init__("S05_REM", config, symbols_config)
        p = config["rem_liquidity"]
        self.sl_buffer_pips = p["sl_buffer_pips"]  # 5.0
        self.rr_target      = p["rr_target"]        # 2.0

    def analyze(self, df_m15: pd.DataFrame, symbol: str,
                 timeframe: str = "M15",
                 df_h4: Optional[pd.DataFrame] = None) -> Optional[Signal]:
        if not self._active:
            return None
        if symbol.upper() not in self.ALLOWED_SYMBOLS:
            return None
        if len(df_m15) < 50:
            return None

        # Day filter
        if now_utc().weekday() not in self.ALLOWED_WEEKDAYS:
            return None

        ps      = self.pip_size(symbol)
        sl_buf  = self.sl_buffer_pips * ps
        _atr    = atr(df_m15)
        atr_val = _atr.iloc[-1]

        # Approximate H4 from M15 if not provided (every 16 M15 bars ≈ 1 H4 bar)
        htf = df_h4 if df_h4 is not None else df_m15.iloc[::16]
        if len(htf) < 5:
            return None

        h4_swings = detect_swing_points(htf, lookback=3)
        if not h4_swings:
            return None

        h4_highs = [s.price for s in h4_swings if s.kind == "HIGH"]
        h4_lows  = [s.price for s in h4_swings if s.kind == "LOW"]
        if not h4_highs or not h4_lows:
            return None

        last_h4_high = max(h4_highs[-3:] if len(h4_highs) >= 3 else h4_highs)
        last_h4_low  = min(h4_lows[-3:]  if len(h4_lows) >= 3  else h4_lows)

        prev = df_m15.iloc[-2]
        curr = df_m15.iloc[-1]

        # ── Bearish: Swept H4 High ────────────────────────────────────────────
        swept_high = (prev["high"] > last_h4_high and   # Wick above H4 high
                      prev["close"] < last_h4_high)     # Body closed below

        if swept_high:
            fvgs = detect_fvg(df_m15.iloc[-20:], 1.5, ps)
            b_fvgs = [f for f in fvgs if f.direction == "BEARISH"]
            if b_fvgs:
                fvg   = b_fvgs[-1]
                entry = fvg.top
                sl    = prev["high"] + sl_buf + atr_val
                tp    = self.get_tp_from_rr(entry, sl, self.rr_target, "SELL")
                sl_pips = self.get_sl_pips(entry, sl, symbol)
                if sl_pips > 3:
                    self.log.info(f"🔴 S05 SELL {symbol} H4 sweep @ {last_h4_high:.5f}")
                    return Signal(
                        symbol=symbol, direction="SELL",
                        entry=entry, sl=sl, tp=tp,
                        strategy="S05_REM", priority=2,
                        comment=f"H4_sweep_high|FVG"
                    )

        # ── Bullish: Swept H4 Low ─────────────────────────────────────────────
        swept_low = (prev["low"] < last_h4_low and      # Wick below H4 low
                     prev["close"] > last_h4_low)       # Body closed above

        if swept_low:
            fvgs = detect_fvg(df_m15.iloc[-20:], 1.5, ps)
            bull_fvgs = [f for f in fvgs if f.direction == "BULLISH"]
            if bull_fvgs:
                fvg   = bull_fvgs[-1]
                entry = fvg.bottom
                sl    = prev["low"] - sl_buf - atr_val
                tp    = self.get_tp_from_rr(entry, sl, self.rr_target, "BUY")
                sl_pips = self.get_sl_pips(entry, sl, symbol)
                if sl_pips > 3:
                    self.log.info(f"🟢 S05 BUY {symbol} H4 sweep @ {last_h4_low:.5f}")
                    return Signal(
                        symbol=symbol, direction="BUY",
                        entry=entry, sl=sl, tp=tp,
                        strategy="S05_REM", priority=2,
                        comment=f"H4_sweep_low|FVG"
                    )

        return None
