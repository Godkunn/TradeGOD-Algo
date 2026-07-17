"""
TradeGOD — Base Strategy Abstract Class
All strategies inherit from this.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import pandas as pd
from utils.logger import get_logger


@dataclass
class Signal:
    """Trade signal output from a strategy."""
    symbol:     str
    direction:  str          # "BUY" or "SELL"
    entry:      float
    sl:         float
    tp:         float
    lot_size:   float = 0.0  # Filled by RiskManager
    strategy:   str = ""
    priority:   int = 3      # 1=highest (BPR), 2=OB, 3=SD
    score:      int = 0
    rr:         float = 0.0
    comment:    str = ""
    valid:      bool = True

    def __post_init__(self):
        if self.entry <= 0 or self.sl <= 0:
            self.valid = False
        if self.direction == "BUY":
            if self.sl >= self.entry:
                self.valid = False
            if self.tp > 0 and self.entry != self.sl:
                self.rr = (self.tp - self.entry) / (self.entry - self.sl)
        else:
            if self.sl <= self.entry:
                self.valid = False
            if self.tp > 0 and self.sl != self.entry:
                self.rr = (self.entry - self.tp) / (self.sl - self.entry)


class BaseStrategy(ABC):
    """Abstract base for all TradeGOD strategy modules."""

    def __init__(self, name: str, config: dict, symbols_config: dict):
        self.name    = name
        self.config  = config
        self.symbols = symbols_config
        self.log     = get_logger(f"Strategy.{name}")
        self._active = True

    @abstractmethod
    def analyze(self, df: pd.DataFrame, symbol: str,
                 timeframe: str) -> Optional[Signal]:
        """Core analysis. Called on each new bar. Returns Signal or None."""
        ...

    def pip_value(self, symbol: str) -> float:
        return self.symbols.get(symbol, {}).get("pip_value_per_lot", 10.0)

    def pip_size(self, symbol: str) -> float:
        return self.symbols.get(symbol, {}).get("pip_size", 0.0001)

    def max_spread(self, symbol: str) -> float:
        return self.symbols.get(symbol, {}).get("max_spread_pips", 3.0)

    def get_sl_pips(self, entry: float, sl: float, symbol: str) -> float:
        ps = self.pip_size(symbol)
        return abs(entry - sl) / ps if ps > 0 else 0

    def get_tp_from_rr(self, entry: float, sl: float,
                        rr: float, direction: str) -> float:
        sl_distance = abs(entry - sl)
        if direction == "BUY":
            return entry + sl_distance * rr
        return entry - sl_distance * rr

    @property
    def is_active(self) -> bool:
        return self._active

    def enable(self): self._active = True
    def disable(self): self._active = False
