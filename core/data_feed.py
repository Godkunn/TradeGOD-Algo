"""
TradeGOD — MT5 Data Feed
Real-time and historical OHLCV data from MetaTrader 5 via Python API.
Handles connection, reconnection, and data normalization.
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, List, Dict
from utils.logger import get_logger
from utils.time_ops import now_utc

log = get_logger("DataFeed")

# Lazy import — MT5 might not be installed in backtest-only mode
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 package not installed. Live feed unavailable.")

# MT5 Timeframe constants mapping
TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  60,
    "H4":  240,
    "D1":  1440,
    "W1":  10080,
}


class MT5DataFeed:
    """
    Manages MT5 connection and provides OHLCV data.
    """

    def __init__(self, login: int, password: str, server: str, mt5_path: str = None):
        self.login    = login
        self.password = password
        self.server   = server
        self.mt5_path = mt5_path
        self._connected = False

    def connect(self) -> bool:
        """Initialize MT5 connection."""
        if not MT5_AVAILABLE:
            log.error("MetaTrader5 not installed. Cannot connect.")
            return False
        if self.mt5_path:
            init_success = mt5.initialize(path=self.mt5_path)
        else:
            init_success = mt5.initialize()
            
        if not init_success:
            log.error(f"MT5 init failed: {mt5.last_error()}")
            return False
        authorized = mt5.login(self.login, self.password, self.server)
        if not authorized:
            log.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False
        info = mt5.account_info()
        self._connected = True
        log.info(
            f"✅ MT5 Connected: {info.name} | Balance=${info.balance:.2f} "
            f"| Equity=${info.equity:.2f} | Server={info.server}"
        )
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False
            log.info("MT5 disconnected.")

    def reconnect(self) -> bool:
        """Attempt reconnection with retries."""
        for attempt in range(5):
            log.warning(f"Reconnecting MT5... attempt {attempt + 1}/5")
            self.disconnect()
            time.sleep(2 ** attempt)  # Exponential backoff
            if self.connect():
                return True
        log.critical("MT5 reconnection failed after 5 attempts.")
        return False

    # ══════════════════════════════════════════════════════════════════════════
    # OHLCV DATA
    # ══════════════════════════════════════════════════════════════════════════

    def get_ohlcv(self, symbol: str, timeframe: str,
                   count: int = 500) -> Optional[pd.DataFrame]:
        """
        Fetch recent OHLCV bars.
        Returns DataFrame with columns: time, open, high, low, close, tick_volume.
        """
        if not self._connected:
            log.error("Not connected to MT5.")
            return None

        tf_const = self._get_tf_const(timeframe)
        if tf_const is None:
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            log.warning(f"No data for {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df = df[["open", "high", "low", "close", "tick_volume"]].copy()
        df.columns = ["open", "high", "low", "close", "tick_volume"]
        return df

    def get_current_tick(self, symbol: str) -> Optional[dict]:
        """Get latest bid/ask tick for a symbol."""
        if not self._connected:
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "spread_pips": (tick.ask - tick.bid) / self._get_pip_size(symbol),
            "time": datetime.fromtimestamp(tick.time, tz=timezone.utc),
        }

    def get_account_info(self) -> Optional[dict]:
        """Return current account state."""
        if not MT5_AVAILABLE or not self._connected:
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return {
            "balance":  info.balance,
            "equity":   info.equity,
            "margin":   info.margin,
            "free_margin": info.margin_free,
            "profit":   info.profit,
            "leverage": info.leverage,
            "currency": info.currency,
        }

    def get_open_positions(self, magic: Optional[int] = None) -> List[dict]:
        """Get all open positions, optionally filtered by magic number."""
        if not MT5_AVAILABLE or not self._connected:
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        result = []
        for pos in positions:
            if magic and pos.magic != magic:
                continue
            result.append({
                "ticket":    pos.ticket,
                "symbol":    pos.symbol,
                "type":      "BUY" if pos.type == 0 else "SELL",
                "lot":       pos.volume,
                "entry":     pos.price_open,
                "sl":        pos.sl,
                "tp":        pos.tp,
                "pnl":       pos.profit,
                "open_time": datetime.fromtimestamp(pos.time, tz=timezone.utc),
                "magic":     pos.magic,
                "comment":   pos.comment,
            })
        return result

    def get_spread_pips(self, symbol: str) -> float:
        """Return current spread in pips."""
        tick = self.get_current_tick(symbol)
        return tick["spread_pips"] if tick else 999.0

    def _get_tf_const(self, timeframe: str):
        """Map string timeframe to MT5 constant."""
        if not MT5_AVAILABLE:
            return None
        tf_consts = {
            "M1":  mt5.TIMEFRAME_M1,
            "M5":  mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1":  mt5.TIMEFRAME_H1,
            "H4":  mt5.TIMEFRAME_H4,
            "D1":  mt5.TIMEFRAME_D1,
        }
        const = tf_consts.get(timeframe.upper())
        if const is None:
            log.error(f"Unknown timeframe: {timeframe}")
        return const

    @staticmethod
    def _get_pip_size(symbol: str) -> float:
        """Return pip size for a given symbol."""
        jpy_pairs = ["JPY", "XAU", "XAG"]
        if any(x in symbol.upper() for x in jpy_pairs):
            return 0.01
        return 0.0001


class DukascopyDataFeed:
    """
    Offline data feed from Dukascopy CSV exports.
    Use for backtesting when MT5 data is insufficient.

    Dukascopy export format: Date,Time,Open,High,Low,Close,Volume
    Download from: https://www.dukascopy.com/swiss/english/marketwatch/historical/
    """

    @staticmethod
    def load_csv(filepath: str, symbol: str = "",
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None) -> pd.DataFrame:
        """
        Load and normalize Dukascopy historical CSV.
        Returns standard DataFrame: time(index), open, high, low, close, tick_volume.
        """
        df = pd.read_csv(
            filepath,
            parse_dates=[["Date", "Time"]] if "Date" in pd.read_csv(filepath, nrows=0).columns else True,
            dayfirst=False,
        )
        df.columns = [c.strip().lower() for c in df.columns]

        # Rename columns to standard names
        rename_map = {
            "open":   "open",
            "high":   "high",
            "low":    "low",
            "close":  "close",
            "volume": "tick_volume",
        }
        df.rename(columns=rename_map, inplace=True)
        df.set_index("time", inplace=True)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["open", "high", "low", "close", "tick_volume"]].copy()

        # Date filtering
        if start_date:
            df = df[df.index >= pd.Timestamp(start_date, tz="UTC")]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date, tz="UTC")]

        # Drop duplicates & sort
        df = df[~df.index.duplicated(keep="first")].sort_index()
        df.dropna(inplace=True)

        log.info(f"Loaded {len(df)} bars from Dukascopy CSV ({filepath}). Symbol={symbol}")
        return df

    @staticmethod
    def download_instructions():
        """Print download instructions for Dukascopy data."""
        print("""
        ╔════════════════════════════════════════════════════════════╗
        ║        Dukascopy Historical Data Download Guide            ║
        ╠════════════════════════════════════════════════════════════╣
        ║  1. Go to: https://www.dukascopy.com/swiss/english/        ║
        ║             marketwatch/historical/                         ║
        ║  2. Select instrument (e.g., EUR/USD)                      ║
        ║  3. Select timeframe: 1-Hour (H1) or 15-Minutes (M15)     ║
        ║  4. Select date range: 5 years recommended                  ║
        ║  5. Download as CSV                                         ║
        ║  6. Place in: backtesting/data/{SYMBOL}_{TF}.csv           ║
        ║                                                             ║
        ║  Alternatively, use the MT5 data downloader:               ║
        ║  python backtesting/data_downloader.py                      ║
        ╚════════════════════════════════════════════════════════════╝
        """)
