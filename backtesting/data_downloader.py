"""
TradeGOD — MT5 Data Downloader
Downloads historical OHLCV data directly from MetaTrader 5
for backtesting. Saves as CSV in backtesting/data/.

Run: python backtesting/data_downloader.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from datetime import datetime, timezone
from config.app_config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from core.data_feed import MT5DataFeed
from utils.logger import get_logger

log = get_logger("DataDownloader")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SYMBOLS_TO_DOWNLOAD = ["EURUSD", "GBPUSD", "XAUUSD", "NZDUSD", "AUDUSD", "USDJPY"]
TIMEFRAMES          = ["M15", "H1", "H4", "D1"]
BARS_TO_DOWNLOAD    = 50000  # ~5 years of M15 data


def download_all():
    feed = MT5DataFeed(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
    if not feed.connect():
        log.error("Cannot connect to MT5. Make sure MT5 is running.")
        return

    for symbol in SYMBOLS_TO_DOWNLOAD:
        for tf in TIMEFRAMES:
            log.info(f"Downloading {symbol} {tf}...")
            df = feed.get_ohlcv(symbol, tf, count=BARS_TO_DOWNLOAD)
            if df is None or df.empty:
                log.warning(f"No data for {symbol} {tf}")
                continue

            filename = DATA_DIR / f"{symbol}_{tf}.csv"
            df.to_csv(filename)
            log.info(f"✅ Saved {len(df)} bars → {filename}")

    feed.disconnect()
    log.info(f"\n📁 All data saved to: {DATA_DIR}")
    print(f"\nData directory: {DATA_DIR}")
    print("Files ready for backtesting!")


if __name__ == "__main__":
    print("TradeGOD Data Downloader")
    print("This will download historical data from MetaTrader 5.")
    print("Make sure MT5 is open and .env credentials are set.\n")
    download_all()
