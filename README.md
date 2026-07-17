# ⚡ TradeGOD Quant Fund v2.0
### Funding Pips $5K Account — SMC + Institutional Algo Engine

---

## 🏗️ Architecture

```
TradeGOD_Quant_Fund/
├── config/                  ← All settings (JSON + Python)
│   ├── app_config.py        ← Central config loader
│   ├── risk_limits.json     ← $5K account risk parameters
│   ├── strategy_params.json ← Tunable strategy settings
│   └── symbols.json         ← Tradable pairs + pip values
│
├── core/                    ← Engine modules
│   ├── data_feed.py         ← MT5 real-time + Dukascopy loader
│   ├── risk_manager.py      ← $50/trade cap, kill-switch, hold timer
│   ├── compliance_guard.py  ← Session + news + spread gatekeeper
│   ├── news_filter.py       ← ForexFactory blackout windows
│   └── zmq_bridge.py        ← Python → MT5 EA command bus
│
├── utils/                   ← Pure Python helpers
│   ├── indicators.py        ← BB, KC, ATR, RSI, MACD, HA, Fib
│   ├── smc_logic.py         ← BOS, CHoCH, FVG, OB, BPR, IDM, Sweeps
│   ├── time_ops.py          ← UTC/IST, sessions, killzones
│   └── logger.py            ← Color-coded structured logging
│
├── strategies/              ← 6 Strategy Modules
│   ├── base_strategy.py     ← Abstract base + Signal dataclass
│   ├── s01_volatility_squeeze.py  ← BB/KC squeeze breakout
│   ├── s02_smc_ob_fvg.py          ← Full SMC stack (primary)
│   ├── s03_liquidity_sweep.py     ← EQL/EQH stop hunt reversal
│   ├── s04_gold_scalper.py        ← XAUUSD zone scalper
│   ├── s05_rem_liquidity.py       ← H4 sweep + M15 FVG entry
│   └── s06_asymmetric_risk.py     ← Defensive/Aggressive mode switcher
│
├── execution_layer/         ← MetaTrader 5
│   └── TradeGOD_EA.mq5      ← MQL5 Expert Advisor (ZMQ + OrderSend)
│
├── telemetry/               ← Monitoring
│   ├── telegram_bot.py      ← Real-time alerts on phone
│   └── dashboard_app.py     ← Streamlit live dashboard
│
├── backtesting/             ← Historical testing
│   ├── data_downloader.py   ← Pull data from MT5
│   └── backtest_engine.py   ← Bar-by-bar simulator
│
├── tests/                   ← Unit tests
│   └── test_risk_math.py    ← Risk engine math verification
│
├── main_orchestrator.py     ← 🚀 MAIN ENTRY POINT
├── run.bat                  ← Windows launcher menu
├── requirements.txt
└── .env.example
```

---

## ⚡ Quick Start

### Step 1: Setup Environment
```bash
cd TradeGOD_Quant_Fund
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your MT5 credentials and Telegram chat ID
```

### Step 2: Setup MetaTrader 5
1. Open **MetaTrader 5**
2. Install ZeroMQ DLL: [mql-zmq releases](https://github.com/dingmaotu/mql-zmq/releases)
3. Copy `execution_layer/TradeGOD_EA.mq5` to `MT5/MQL5/Experts/`
4. Compile the EA in MetaEditor (F7)
5. Attach to **any chart** (e.g., EURUSD M15)
6. Set: `Push Port=5555`, `Pull Port=5556`, `Magic=777999`

### Step 3: Run Backtesting First
```bash
# Download historical data
python backtesting/data_downloader.py

# Run backtest on EURUSD with S02 (SMC strategy)
python backtesting/backtest_engine.py --symbol EURUSD --strategy S02 --csv backtesting/data/EURUSD_M15.csv
```

### Step 4: Go Live
```bash
# Option A: Double-click run.bat (Windows menu)
run.bat

# Option B: Direct Python
python main_orchestrator.py
```

### Step 5: Monitor
```bash
# Dashboard
streamlit run telemetry/dashboard_app.py
# Opens at http://localhost:8501

# Telegram: Already configured with token in .env
```

---

## 💰 Account Parameters (Funding Pips 5K)

| Parameter | Value | Why |
|-----------|-------|-----|
| Account Size | $5,000 | Funding Pips 5K BOGO |
| Risk Per Trade | **$50 (1%)** | Hard cap, never exceeded |
| Daily Kill-Switch | **$225 (4.5%)** | 0.5% buffer before Funding Pips 5% limit |
| Max Total DD | **$450 (9%)** | 1% buffer before Funding Pips 10% limit |
| Min Hold Time | **180 seconds** | HFT compliance |
| Max Trades/Day | **2** | One Good Trade rule |
| Directional Cooldown | **10 min** | After SL hit, same symbol+direction |

---

## 🎯 Strategy Suite

| # | Strategy | Signal Source | Session | RR | Win Target |
|---|----------|---------------|---------|-----|------------|
| S01 | Volatility Squeeze | BB/KC breakout + EMA200 | LDN/NY KZ | 1:2 | 55%+ |
| S02 | SMC OB+FVG | BPR > OB > S&D | LDN/NY | 1:2–3 | 60%+ |
| S03 | Liquidity Sweep | EQL/EQH stop hunt | NY only | 1:2 | 55%+ |
| S04 | Gold Scalper | Zone touch + RSI | Asian/NY | 1:1 | 55%+ |
| S05 | REM Liquidity | H4 sweep + M15 FVG | LDN/NY | 1:2 | 55%+ |
| S06 | Asymmetric Risk | Equity-based mode | Wrapper | N/A | N/A |

---

## 🛡️ Funding Pips Compliance Checklist

- ✅ **No HFT**: All trades hold minimum 180s (keepalive: 185s)
- ✅ **News Blackout**: ±5min ForexFactory high-impact filter
- ✅ **No Weekend Positions**: Auto-close Friday 21:00 UTC
- ✅ **No Martingale**: Fixed 1% risk per trade, no averaging
- ✅ **Consistency Rule**: 60% concentration cap ($240 max single trade)
- ✅ **Daily Limit**: Kill-switch at 4.5%, hard stop at 5%
- ✅ **No VPN/Evasion**: Runs locally on your Windows machine
- ✅ **Magic Number**: 777999 identifies all bot trades

---

## 🧪 Run Tests
```bash
pytest tests/ -v
# All 15+ tests should pass
```

---

## 📱 Telegram Alerts
You'll receive alerts for:
- 🟢/🔴 Trade opened/closed with full details
- 💓 Hourly heartbeat with balance/equity
- 🚨 Kill-switch triggered
- 📰 News blackout activated
- 🗓️ Weekend close executed
- 🚀 Bot startup confirmation

---

## ⚠️ Disclaimer
This software is for educational purposes. Past performance does not guarantee future results. Trading involves risk. Always understand what you're running before going live on a funded account.
