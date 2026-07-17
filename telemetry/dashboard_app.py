"""
TradeGOD — Streamlit Dashboard
Real-time monitoring via web browser at http://localhost:8501
Shows balance, equity, daily P&L, positions, compliance status.

Run: streamlit run telemetry/dashboard_app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sqlite3
from datetime import datetime
from config.app_config import ACCOUNT_SIZE, DAILY_KILL_DOLLAR, MAX_RISK_DOLLAR, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
from utils.time_ops import now_utc, get_current_session, is_trading_allowed
from core.data_feed import MT5DataFeed

DB_PATH = Path(__file__).parent.parent / "database" / "trade_logs.db"

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TradeGOD — Live Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Dark Theme CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0a0a0f; color: #e0e0e0; }
    .metric-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #0f3460;
        border-radius: 12px; padding: 20px; margin: 8px 0;
    }
    .metric-value { font-size: 2rem; font-weight: 700; color: #00d4aa; }
    .metric-label { font-size: 0.85rem; color: #888; text-transform: uppercase; }
    .status-badge {
        display: inline-block; padding: 4px 12px;
        border-radius: 20px; font-size: 0.75rem; font-weight: 600;
    }
    .badge-green { background: #00d4aa22; color: #00d4aa; border: 1px solid #00d4aa44; }
    .badge-red   { background: #ff445422; color: #ff4454; border: 1px solid #ff445444; }
    .badge-yellow{ background: #ffa50022; color: #ffa500; border: 1px solid #ffa50044; }
    h1, h2, h3 { color: #00d4aa; }
    .stMetric label { color: #888 !important; font-size: 0.8rem !important; }
    .stMetric div[data-testid="metric-container"] { background: #1a1a2e; border-radius: 8px; padding: 12px; }
</style>
""", unsafe_allow_html=True)


# ── Data Loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)  # Refresh every 30 seconds
def load_trades():
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM trades ORDER BY open_time DESC", conn)
    conn.close()
    return df

def get_mt5_feed():
    feed = MT5DataFeed(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)
    feed.connect()
    return feed


def get_daily_stats(df: pd.DataFrame) -> dict:
    today = now_utc().strftime("%Y-%m-%d")
    today_trades = df[df["open_time"].str.startswith(today)] if not df.empty else df
    closed_today = today_trades[today_trades["status"] == "CLOSED"]
    return {
        "total_trades": len(today_trades),
        "wins":   len(closed_today[closed_today["pnl"] > 0]),
        "losses": len(closed_today[closed_today["pnl"] < 0]),
        "daily_pnl": closed_today["pnl"].sum() if not closed_today.empty else 0.0,
        "win_rate": (len(closed_today[closed_today["pnl"] > 0]) / max(len(closed_today), 1)) * 100,
    }


# ── Header ───────────────────────────────────────────────────────────────────
col_title, col_time = st.columns([3, 1])
with col_title:
    st.markdown("# ⚡ TradeGOD Quant Fund — Live Dashboard")
with col_time:
    st.markdown(f"**UTC:** `{now_utc().strftime('%Y-%m-%d %H:%M:%S')}`")
    session = get_current_session()
    trading = is_trading_allowed()
    badge_class = "badge-green" if trading else "badge-red"
    st.markdown(f'<span class="status-badge {badge_class}">{"🟢 " if trading else "🔴 "}{session}</span>',
                unsafe_allow_html=True)

st.divider()

# ── Load Data ─────────────────────────────────────────────────────────────────
trades_df = load_trades()
stats     = get_daily_stats(trades_df)
feed      = get_mt5_feed()

acct_info = feed.get_account_info()
if acct_info:
    live_balance = acct_info["balance"]
    live_equity  = acct_info["equity"]
    live_profit  = acct_info["profit"]
    open_pos     = len(feed.get_open_positions())
else:
    live_balance = ACCOUNT_SIZE
    live_equity  = ACCOUNT_SIZE
    live_profit  = 0.0
    open_pos     = 0

# ── Row 1: Key Metrics ────────────────────────────────────────────────────────
c1, c2, c3, c4, c5, c6 = st.columns(6)

with c1:
    st.metric("💰 Live Balance", f"${live_balance:,.2f}")
with c2:
    st.metric("📈 Live Equity", f"${live_equity:,.2f}", f"{live_profit:+.2f}")
