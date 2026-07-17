"""
TradeGOD Quant Fund — App Configuration Loader
Loads .env variables and JSON configs into a unified settings object.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Helper ─────────────────────────────────────────────────────────────────────
def _load_json(filename: str) -> dict:
    config_path = Path(__file__).parent / filename
    with open(config_path, "r", encoding="utf-8") as f:
        raw = f.read()
        # Strip comments (lines starting with _comment keys are valid JSON but let's keep them)
        return json.loads(raw)


# ── MT5 Settings ───────────────────────────────────────────────────────────────
MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "ICMarkets-Demo")
MT5_PATH     = os.getenv("MT5_PATH", "")

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── ZeroMQ ─────────────────────────────────────────────────────────────────────
ZMQ_PUSH_PORT = int(os.getenv("ZMQ_PUSH_PORT", "5555"))
ZMQ_PULL_PORT = int(os.getenv("ZMQ_PULL_PORT", "5556"))

# ── ForexFactory ───────────────────────────────────────────────────────────────
FOREX_FACTORY_URL = os.getenv(
    "FOREX_FACTORY_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
)

# ── Load JSON Configs ──────────────────────────────────────────────────────────
RISK          = _load_json("risk_limits.json")
STRATEGY      = _load_json("strategy_params.json")
SYMBOLS       = _load_json("symbols.json")

# ── Convenience Aliases (5K Account) ──────────────────────────────────────────
ACCOUNT_SIZE           = float(os.getenv("ACCOUNT_SIZE",  RISK["account"]["starting_capital"]))
RISK_PCT               = RISK["risk_per_trade"]["percent"]           # 1.0
MAX_RISK_DOLLAR        = RISK["risk_per_trade"]["max_dollar_amount"]  # $50
DAILY_KILL_DOLLAR      = RISK["daily_limits"]["kill_switch_dollar"]   # $225
DAILY_HARD_DOLLAR      = RISK["daily_limits"]["hard_limit_dollar"]    # $250
MAX_DD_DOLLAR          = RISK["max_drawdown"]["kill_switch_dollar"]   # $450
MAGIC_NUMBER           = RISK["account"]["magic_number"]              # 777999

MIN_HOLD_SECONDS       = RISK["position_rules"]["min_hold_time_seconds"]     # 180
DIRECTIONAL_COOLDOWN   = RISK["position_rules"]["directional_cooldown_seconds"]  # 600
MAX_SLIPPAGE_PIPS      = RISK["position_rules"]["max_slippage_pips"]          # 3

# ── Session Times (UTC) ────────────────────────────────────────────────────────
SESSIONS = {
    "ASIAN":  {"open": 0,  "close": 8},   # 00:00–08:00 UTC
    "LONDON": {"open": 7,  "close": 16},  # 07:00–16:00 UTC
    "NY":     {"open": 12, "close": 21},  # 12:00–21:00 UTC
}

# Kill-zone windows (primary trading)
KILLZONE_LONDON_OPEN_UTC_HOUR  = 7
KILLZONE_NY_OPEN_UTC_HOUR      = 13
TRADING_END_UTC_HOUR           = 21
FRIDAY_CLOSE_UTC_HOUR          = 21

# ── Validation ─────────────────────────────────────────────────────────────────
def validate_config():
    """Sanity-check critical settings at startup."""
    assert MT5_LOGIN != 0,          "MT5_LOGIN not set in .env"
    assert MT5_PASSWORD != "",      "MT5_PASSWORD not set in .env"
    assert TELEGRAM_BOT_TOKEN != "", "TELEGRAM_BOT_TOKEN not set in .env"
    assert ACCOUNT_SIZE > 0,        "ACCOUNT_SIZE must be positive"
    assert MAX_RISK_DOLLAR <= ACCOUNT_SIZE * 0.03, \
        f"Risk per trade ${MAX_RISK_DOLLAR} exceeds 3% limit for ${ACCOUNT_SIZE} account"
    print("[CONFIG] All settings validated successfully.")
