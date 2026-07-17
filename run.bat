@echo off
title TradeGOD Quant Fund
color 0A

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║          ⚡ TradeGOD Quant Fund v2.0 ⚡              ║
echo  ║     Funding Pips 5K Account — SMC Engine Active     ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

REM ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ❌ Python not found. Install from python.org
    pause
    exit /b 1
)

REM ── Install dependencies if needed ──────────────────────────────────────────
echo  📦 Checking dependencies...
pip install -r requirements.txt -q
echo  ✅ Dependencies ready.
echo.

REM ── Copy .env if not exists ──────────────────────────────────────────────────
if not exist ".env" (
    echo  ⚠️  .env not found. Copying from .env.example...
    copy .env.example .env
    echo  ⚠️  EDIT .env with your MT5 credentials and Telegram chat ID before proceeding!
    echo.
    notepad .env
    pause
)

echo.
echo  ═══════════════════════════════════════════════════════
echo  ║  STEP 1: Make sure TradeGOD_EA.mq5 is compiled     ║
echo  ║  and attached to a chart in MetaTrader 5.           ║
echo  ║                                                     ║
echo  ║  STEP 2: Choose what to run:                        ║
echo  ║  [1] Start Live Trading Bot                         ║
echo  ║  [2] Open Dashboard (Streamlit)                     ║
echo  ║  [3] Run Backtester                                 ║
echo  ║  [4] Run Unit Tests                                 ║
echo  ║  [5] Exit                                           ║
echo  ═══════════════════════════════════════════════════════
echo.

set /p choice="  Your choice (1-5): "

if "%choice%"=="1" goto live
if "%choice%"=="2" goto dashboard
if "%choice%"=="3" goto backtest
if "%choice%"=="4" goto tests
if "%choice%"=="5" exit /b 0

:live
echo.
echo  🚀 Starting TradeGOD Live Bot...
echo  Press Ctrl+C to stop safely.
echo.
python main_orchestrator.py
pause
goto start

:dashboard
echo.
echo  📊 Opening Dashboard at http://localhost:8501
echo.
start "" http://localhost:8501
streamlit run telemetry/dashboard_app.py
pause
goto start

:backtest
echo.
set /p sym="  Symbol (e.g. EURUSD): "
set /p strat="  Strategy (S01-S05): "
set /p csv="  CSV path (e.g. backtesting/data/EURUSD_M15.csv): "
echo.
python backtesting/backtest_engine.py --symbol %sym% --strategy %strat% --csv %csv%
pause
goto start

:tests
echo.
echo  🧪 Running Unit Tests...
pytest tests/ -v --tb=short
pause
goto start

:start
cls
goto :eof
