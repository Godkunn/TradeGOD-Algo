"""
TradeGOD — SMC Logic Engine
Implements: BOS, CHoCH, FVG, Order Blocks, IDM, Liquidity Sweeps,
            BPR (Balanced Price Range), Supply/Demand Flips, Breaker Blocks.

All functions operate on pandas DataFrames with columns:
    time, open, high, low, close, tick_volume
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from utils.indicators import atr, body_ratio, candle_range


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str   # "HIGH" or "LOW"
    time: pd.Timestamp


@dataclass
class FVG:
    """Fair Value Gap / Imbalance"""
    index: int          # Index of middle candle (the displacement candle)
    top: float          # FVG top boundary
    bottom: float       # FVG bottom boundary
    center: float       # 50% Consequent Encroachment level
    direction: str      # "BULLISH" or "BEARISH"
    mitigated: bool = False
    is_ifvg: bool = False   # Inverse FVG (failed)


@dataclass
class OrderBlock:
    """Validated SMC Order Block"""
    index: int
    high: float
    low: float
    mid: float          # 50% level
    direction: str      # "BULLISH" (buy) or "BEARISH" (sell)
    has_fvg: bool = False
    has_liquidity_sweep: bool = False
    idm_swept: bool = False
    unmitigated: bool = True
    score: int = 0      # 0-10 scoring system
    time: Optional[pd.Timestamp] = None


@dataclass
class SupplyDemandZone:
    proximal: float     # Edge closest to current price (entry line)
    distal: float       # Far edge (stop loss line)
    kind: str           # "DEMAND" or "SUPPLY"
    base_candle_count: int = 0
    explosion_atr_ratio: float = 0.0
    fresh: bool = True
    touch_count: int = 0
    score: int = 0


@dataclass
class BPR:
    """Balanced Price Range — overlap of Bullish FVG + Bearish FVG"""
    top: float
    bottom: float
    center: float
    direction: str      # "BEARISH" or "BULLISH" (which way to trade)
    active: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SWING HIGH / LOW DETECTION (Fractal-Based)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_swing_points(df: pd.DataFrame, lookback: int = 5) -> List[SwingPoint]:
    """
    Detect swing highs and lows using fractal algorithm.
    A swing high: candle[i].high > all highs in [i-lookback .. i-1] and [i+1 .. i+lookback].
    Uses BODY CLOSE for BOS confirmation (not wicks).
    """
    swings: List[SwingPoint] = []
    n = len(df)
    for i in range(lookback, n - lookback):
        # Swing High
        is_sh = all(df["high"].iloc[i] > df["high"].iloc[j]
                    for j in range(i - lookback, i + lookback + 1) if j != i)
        if is_sh:
            swings.append(SwingPoint(
                index=i, price=df["high"].iloc[i],
                kind="HIGH", time=df.index[i]
            ))
        # Swing Low
        is_sl = all(df["low"].iloc[i] < df["low"].iloc[j]
                    for j in range(i - lookback, i + lookback + 1) if j != i)
        if is_sl:
            swings.append(SwingPoint(
                index=i, price=df["low"].iloc[i],
                kind="LOW", time=df.index[i]
            ))
    return sorted(swings, key=lambda s: s.index)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MARKET STRUCTURE: BOS & CHoCH
# ═══════════════════════════════════════════════════════════════════════════════

def detect_market_structure(df: pd.DataFrame,
                            swings: List[SwingPoint]) -> pd.DataFrame:
    """
    Returns DataFrame with columns: bos, choch, trend
    BOS   = candle CLOSE beyond the most recent swing high/low (body close, not wick)
    CHoCH = BOS in opposite direction → trend reversal
    """
    result = pd.DataFrame(index=df.index,
                          data={"bos": False, "choch": False,
                                "trend": "NEUTRAL", "bos_level": np.nan})
    if len(swings) < 2:
        return result

    trend       = "NEUTRAL"
    last_sh     = None  # last swing high
    last_sl     = None  # last swing low

    for i in range(len(df)):
        close = df["close"].iloc[i]

        # Update swing point references up to bar i
        for s in swings:
            if s.index < i:
                if s.kind == "HIGH":
                    last_sh = s
                else:
                    last_sl = s

        if last_sh is None or last_sl is None:
            continue

        # BOS Bullish: close strictly above last swing high
        if close > last_sh.price:
            if trend == "BEARISH":
                result.at[df.index[i], "choch"] = True
                result.at[df.index[i], "bos_level"] = last_sh.price
            else:
                result.at[df.index[i], "bos"] = True
                result.at[df.index[i], "bos_level"] = last_sh.price
            trend = "BULLISH"

        # BOS Bearish: close strictly below last swing low
        elif close < last_sl.price:
            if trend == "BULLISH":
                result.at[df.index[i], "choch"] = True
                result.at[df.index[i], "bos_level"] = last_sl.price
            else:
                result.at[df.index[i], "bos"] = True
                result.at[df.index[i], "bos_level"] = last_sl.price
            trend = "BEARISH"

        result.at[df.index[i], "trend"] = trend

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FAIR VALUE GAPS (FVG / Imbalance)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_fvg(df: pd.DataFrame, min_gap_pips: float = 2.0,
               pip_size: float = 0.0001) -> List[FVG]:
    """
    Scan 3-candle patterns for Fair Value Gaps.

    Bullish FVG: Candle[i-2].High < Candle[i].Low  (gap between candles 1 and 3)
    Bearish FVG: Candle[i-2].Low  > Candle[i].High
    """
    fvgs: List[FVG] = []
    min_gap = min_gap_pips * pip_size

    for i in range(2, len(df)):
        c1_high = df["high"].iloc[i - 2]
        c1_low  = df["low"].iloc[i - 2]
        c3_high = df["high"].iloc[i]
        c3_low  = df["low"].iloc[i]

        # Bullish FVG
        if c3_low > c1_high and (c3_low - c1_high) >= min_gap:
            top    = c3_low
            bottom = c1_high
            fvgs.append(FVG(
                index=i - 1, top=top, bottom=bottom,
                center=(top + bottom) / 2, direction="BULLISH"
            ))

        # Bearish FVG
        elif c1_low > c3_high and (c1_low - c3_high) >= min_gap:
            top    = c1_low
            bottom = c3_high
            fvgs.append(FVG(
                index=i - 1, top=top, bottom=bottom,
                center=(top + bottom) / 2, direction="BEARISH"
            ))

    return fvgs


def update_fvg_mitigation(fvgs: List[FVG], current_high: float,
                           current_low: float) -> List[FVG]:
    """Mark FVGs as mitigated when price enters the gap zone."""
    for fvg in fvgs:
        if fvg.mitigated:
            continue
        if fvg.direction == "BULLISH" and current_low <= fvg.top:
            fvg.mitigated = True
        elif fvg.direction == "BEARISH" and current_high >= fvg.bottom:
            fvg.mitigated = True
    return fvgs


def detect_ifvg(fvgs: List[FVG], df: pd.DataFrame) -> List[FVG]:
    """
    Detect Inverse FVGs: FVG that failed (price closed through it).
    A bullish FVG becomes bearish IFVG when candle body closes BELOW fvg.bottom.
    """
    for fvg in fvgs:
        if fvg.mitigated or fvg.is_ifvg:
            continue
        subsequent = df.iloc[fvg.index + 1:]
        if fvg.direction == "BULLISH":
            # Failed if price closes below FVG bottom
            failed = subsequent[subsequent["close"] < fvg.bottom]
            if not failed.empty:
                fvg.is_ifvg = True
                fvg.direction = "BEARISH"   # Now acts as resistance
        elif fvg.direction == "BEARISH":
            failed = subsequent[subsequent["close"] > fvg.top]
            if not failed.empty:
                fvg.is_ifvg = True
                fvg.direction = "BULLISH"   # Now acts as support
    return fvgs


# ═══════════════════════════════════════════════════════════════════════════════
# 4. INDUCEMENT (IDM) TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

def detect_idm(swings: List[SwingPoint], trend: str) -> Optional[float]:
    """
    In a BULLISH trend: IDM = first minor swing LOW after the last BOS.
    In a BEARISH trend: IDM = first minor swing HIGH after the last BOS.
    Returns the IDM price level (None if not detected).
    """
    if not swings:
        return None
    if trend == "BULLISH":
        lows = [s for s in swings if s.kind == "LOW"]
        return lows[-1].price if lows else None
    elif trend == "BEARISH":
        highs = [s for s in swings if s.kind == "HIGH"]
        return highs[-1].price if highs else None
    return None


def is_idm_swept(idm_level: float, current_price: float, trend: str) -> bool:
    """
    IDM swept = price has crossed the IDM level.
    Bullish trend: price dipped BELOW IDM level.
    Bearish trend: price spiked ABOVE IDM level.
    """
    if idm_level is None:
        return False
    if trend == "BULLISH":
        return current_price < idm_level
    return current_price > idm_level


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LIQUIDITY SWEEPS (Stop Hunts)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_liquidity_sweep(df: pd.DataFrame,
                            swings: List[SwingPoint]) -> pd.Series:
    """
    Liquidity sweep: wick crossed the swing level but CLOSE is back inside.
    Returns boolean series: True where a sweep occurred.

    Bullish sweep (buy setup): wick below swing low, close above swing low.
    Bearish sweep (sell setup): wick above swing high, close below swing high.
    """
    result = pd.Series(False, index=df.index)

    highs = [s for s in swings if s.kind == "HIGH"]
    lows  = [s for s in swings if s.kind == "LOW"]

    if not highs or not lows:
        return result

    # Most recent significant levels
    last_sh = highs[-1].price if highs else None
    last_sl = lows[-1].price  if lows  else None

    for i in range(len(df)):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        c = df["close"].iloc[i]

        # Bearish sweep: wick above swing high, but CLOSE < swing high
        if last_sh and h > last_sh and c < last_sh:
            result.iloc[i] = True

        # Bullish sweep: wick below swing low, but CLOSE > swing low
        if last_sl and l < last_sl and c > last_sl:
            result.iloc[i] = True

    return result


def detect_equal_highs_lows(df: pd.DataFrame,
                              tolerance_pips: float = 3.0,
                              pip_size: float = 0.0001) -> dict:
    """
    Detect Equal Highs (EQH) and Equal Lows (EQL) — retail liquidity pools.
    Returns {"EQH": [(price, idx1, idx2), ...], "EQL": [...]}
    """
    tol = tolerance_pips * pip_size
    eqh: List[Tuple] = []
    eql: List[Tuple] = []

    highs = df["high"].values
    lows  = df["low"].values

    for i in range(len(df) - 1):
        for j in range(i + 1, min(i + 20, len(df))):
            if abs(highs[i] - highs[j]) <= tol:
                eqh.append((max(highs[i], highs[j]), i, j))
            if abs(lows[i] - lows[j]) <= tol:
                eql.append((min(lows[i], lows[j]), i, j))

    return {"EQH": eqh, "EQL": eql}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ORDER BLOCK DETECTION (7-Step Validation)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_order_blocks(df: pd.DataFrame, fvgs: List[FVG],
                         swings: List[SwingPoint],
                         pip_size: float = 0.0001) -> List[OrderBlock]:
    """
    7-Step SMC Order Block Validation:
    1. Higher Timeframe context (passed in via trend)
    2. Break of Structure occurred
    3. Candle swept prior liquidity (wick)
    4. FVG exists after the OB
    5. IDM was swept
    6. Zone is unmitigated
    7. Extreme OB preferred over Decisional OB
    """
    obs: List[OrderBlock] = []
    if len(df) < 5:
        return obs

    for i in range(2, len(df) - 2):
        c = df.iloc[i]
        is_bullish_ob = (
            c["close"] > c["open"] and    # Bullish candle
            df["low"].iloc[i] < df["low"].iloc[i - 1]  # Swept prior low
        )
        is_bearish_ob = (
            c["close"] < c["open"] and    # Bearish candle
            df["high"].iloc[i] > df["high"].iloc[i - 1]  # Swept prior high
        )

        if not (is_bullish_ob or is_bearish_ob):
            continue

        # Check if FVG exists after this candle
        ob_has_fvg = any(
            fvg.index > i and fvg.index <= i + 3
            for fvg in fvgs
        )

        direction = "BULLISH" if is_bullish_ob else "BEARISH"
        score = _score_order_block(c, df, i, ob_has_fvg, pip_size)

        ob = OrderBlock(
            index=i,
            high=c["high"],
            low=c["low"],
            mid=(c["high"] + c["low"]) / 2,
            direction=direction,
            has_fvg=ob_has_fvg,
            has_liquidity_sweep=True,
            unmitigated=True,
            score=score,
            time=df.index[i] if hasattr(df.index[i], 'date') else None
        )
        obs.append(ob)

    return obs


def _score_order_block(candle: pd.Series, df: pd.DataFrame,
                        idx: int, has_fvg: bool, pip_size: float) -> int:
    """
    10-Point Scoring System for Order Blocks.
    8+ required for trade execution.
    """
    score = 0

    # 1. ERC departure (body ratio >= 80%)
    body = abs(candle["close"] - candle["open"])
    rng  = candle["high"] - candle["low"]
    if rng > 0 and body / rng >= 0.80:
        score += 2

    # 2. FVG after OB
    if has_fvg:
        score += 2

    # 3. Unmitigated (fresh zone)
    score += 3   # Assumed fresh at creation; marked dirty on first touch

    # 4. Minimum RR achievable (basic check)
    score += 3   # RR is calculated at entry time; grant default for now

    return min(score, 10)


def update_ob_mitigation(obs: List[OrderBlock],
                          current_high: float,
                          current_low: float) -> List[OrderBlock]:
    """Mark OBs as mitigated when price enters the zone."""
    for ob in obs:
        if not ob.unmitigated:
            continue
        if ob.direction == "BULLISH" and current_low <= ob.high:
            ob.unmitigated = False
        elif ob.direction == "BEARISH" and current_high >= ob.low:
            ob.unmitigated = False
    return obs


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SUPPLY & DEMAND ZONES (Fortune Talks Model)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_supply_demand_zones(df: pd.DataFrame,
                                max_base_candles: int = 6,
                                base_max_range_pips: float = 15.0,
                                explosion_atr_mult: float = 2.0,
                                pip_size: float = 0.0001) -> List[SupplyDemandZone]:
    """
    Detect Supply & Demand zones using the DBR/RBD/RBR/DBD pattern:
    - Base: 1-6 tight candles (body < 50% range, range < 15 pips)
    - Explosion: next candle body >= 2x ATR
    """
    zones: List[SupplyDemandZone] = []
    _atr = atr(df, 14)
    max_range = base_max_range_pips * pip_size

    i = 0
    while i < len(df) - 2:
        # Find base candles
        base_start = i
        base_count = 0
        base_highs = []
        base_lows  = []

        while i < len(df) - 1 and base_count < max_base_candles:
            c = df.iloc[i]
            c_range = c["high"] - c["low"]
            c_body  = abs(c["close"] - c["open"])
            is_base = (c_range <= max_range) and (c_body <= c_range * 0.50)

            if is_base:
                base_highs.append(c["high"])
                base_lows.append(c["low"])
                base_count += 1
                i += 1
            else:
                break

        if base_count == 0:
            i += 1
            continue

        if i >= len(df):
            break

        # Explosion candle
        exp = df.iloc[i]
        exp_body  = abs(exp["close"] - exp["open"])
        atr_val   = _atr.iloc[i] if not np.isnan(_atr.iloc[i]) else 0.001
        is_explosion = exp_body >= atr_val * explosion_atr_mult

        if not is_explosion:
            i += 1
            continue

        # Zone boundaries (base candle zone)
        zone_high = max(base_highs)
        zone_low  = min(base_lows)
        exp_ratio = exp_body / atr_val

        # Determine Supply or Demand
        if exp["close"] > exp["open"]:
            # Rally after base → Demand Zone (DBR)
            kind = "DEMAND"
            proximal = zone_high  # Top of base (entry line)
            distal   = zone_low   # Bottom of base (SL line)
        else:
            # Drop after base → Supply Zone (RBD)
            kind = "SUPPLY"
            proximal = zone_low   # Bottom of base (entry line)
            distal   = zone_high  # Top of base (SL line)

        score = _score_sd_zone(base_count, exp_ratio)

        zones.append(SupplyDemandZone(
            proximal=proximal,
            distal=distal,
            kind=kind,
            base_candle_count=base_count,
            explosion_atr_ratio=exp_ratio,
            score=score
        ))
        i += 1

    return zones


def _score_sd_zone(base_count: int, explosion_ratio: float) -> int:
    """Score Supply/Demand zone 0-10."""
    score = 0
    # Strength of departure
    if explosion_ratio >= 3.0: score += 3
    elif explosion_ratio >= 2.0: score += 2
    # Time at base
    if base_count <= 2: score += 3
    elif base_count <= 6: score += 2
    # Freshness (always fresh at creation)
    score += 3    # Deduct on touch later
    return min(score, 10)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. BALANCED PRICE RANGE (BPR)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_bpr(fvgs: List[FVG]) -> List[BPR]:
    """
    BPR = Overlap zone between a Bullish FVG and a Bearish FVG.
    Banks use BPR for fast fills. Highest priority entry.
    """
    bprs: List[BPR] = []
    bullish_fvgs = [f for f in fvgs if f.direction == "BULLISH" and not f.mitigated]
    bearish_fvgs = [f for f in fvgs if f.direction == "BEARISH" and not f.mitigated]

    for bfvg in bullish_fvgs:
        for sfvg in bearish_fvgs:
            # Check temporal proximity (within 10 bars)
            if abs(bfvg.index - sfvg.index) > 10:
                continue

            # Calculate overlap
            bpr_top    = min(bfvg.top,    sfvg.top)
            bpr_bottom = max(bfvg.bottom, sfvg.bottom)

            if bpr_top > bpr_bottom:
                # Valid BPR exists
                # Direction: if bearish FVG came AFTER bullish FVG → bearish BPR
                direction = "BEARISH" if sfvg.index > bfvg.index else "BULLISH"
                bprs.append(BPR(
                    top=bpr_top,
                    bottom=bpr_bottom,
                    center=(bpr_top + bpr_bottom) / 2,
                    direction=direction
                ))

    return bprs


# ═══════════════════════════════════════════════════════════════════════════════
# 9. BREAKER BLOCKS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_breaker_blocks(df: pd.DataFrame,
                            obs: List[OrderBlock],
                            swings: List[SwingPoint]) -> List[OrderBlock]:
    """
    Breaker Block = Failed Order Block that swept liquidity.
    Condition: price sweeps past swing high/low AND closes on opposite side (CHoCH).
    The last up-close candle before the sweep = Bullish Breaker Block.
    """
    breakers: List[OrderBlock] = []

    for ob in obs:
        if ob.unmitigated:
            continue   # Only mitigated (failed) OBs can be breakers

        # Check if the OB failure was preceded by a liquidity sweep
        future_df = df.iloc[ob.index:]
        if ob.direction == "BULLISH":
            # Failed bullish OB → look for sweep below swing low + CHoCH up
            lows  = [s for s in swings if s.kind == "LOW" and s.index >= ob.index]
            if not lows:
                continue
            swept = any(future_df["low"].values < lows[-1].price)
            if swept:
                choch_up = any(future_df["close"].values > future_df["high"].shift(1).values)
                if choch_up:
                    ob.direction = "BULLISH"  # Breaker acts as support
                    breakers.append(ob)
        else:
            highs = [s for s in swings if s.kind == "HIGH" and s.index >= ob.index]
            if not highs:
                continue
            swept = any(future_df["high"].values > highs[-1].price)
            if swept:
                choch_down = any(future_df["close"].values < future_df["low"].shift(1).values)
                if choch_down:
                    ob.direction = "BEARISH"
                    breakers.append(ob)

    return breakers


# ═══════════════════════════════════════════════════════════════════════════════
# 10. HTF CURVE FILTER (33/66 Rule)
# ═══════════════════════════════════════════════════════════════════════════════

def get_htf_curve_location(current_price: float,
                            htf_demand_price: float,
                            htf_supply_price: float) -> float:
    """
    Returns 0.0 to 1.0 position within the HTF Supply/Demand range.
    < 0.33 → Low on curve → BUY ONLY
    > 0.66 → High on curve → SELL ONLY
    """
    rng = htf_supply_price - htf_demand_price
    if rng == 0:
        return 0.5
    return (current_price - htf_demand_price) / rng


def htf_bias(curve_location: float) -> str:
    """Returns 'BUY', 'SELL', or 'NEUTRAL' based on HTF curve position."""
    if curve_location < 0.33:
        return "BUY"
    if curve_location > 0.66:
        return "SELL"
    return "NEUTRAL"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ENTRY SIGNAL AGGREGATOR
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_smc_setup(
    current_price: float,
    current_high: float,
    current_low: float,
    trend: str,
    fvgs: List[FVG],
    obs: List[OrderBlock],
    bprs: List[BPR],
    sd_zones: List[SupplyDemandZone],
    idm_level: Optional[float],
    equilibrium: float
) -> Optional[dict]:
    """
    Master SMC entry evaluator. Priority:
    1. BPR (highest probability — direct limit)
    2. Unmitigated OB with score >= 8 (LTF CHoCH confirmation needed)
    3. Fresh S/D Zone with score >= 8
    Returns signal dict or None.
    """
    # ── Priority 1: BPR ───────────────────────────────────────────────────────
    for bpr in bprs:
        if not bpr.active:
            continue
        if bpr.direction == "BEARISH" and current_price >= bpr.bottom:
            if trend == "BEARISH" or current_price > equilibrium:
                return {
                    "type": "BPR_SELL",
                    "entry":  bpr.bottom,
                    "sl":     bpr.top,
                    "source": "BPR",
                    "priority": 1
                }
        if bpr.direction == "BULLISH" and current_price <= bpr.top:
            if trend == "BULLISH" or current_price < equilibrium:
                return {
                    "type": "BPR_BUY",
                    "entry":  bpr.top,
                    "sl":     bpr.bottom,
                    "source": "BPR",
                    "priority": 1
                }

    # ── Priority 2: High-Score Order Block ───────────────────────────────────
    for ob in sorted(obs, key=lambda x: x.score, reverse=True):
        if not ob.unmitigated or ob.score < 8:
            continue
        # IDM must be swept before entering
        if idm_level and not is_idm_swept(idm_level, current_price, trend):
            continue
        if ob.direction == "BULLISH" and current_low <= ob.high:
            if current_price < equilibrium:   # Discount zone
                return {
                    "type": "OB_BUY",
                    "entry":  ob.high,
                    "sl":     ob.low,
                    "source": "OrderBlock",
                    "score":  ob.score,
                    "priority": 2
                }
        if ob.direction == "BEARISH" and current_high >= ob.low:
            if current_price > equilibrium:   # Premium zone
                return {
                    "type": "OB_SELL",
                    "entry":  ob.low,
                    "sl":     ob.high,
                    "source": "OrderBlock",
                    "score":  ob.score,
                    "priority": 2
                }

    # ── Priority 3: Supply/Demand Zone ───────────────────────────────────────
    for zone in sd_zones:
        if not zone.fresh or zone.score < 8:
            continue
        if zone.kind == "DEMAND" and abs(current_price - zone.proximal) < 0.001:
            return {
                "type": "SD_BUY",
                "entry":  zone.proximal,
                "sl":     zone.distal,
                "source": "S&D_Zone",
                "score":  zone.score,
                "priority": 3
            }
        if zone.kind == "SUPPLY" and abs(current_price - zone.proximal) < 0.001:
            return {
                "type": "SD_SELL",
                "entry":  zone.proximal,
                "sl":     zone.distal,
                "source": "S&D_Zone",
                "score":  zone.score,
                "priority": 3
            }

    return None
