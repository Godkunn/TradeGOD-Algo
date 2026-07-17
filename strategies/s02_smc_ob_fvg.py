"""
TradeGOD — Strategy S02: SMC Order Block + FVG Hunter
Full SMC stack: HTF BOS → IDM Sweep → OB/FVG/BPR → LTF CHoCH Entry

Priority: BPR (1) > Breaker Block/OB+FVG (2) > S&D Zone (3)
Session: London + NY
RR: 1:2 to 1:3
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.smc_logic import (
    detect_swing_points, detect_market_structure,
    detect_fvg, detect_order_blocks, detect_bpr,
    detect_supply_demand_zones, detect_idm, is_idm_swept,
    update_fvg_mitigation, update_ob_mitigation,
    evaluate_smc_setup
)
from utils.indicators import atr
from utils.time_ops import get_current_session


class SMCOrderBlockFVGStrategy(BaseStrategy):
    """
    Module S02: SMC Full Stack
    State Machine:
      1. IDLE: Scan M15 for BOS/CHoCH
      2. IDM_WAIT: Wait for Inducement sweep
      3. ZONE_ENTRY: Price approaching OB/BPR/S&D
      4. EXECUTE: Fire limit order at zone
    """

    def __init__(self, config: dict, symbols_config: dict):
        super().__init__("S02_SMC", config, symbols_config)
        p = config["smc_ob_fvg"]
        self.fractal_lookback = p["fractal_lookback"]           # 5
        self.fvg_min_gap      = p["fvg_min_gap_pips"]           # 2.0
        self.ob_min_score     = p["ob_scoring_min_score"]        # 8
        self.sl_atr_buffer    = p["sl_atr_buffer_multiplier"]   # 1.5
        self.rr_target        = p["rr_target"]                   # 2.0

    def analyze(self, df: pd.DataFrame, symbol: str,
                 timeframe: str = "M15",
                 backtesting: bool = False) -> Optional[Signal]:
        if not self._active or len(df) < 50:
            return None

        # S02 runs London + NY only (skip check in backtest mode)
        if not backtesting and get_current_session() not in ["LONDON", "NY"]:
            return None

        ps      = self.pip_size(symbol)
        _atr    = atr(df)
        atr_val = _atr.iloc[-1]

        # ── Full SMC analysis ─────────────────────────────────────────────────
        swings   = detect_swing_points(df, self.fractal_lookback)
        struct   = detect_market_structure(df, swings)
        fvgs     = detect_fvg(df, self.fvg_min_gap, ps)
        obs      = detect_order_blocks(df, fvgs, swings, ps)
        bprs     = detect_bpr(fvgs)
        sd_zones = detect_supply_demand_zones(df, pip_size=ps)

        current_price = df["close"].iloc[-1]
        current_high  = df["high"].iloc[-1]
        current_low   = df["low"].iloc[-1]
        trend         = struct["trend"].iloc[-1]

        if trend == "NEUTRAL":
            return None

        # ── IDM Sweep check ───────────────────────────────────────────────────
        idm_level = detect_idm(swings, trend)
        if idm_level and not is_idm_swept(idm_level, current_price, trend):
            return None

        # ── Update mitigation state ───────────────────────────────────────────
        fvgs = update_fvg_mitigation(fvgs, current_high, current_low)
        obs  = update_ob_mitigation(obs, current_high, current_low)

        # ── Equilibrium ───────────────────────────────────────────────────────
        h_prices = [s.price for s in swings if s.kind == "HIGH"]
        l_prices = [s.price for s in swings if s.kind == "LOW"]
        if not h_prices or not l_prices:
            return None
        swing_high  = max(h_prices[-3:]) if len(h_prices) >= 3 else max(h_prices)
        swing_low   = min(l_prices[-3:]) if len(l_prices) >= 3 else min(l_prices)
        equilibrium = (swing_high + swing_low) / 2

        # ── Evaluate priority setup ───────────────────────────────────────────
        setup = evaluate_smc_setup(
            current_price=current_price,
            current_high=current_high,
            current_low=current_low,
            trend=trend,
            fvgs=fvgs, obs=obs, bprs=bprs, sd_zones=sd_zones,
            idm_level=idm_level, equilibrium=equilibrium
        )

        if setup is None:
            return None

        # ── Build Signal ──────────────────────────────────────────────────────
        direction = "BUY" if "BUY" in setup["type"] else "SELL"
        entry     = setup["entry"]
        sl_raw    = setup["sl"]

        # ATR buffer on SL
        if direction == "BUY":
            sl = sl_raw - atr_val * self.sl_atr_buffer
        else:
            sl = sl_raw + atr_val * self.sl_atr_buffer

        tp      = self.get_tp_from_rr(entry, sl, self.rr_target, direction)
        sl_pips = self.get_sl_pips(entry, sl, symbol)

        # RR check
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr_actual = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr_actual < 1.5:
            return None

        self.log.info(
            f"🎯 S02 {direction} {symbol} @ {entry:.5f} "
            f"SL={sl:.5f} TP={tp:.5f} | {setup['source']} P={setup.get('priority',3)}"
        )
        return Signal(
            symbol=symbol, direction=direction,
            entry=entry, sl=sl, tp=tp,
            strategy="S02_SMC",
            priority=setup.get("priority", 3),
            score=setup.get("score", 7),
            comment=f"{setup['type']}|{setup['source']}"
        )