with c3:
    daily_pnl = stats["daily_pnl"] + live_profit
    kill_used = abs(daily_pnl) if daily_pnl < 0 else 0
    kill_pct  = (kill_used / DAILY_KILL_DOLLAR) * 100
    st.metric("🛡️ Kill Buffer",
              f"${DAILY_KILL_DOLLAR - kill_used:.0f}",
              f"{kill_pct:.0f}% used")
with c4:
    st.metric("📊 Open Positions", str(open_pos))
with c5:
    st.metric("✅ Wins Today",  str(stats["wins"]))
with c6:
    st.metric("❌ Losses Today", str(stats["losses"]))

st.divider()

# ── Row 2: Charts ─────────────────────────────────────────────────────────────
left, right = st.columns([2, 1])

with left:
    st.subheader("📊 Equity Curve")
    if not trades_df.empty:
        closed = trades_df[trades_df["status"] == "CLOSED"].copy()
        if not closed.empty:
            closed = closed.sort_values("close_time")
            closed["cumulative_pnl"] = closed["pnl"].cumsum()
            closed["equity"]         = ACCOUNT_SIZE + closed["cumulative_pnl"]

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=closed["close_time"],
                y=closed["equity"],
                mode="lines+markers",
                name="Equity",
                line=dict(color="#00d4aa", width=2),
                fill="tozeroy",
                fillcolor="rgba(0,212,170,0.1)"
            ))
            fig.add_hline(y=ACCOUNT_SIZE, line_dash="dash",
                          line_color="#888", annotation_text="Starting Balance")
            fig.add_hline(y=ACCOUNT_SIZE - DAILY_KILL_DOLLAR,
                          line_dash="dot", line_color="#ff4454",
                          annotation_text="Kill-Switch")
            fig.update_layout(
                paper_bgcolor="#0a0a0f",
                plot_bgcolor="#0a0a0f",
                font_color="#e0e0e0",
                height=300,
                showlegend=False,
                xaxis=dict(gridcolor="#1a1a2e"),
                yaxis=dict(gridcolor="#1a1a2e")
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No closed trades yet. Waiting for first signal...")
    else:
        st.info("Database empty. Start the bot to see data here.")

with right:
    st.subheader("🎯 Strategy Breakdown")
    if not trades_df.empty and "strategy" in trades_df.columns:
        strat_df = trades_df.groupby("strategy").agg(
            trades=("id", "count"),
            pnl=("pnl", "sum")
        ).reset_index()
        if not strat_df.empty:
            fig2 = px.bar(
                strat_df, x="strategy", y="pnl",
                color="pnl",
                color_continuous_scale=["#ff4454", "#00d4aa"],
            )
            fig2.update_layout(
                paper_bgcolor="#0a0a0f",
                plot_bgcolor="#0a0a0f",
                font_color="#e0e0e0",
                height=300,
                showlegend=False,
                xaxis_title="",
                yaxis_title="PnL ($)"
            )
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No strategy data yet.")

# ── Row 3: Trade Log ──────────────────────────────────────────────────────────
st.subheader("📋 Recent Trades")
if not trades_df.empty:
    display_cols = ["symbol", "direction", "lot_size", "entry_price",
                    "sl_price", "tp_price", "pnl", "status", "strategy", "open_time"]
    disp_cols = [c for c in display_cols if c in trades_df.columns]
    recent = trades_df.head(20)[disp_cols]

    # Color PnL column
    def color_pnl(val):
        if isinstance(val, (int, float)):
            color = "#00d4aa" if val >= 0 else "#ff4454"
            return f"color: {color}"
        return ""

    st.dataframe(
        recent.style.applymap(color_pnl, subset=["pnl"] if "pnl" in recent.columns else []),
        use_container_width=True,
        height=400
    )
else:
    st.info("No trades in database yet.")

# ── Auto-refresh every 30s ────────────────────────────────────────────────────
st.markdown("---")
st.caption("🔄 Auto-refreshes every 30 seconds | TradeGOD Quant Fund v2.0")

# Auto-refresh
from streamlit_autorefresh import st_autorefresh
try:
    st_autorefresh(interval=30000, key="dashboard_refresh")
except ImportError:
    st.caption("Install streamlit-autorefresh for auto-refresh: pip install streamlit-autorefresh")
