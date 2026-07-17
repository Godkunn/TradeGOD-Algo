"""
TradeGOD — Strategy S06: Asymmetric Risk Controller
Switches risk mode based on account equity. Wraps signals from S01-S05.

DEFENSIVE: balance < starting_capital → 0.5% risk, 1:1 RR
NEUTRAL:   balance ≈ starting_capital → 1.0% risk, 1:2 RR
AGGRESSIVE: balance > starting_capital by 2%+ → 1.5% risk, 1:2 RR
"""

import pandas as pd
from typing import Optional
from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import get_logger

log = get_logger("S06_Asymmetric")


class AsymmetricRiskStrategy(BaseStrategy):
    """
    Module S06: Asymmetric Risk Controller
    Does NOT generate signals. Modifies incoming signals' TP
    and provides risk% for RiskManager.
    """

    def __init__(self, config: dict, symbols_config: dict,
                  starting_capital: float = 5000.0):
        super().__init__("S06_Asymmetric", config, symbols_config)
        p = config["asymmetric_risk"]
        self.def_risk_pct  = p["defensive_risk_pct"]   # 0.5%
        self.agg_risk_pct  = p["aggressive_risk_pct"]  # 1.5%
        self.def_rr        = p["defensive_rr"]          # 1.0
        self.agg_rr        = p["aggressive_rr"]         # 1.5
        self.starting_cap  = starting_capital           # $5000
        self._mode         = "NEUTRAL"

    def analyze(self, df: pd.DataFrame, symbol: str,
                 timeframe: str = "ANY") -> Optional[Signal]:
        return None  # Signal-less strategy

    def get_risk_mode(self, current_balance: float) -> str:
        """Returns DEFENSIVE / NEUTRAL / AGGRESSIVE."""
        if current_balance < self.starting_cap * 0.99:
            self._mode = "DEFENSIVE"
        elif current_balance > self.starting_cap * 1.02:
            self._mode = "AGGRESSIVE"
        else:
            self._mode = "NEUTRAL"
        return self._mode

    def apply_risk_mode(self, signal: Signal,
                         current_balance: float) -> Signal:
        """
        Adjusts signal TP based on equity mode.
        Lot size adjustment is done by RiskManager using risk%.
        """
        mode    = self.get_risk_mode(current_balance)
        sl_dist = abs(signal.entry - signal.sl)

        if mode == "DEFENSIVE":
            # Tighten TP to 1:1 RR
            if signal.direction == "BUY":
                signal.tp = signal.entry + sl_dist * self.def_rr
            else:
                signal.tp = signal.entry - sl_dist * self.def_rr
            signal.comment += "|DEFENSIVE"
            log.info(f"🛡️ DEFENSIVE mode: TP→1:1 for {signal.symbol}")

        elif mode == "AGGRESSIVE":
            # Keep full RR from strategy (1:2 default)
            signal.comment += "|AGGRESSIVE"
            log.info(f"⚡ AGGRESSIVE mode for {signal.symbol}")

        else:
            signal.comment += "|NEUTRAL"

        return signal

    def get_risk_pct_for_mode(self, current_balance: float) -> float:
        """Returns risk% for current mode."""
        mode = self.get_risk_mode(current_balance)
        return {
            "DEFENSIVE":  self.def_risk_pct,  # 0.5%
            "NEUTRAL":    1.0,
            "AGGRESSIVE": self.agg_risk_pct,  # 1.5%
        }.get(mode, 1.0)
