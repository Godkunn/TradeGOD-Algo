"""
TradeGOD — Strategy S04: Gold Scalper (XAUUSD)
Direct S/R zone touch, 1:1 RR scalp with pin bar confirmation.

Asset: XAUUSD only
Session: Asian + NY (block London noise)
RR: 1:1 scalp / 1:1.5 extended
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.smc_logic import detect_supply_demand_zones
from utils.indicators import atr, rsi, is_pin_bar, ema_200
from utils.time_ops import get_current_session


class GoldScalperStrategy(BaseStrategy):
    """
    Module S04: Gold Scalper
    1. Find key S&D zones on M15 (past 20 bars)
    2. Wait for price to touch zone proximal line
    3. Confirm with pin bar OR RSI extreme
    4. SL: zone distal + 1 ATR buffer
    5. TP: 1:1 RR (scalp mode)
    """

    SYMBOL = "XAUUSD"

    def __init__(self, config: dict, symbols_config: dict):
        super().__init__("S04_GoldScalp", config, symbols_config)
        p = config["gold_scalper"]
        self.zone_lookback = p["zone_lookback_bars"]         # 20
        self.touch_tol     = p["zone_touch_tolerance_pips"]  # 5.0
        self.rr_scalp      = p["direct_touch_rr"]            # 1.0
        self.rr_extended   = p["fallback_rr"]                # 1.5
        self.atr_period    = p["atr_period"]                  # 14

    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD",
                 timeframe: str = "M15") -> Optional[Signal]:
        if not self._active or symbol.upper() != self.SYMBOL:
            return None
        if len(df) < 30:
            return None

        # Block London session for Gold
        if get_current_session() == "LONDON":
            return None

        ps      = self.pip_size(symbol)   # 0.1 for Gold
        tol     = self.touch_tol * ps
        _atr    = atr(df, self.atr_period)
        atr_val = _atr.iloc[-1]
        rsi_val = rsi(df).iloc[-1]
        ema200  = ema_200(df).iloc[-1]
        price   = df["close"].iloc[-1]

        zones = detect_supply_demand_zones(
            df.iloc[-self.zone_lookback:], pip_size=ps
        )

        for zone in zones:
            if not zone.fresh:
                continue

            # ── Demand Zone BUY ───────────────────────────────────────────────
            if zone.kind == "DEMAND":
                at_zone  = abs(price - zone.proximal) <= tol
                oversold = rsi_val < 40
                pin      = is_pin_bar(df).iloc[-1] == 1

                if at_zone and (oversold or pin):
                    entry   = price
                    sl      = zone.distal - atr_val
                    rr      = self.rr_scalp if pin else self.rr_extended
                    tp      = self.get_tp_from_rr(entry, sl, rr, "BUY")
                    sl_pips = self.get_sl_pips(entry, sl, symbol)

                    if sl_pips < 5 or sl_pips > 150:
                        continue

                    zone.fresh       = False
                    zone.touch_count += 1
                    self.log.info(
                        f"🥇 S04 BUY Gold @ {entry:.2f} "
                        f"SL={sl:.2f} TP={tp:.2f} | RSI={rsi_val:.0f}"
                    )
                    return Signal(
                        symbol=symbol, direction="BUY",
                        entry=entry, sl=sl, tp=tp,
                        strategy="S04_GoldScalp", priority=3,
                        comment=f"DemandZone|RSI={rsi_val:.0f}"
                    )

            # ── Supply Zone SELL ──────────────────────────────────────────────
            if zone.kind == "SUPPLY":
                at_zone    = abs(price - zone.proximal) <= tol
                overbought = rsi_val > 60
                pin        = is_pin_bar(df).iloc[-1] == -1

                if at_zone and (overbought or pin):
                    entry   = price
                    sl      = zone.distal + atr_val
                    rr      = self.rr_scalp if pin else self.rr_extended
                    tp      = self.get_tp_from_rr(entry, sl, rr, "SELL")
                    sl_pips = self.get_sl_pips(entry, sl, symbol)

                    if sl_pips < 5 or sl_pips > 150:
                        continue

                    zone.fresh       = False
                    zone.touch_count += 1
                    self.log.info(
                        f"🥇 S04 SELL Gold @ {entry:.2f} "
                        f"SL={sl:.2f} TP={tp:.2f} | RSI={rsi_val:.0f}"
                    )
                    return Signal(
                        symbol=symbol, direction="SELL",
                        entry=entry, sl=sl, tp=tp,
                        strategy="S04_GoldScalp", priority=3,
                        comment=f"SupplyZone|RSI={rsi_val:.0f}"
                    )

        return None
