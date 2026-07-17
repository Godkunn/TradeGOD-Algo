"""
TradeGOD — Technical Indicators Library
Pure-NumPy implementations. Works with pandas DataFrames or numpy arrays.
All functions expect a DataFrame with columns: open, high, low, close, tick_volume
"""

import numpy as np
import pandas as pd
from typing import Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# MOVING AVERAGES
# ═══════════════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema_200(df: pd.DataFrame) -> pd.Series:
    return ema(df["close"], 200)


def ema_50(df: pd.DataFrame) -> pd.Series:
    return ema(df["close"], 50)


def ema_21(df: pd.DataFrame) -> pd.Series:
    return ema(df["close"], 21)


# ═══════════════════════════════════════════════════════════════════════════════
# VOLATILITY
# ═══════════════════════════════════════════════════════════════════════════════

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_c).abs(),
        (l - prev_c).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def bollinger_bands(
    df: pd.DataFrame, period: int = 20, multiplier: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (upper_band, middle_band, lower_band)
    BB = SMA ± (multiplier × StdDev)
    """
    mid = sma(df["close"], period)
    std = df["close"].rolling(window=period).std()
    upper = mid + multiplier * std
    lower = mid - multiplier * std
    return upper, mid, lower


def keltner_channels(
    df: pd.DataFrame, period: int = 20, atr_mult: float = 1.5
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (upper_kc, middle_kc, lower_kc)
    KC = EMA ± (atr_mult × ATR)
    """
    mid = ema(df["close"], period)
    _atr = atr(df, period)
    upper = mid + atr_mult * _atr
    lower = mid - atr_mult * _atr
    return upper, mid, lower


def squeeze_detector(df: pd.DataFrame, bb_period=20, bb_mult=2.0,
                     kc_period=20, kc_mult=1.5) -> pd.Series:
    """
    Volatility Squeeze: True when BB is inside KC.
    squeeze = True  → market consolidating (energy building)
    squeeze = False → breakout occurring
    """
    bb_upper, _, bb_lower = bollinger_bands(df, bb_period, bb_mult)
    kc_upper, _, kc_lower = keltner_channels(df, kc_period, kc_mult)
    squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    return squeeze


# ═══════════════════════════════════════════════════════════════════════════════
# MOMENTUM
# ═══════════════════════════════════════════════════════════════════════════════

def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df["close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(df: pd.DataFrame, fast=12, slow=26, signal=9
         ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema  = ema(df["close"], fast)
    slow_ema  = ema(df["close"], slow)
    macd_line = fast_ema - slow_ema
    sig_line  = ema(macd_line.rename("close").to_frame(), signal).iloc[:, 0] \
        if isinstance(macd_line, pd.Series) else ema(pd.DataFrame({"close": macd_line}), signal)
    # Safe calculation
    sig_line  = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - sig_line
    return macd_line, sig_line, histogram


# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME
# ═══════════════════════════════════════════════════════════════════════════════

def volume_ma(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Moving Average of tick volume."""
    return sma(df["tick_volume"], period)


def volume_above_average(df: pd.DataFrame, period: int = 10,
                         multiplier: float = 1.0) -> pd.Series:
    """True where current volume > multiplier × MA(volume, period)."""
    vma = volume_ma(df, period)
    return df["tick_volume"] > vma * multiplier


# ═══════════════════════════════════════════════════════════════════════════════
# CANDLESTICK ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def candle_body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()


def candle_range(df: pd.DataFrame) -> pd.Series:
    return df["high"] - df["low"]


def upper_wick(df: pd.DataFrame) -> pd.Series:
    return df["high"] - df[["open", "close"]].max(axis=1)


def lower_wick(df: pd.DataFrame) -> pd.Series:
    return df[["open", "close"]].min(axis=1) - df["low"]


def is_bullish_candle(df: pd.DataFrame) -> pd.Series:
    return df["close"] > df["open"]


def is_bearish_candle(df: pd.DataFrame) -> pd.Series:
    return df["close"] < df["open"]


def body_ratio(df: pd.DataFrame) -> pd.Series:
    """Ratio of body to total candle range. 1.0 = Marubozu, 0.0 = Doji."""
    rng = candle_range(df)
    return candle_body(df) / rng.replace(0, np.nan)


def is_pin_bar(df: pd.DataFrame, wick_body_ratio: float = 2.0) -> pd.Series:
    """
    Bullish pin bar: lower wick >= wick_body_ratio × body, body in top 35% of range.
    Bearish pin bar: upper wick >= wick_body_ratio × body, body in bottom 35%.
    Returns: 1 = bullish pin, -1 = bearish pin, 0 = neither.
    """
    body  = candle_body(df)
    u_wick = upper_wick(df)
    l_wick = lower_wick(df)
    bullish_pin = (l_wick >= body * wick_body_ratio) & is_bullish_candle(df)
    bearish_pin = (u_wick >= body * wick_body_ratio) & is_bearish_candle(df)
    result = pd.Series(0, index=df.index)
    result[bullish_pin] = 1
    result[bearish_pin] = -1
    return result


def is_erc(df: pd.DataFrame, body_min_ratio: float = 0.80) -> pd.Series:
    """Extended Range Candle: body >= 80% of total range (institutional candle)."""
    return body_ratio(df) >= body_min_ratio


def is_inside_bar(df: pd.DataFrame) -> pd.Series:
    """True where current candle is completely inside the previous candle's range."""
    curr_high = df["high"]
    curr_low  = df["low"]
    prev_high = df["high"].shift(1)
    prev_low  = df["low"].shift(1)
    return (curr_high < prev_high) & (curr_low > prev_low)


def anti_fomo_filter(df: pd.DataFrame, atr_period: int = 14,
                     multiplier: float = 3.0) -> pd.Series:
    """
    True if candle is NOT a runaway FOMO candle.
    Block trading if current candle size > multiplier × ATR.
    """
    _atr  = atr(df, atr_period)
    c_range = candle_range(df)
    return c_range <= _atr * multiplier


# ═══════════════════════════════════════════════════════════════════════════════
# HEIKIN ASHI
# ═══════════════════════════════════════════════════════════════════════════════

def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate Heikin Ashi candles in background (for exit management).
    Returns DataFrame with columns: ha_open, ha_high, ha_low, ha_close.
    """
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open  = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2

    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low  = pd.concat([df["low"],  ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame({
        "ha_open":  ha_open,
        "ha_high":  ha_high,
        "ha_low":   ha_low,
        "ha_close": ha_close,
    }, index=df.index)


def ha_trend_reversal(ha_df: pd.DataFrame) -> pd.Series:
    """
    Detect Heikin Ashi trend reversal (for exit signals).
    -1: Strong bearish reversal (red candle with no upper wick) → Exit long.
    +1: Strong bullish reversal (green candle with no lower wick) → Exit short.
     0: No reversal signal.
    """
    result = pd.Series(0, index=ha_df.index)
    is_red   = ha_df["ha_close"] < ha_df["ha_open"]
    is_green = ha_df["ha_close"] > ha_df["ha_open"]
    no_upper_wick = (ha_df["ha_high"] - ha_df[["ha_open", "ha_close"]].max(axis=1)).abs() < 0.00001
    no_lower_wick = (ha_df[["ha_open", "ha_close"]].min(axis=1) - ha_df["ha_low"]).abs() < 0.00001
    result[is_red & no_upper_wick]   = -1   # Exit long
    result[is_green & no_lower_wick] = +1   # Exit short
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# FIBONACCI
# ═══════════════════════════════════════════════════════════════════════════════

def fibonacci_levels(swing_low: float, swing_high: float) -> dict:
    """
    Returns key Fibonacci levels from swing low to swing high.
    Premium zone: > 0.5 (Equilibrium)
    Discount zone: < 0.5
    """
    diff = swing_high - swing_low
    return {
        "0.0":   swing_low,
        "0.236": swing_low + 0.236 * diff,
        "0.382": swing_low + 0.382 * diff,
        "0.500": swing_low + 0.500 * diff,   # Equilibrium
        "0.618": swing_low + 0.618 * diff,   # Golden ratio
        "0.705": swing_low + 0.705 * diff,
        "0.786": swing_low + 0.786 * diff,
        "1.0":   swing_high,
        "1.272": swing_low + 1.272 * diff,   # Butterfly extension
        "1.618": swing_low + 1.618 * diff,
    }


def is_in_discount_zone(price: float, swing_low: float, swing_high: float) -> bool:
    """True if price is below the 50% equilibrium (Discount = good to BUY)."""
    equilibrium = (swing_high + swing_low) / 2
    return price < equilibrium


def is_in_premium_zone(price: float, swing_low: float, swing_high: float) -> bool:
    """True if price is above the 50% equilibrium (Premium = good to SELL)."""
    equilibrium = (swing_high + swing_low) / 2
    return price > equilibrium
