"""
TradeGOD -- FastAPI Live Dashboard (replaces Streamlit)
Run: python telemetry/web_dashboard.py
Open: http://localhost:8081
"""
import sys, json, sqlite3, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

from config.app_config import ACCOUNT_SIZE, DAILY_KILL_DOLLAR, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
from utils.time_ops import now_utc, get_current_session, is_trading_allowed, is_blocked_day
from utils.logger import get_logger

log = get_logger("WebDashboard")
DB_PATH    = Path(__file__).parent.parent / "database" / "trade_logs.db"
STATE_PATH = Path(__file__).parent.parent / "database" / "system_state.json"
app = FastAPI(title="TradeGOD Dashboard", docs_url=None)


def get_db_trades():
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades ORDER BY open_time DESC LIMIT 200").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_system_state():
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def build_data() -> dict:
    trades  = get_db_trades()
    state   = get_system_state()
    utc_now = now_utc()
    session = get_current_session()
    trading = is_trading_allowed()
    blocked = is_blocked_day()

    today_str    = utc_now.strftime("%Y-%m-%d")
    today_trades = [t for t in trades if (t.get("open_time") or "").startswith(today_str)]
    closed_today = [t for t in today_trades if t.get("status") == "CLOSED" and t.get("pnl") is not None]
    open_today   = [t for t in today_trades if t.get("status") == "OPEN"]

    wins    = [t for t in closed_today if (t.get("pnl") or 0) > 0]
    losses  = [t for t in closed_today if (t.get("pnl") or 0) <= 0]

    kill_triggered  = state.get("kill_triggered", False)
    state_daily_pnl = state.get("daily_pnl", sum(t.get("pnl", 0) or 0 for t in closed_today))
    kill_used = abs(state_daily_pnl) if state_daily_pnl < 0 else 0
    kill_pct  = min(round((kill_used / DAILY_KILL_DOLLAR) * 100, 1), 100)

    strat_map: dict = {}
    for t in trades:
        s = t.get("strategy", "unknown") or "unknown"
        if s not in strat_map:
            strat_map[s] = {"trades": 0, "pnl": 0.0, "wins": 0}
        strat_map[s]["trades"] += 1
        strat_map[s]["pnl"] += t.get("pnl", 0) or 0
        if (t.get("pnl", 0) or 0) > 0:
            strat_map[s]["wins"] += 1

    closed_all = sorted(
        [t for t in trades if t.get("status") == "CLOSED" and t.get("pnl") is not None],
        key=lambda x: x.get("close_time") or ""
    )
    equity_pts   = [ACCOUNT_SIZE]
    equity_times = ["Start"]
    for t in closed_all:
        equity_pts.append(round(equity_pts[-1] + (t.get("pnl") or 0), 2))
        equity_times.append((t.get("close_time") or "")[:16])

    floating_pnl  = sum(t.get("pnl", 0) or 0 for t in open_today)
    realized_total = sum(t.get("pnl", 0) or 0 for t in closed_all)

    return {
        "ts":           utc_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "session":      session,
        "trading_ok":   trading and not blocked and not kill_triggered,
        "blocked_day":  blocked,
        "kill_active":  kill_triggered,
        "account_size": ACCOUNT_SIZE,
        "balance":      round(ACCOUNT_SIZE + realized_total, 2),
        "equity":       round(ACCOUNT_SIZE + realized_total + floating_pnl, 2),
        "floating_pnl": round(floating_pnl, 2),
        "daily_pnl":    round(state_daily_pnl, 2),
        "kill_buffer":  round(DAILY_KILL_DOLLAR - kill_used, 2),
        "kill_pct_used": kill_pct,
        "daily_trades": state.get("daily_trades", len(today_trades)),
        "wins_today":   len(wins),
        "losses_today": len(losses),
        "open_positions": len(open_today),
        "win_rate_today": round(len(wins) / max(len(closed_today), 1) * 100, 1),
        "phase1_target":  400.0,
        "phase1_progress": round(realized_total, 2),
        "equity_curve":  {"x": equity_times, "y": equity_pts},
        "strategy_breakdown": [
            {"name": k, "trades": v["trades"], "pnl": round(v["pnl"], 2),
             "winrate": round(v["wins"] / max(v["trades"], 1) * 100, 1)}
            for k, v in strat_map.items()
        ],
        "recent_trades": trades[:20],
    }


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradeGOD Live Dashboard</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#080810;--bg2:#0e0e1a;--bg3:#141428;--border:#1e1e3a;--green:#00d4aa;--green2:rgba(0,212,170,0.15);--red:#ff4454;--red2:rgba(255,68,84,0.15);--yellow:#ffa500;--blue:#4f8ef7;--text:#e0e0e0;--muted:#666;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-size:14px;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;padding:16px 28px;border-bottom:1px solid var(--border);background:linear-gradient(90deg,#080810,#0e0e1a);position:sticky;top:0;z-index:100;box-shadow:0 2px 20px rgba(0,0,0,0.6)}
.logo{font-size:1.4rem;font-weight:800;letter-spacing:-0.5px}.logo span{color:var(--green)}
.header-right{display:flex;align-items:center;gap:16px}
.ws-dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}.ws-dot.connected{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
#ts{font-size:0.75rem;color:var(--muted);font-family:'Fira Code',monospace}
.badge{padding:4px 12px;border-radius:20px;font-size:0.7rem;font-weight:700;letter-spacing:0.5px;text-transform:uppercase}
.badge.green{background:var(--green2);color:var(--green);border:1px solid rgba(0,212,170,0.3)}
.badge.red{background:var(--red2);color:var(--red);border:1px solid rgba(255,68,84,0.3)}
.badge.yellow{background:rgba(255,165,0,0.15);color:var(--yellow);border:1px solid rgba(255,165,0,0.3)}
.container{max-width:1600px;margin:0 auto;padding:24px 28px}
.metrics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:20px 22px;transition:border-color 0.3s,box-shadow 0.3s}
.card:hover{border-color:#2a2a4a;box-shadow:0 0 20px rgba(0,212,170,0.1)}
.card-label{font-size:0.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.card-value{font-size:1.8rem;font-weight:700;line-height:1;font-variant-numeric:tabular-nums;font-family:'Fira Code',monospace}
.card-sub{font-size:0.75rem;color:var(--muted);margin-top:6px}
.green-val{color:var(--green)}.red-val{color:var(--red)}.white-val{color:#fff}
.progress-bar-bg{background:var(--bg3);border-radius:4px;height:6px;margin-top:8px;overflow:hidden}
.progress-bar{height:6px;border-radius:4px;transition:width 0.5s}
.progress-green{background:var(--green)}.progress-yellow{background:var(--yellow)}.progress-red{background:var(--red)}
.charts-row{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:24px}
.chart-card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:20px}
.chart-title{font-size:0.85rem;font-weight:600;color:var(--green);margin-bottom:16px}
.trades-section{background:var(--bg2);border:1px solid var(--border);border-radius:14px;padding:20px;margin-bottom:24px}
table{width:100%;border-collapse:collapse;font-size:0.8rem}
thead th{color:var(--muted);font-weight:500;padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.5px}
tbody tr{border-bottom:1px solid rgba(255,255,255,0.03);transition:background 0.15s}
tbody tr:hover{background:rgba(255,255,255,0.02)}
td{padding:10px 12px;font-family:'Fira Code',monospace}
.dir-buy{color:var(--green);font-weight:600}.dir-sell{color:var(--red);font-weight:600}
.pnl-pos{color:var(--green)}.pnl-neg{color:var(--red)}
.status-open{color:var(--blue)}.status-closed{color:var(--muted)}
.kill-alert{background:var(--red2);border:1px solid rgba(255,68,84,0.4);border-radius:12px;padding:14px 20px;margin-bottom:24px;display:none;align-items:center;gap:12px;font-weight:600;color:var(--red)}
.kill-alert.active{display:flex}
@media(max-width:900px){.charts-row{grid-template-columns:1fr}.metrics-grid{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header>
  <div class="logo">&#9889; Trade<span>GOD</span></div>
  <div class="header-right">
    <span id="ts">--:--:-- UTC</span>
    <span id="session-badge" class="badge green">--</span>
    <div class="ws-dot" id="wsDot"></div>
  </div>
</header>
<div class="container">
  <div class="kill-alert" id="killAlert">&#128680; DAILY KILL-SWITCH ACTIVE &mdash; All trading disabled. Resets midnight UTC.</div>
  <div class="metrics-grid">
    <div class="card"><div class="card-label">&#128176; Prop Balance (5K Base)</div><div class="card-value green-val" id="balance">$5,000.00</div><div class="card-sub">Funding Pips 5K Account</div></div>
    <div class="card"><div class="card-label">&#128200; Equity (+ Floating)</div><div class="card-value white-val" id="equity">$5,000.00</div><div class="card-sub" id="floatingPnl">Floating: $0.00</div></div>
    <div class="card"><div class="card-label">&#128197; Daily P&L</div><div class="card-value" id="dailyPnl">$0.00</div><div class="card-sub" id="killPct">Kill buffer: $225 (0% used)</div><div class="progress-bar-bg"><div class="progress-bar progress-green" id="killBar" style="width:0%"></div></div></div>
    <div class="card"><div class="card-label">&#127919; Phase 1 Progress</div><div class="card-value white-val" id="phase1">$0.00</div><div class="card-sub" id="phase1sub">Target: +$400 (0%)</div><div class="progress-bar-bg"><div class="progress-bar progress-green" id="phaseBar" style="width:0%"></div></div></div>
    <div class="card"><div class="card-label">&#128202; Open Positions</div><div class="card-value white-val" id="openPos">0</div><div class="card-sub" id="dailyTradesCount">0 / 2 trades today</div></div>
    <div class="card"><div class="card-label">&#9989; Wins Today</div><div class="card-value green-val" id="winsToday">0</div><div class="card-sub" id="winRateToday">Win rate: --%</div></div>
    <div class="card"><div class="card-label">&#10060; Losses Today</div><div class="card-value red-val" id="lossesToday">0</div><div class="card-sub">Max 2 trades/day</div></div>
    <div class="card"><div class="card-label">&#9889; Session Status</div><div class="card-value white-val" id="sessionVal">--</div><div class="card-sub" id="sessionSub">Checking...</div></div>
  </div>
  <div class="charts-row">
    <div class="chart-card"><div class="chart-title">&#128202; Equity Curve (All Closed Trades)</div><div id="equityChart" style="height:300px;"></div></div>
    <div class="chart-card"><div class="chart-title">&#127919; Strategy P&L Breakdown</div><div id="stratChart" style="height:300px;"></div></div>
  </div>
  <div class="trades-section">
    <div class="chart-title">&#128203; Recent Trades (Last 20)</div>
    <div style="overflow-x:auto;"><table>
      <thead><tr><th>Symbol</th><th>Dir</th><th>Strategy</th><th>Lots</th><th>Entry</th><th>SL</th><th>TP</th><th>PnL</th><th>Status</th><th>Opened</th></tr></thead>
      <tbody id="tradeLog"><tr><td colspan="10" style="text-align:center;color:var(--muted);padding:30px">Waiting for trades... Start the bot to see live data.</td></tr></tbody>
    </table></div>
  </div>
  <div style="text-align:center;color:var(--muted);font-size:0.7rem;padding-bottom:24px">TradeGOD Quant Fund v2.0 &mdash; FastAPI WebSocket Dashboard &mdash; Updates every 3s</div>
</div>
<script>
const $=id=>document.getElementById(id);
function fmtUSD(n){if(n==null)return'--';return'$'+Number(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g,',');}
function fmtPnL(n){if(n==null)return'--';return(n>=0?'+$':'-$')+Math.abs(n).toFixed(2);}
let eq=null,st=null;
function renderEquity(d){
  const c=d.equity_curve;if(!c||c.y.length<2)return;
  const traces=[
    {x:c.x,y:c.y,type:'scatter',mode:'lines',name:'Equity',line:{color:'#00d4aa',width:2},fill:'tozeroy',fillcolor:'rgba(0,212,170,0.05)'},
    {x:[c.x[0],c.x[c.x.length-1]],y:[5000,5000],type:'scatter',mode:'lines',name:'Start',line:{color:'#888',width:1,dash:'dash'}},
    {x:[c.x[0],c.x[c.x.length-1]],y:[4775,4775],type:'scatter',mode:'lines',name:'Kill-Switch',line:{color:'#ff4454',width:1,dash:'dot'}}
  ];
  const layout={paper_bgcolor:'#0e0e1a',plot_bgcolor:'#0e0e1a',font:{color:'#e0e0e0',family:'Inter',size:11},margin:{l:50,r:10,t:10,b:40},xaxis:{gridcolor:'#1e1e3a'},yaxis:{gridcolor:'#1e1e3a',tickprefix:'$'},showlegend:true,legend:{x:0,y:1,font:{size:10}}};
  if(!eq){Plotly.newPlot('equityChart',traces,layout,{responsive:true,displayModeBar:false});eq=true;}
  else{Plotly.update('equityChart',{x:[c.x],y:[c.y]},[],0);}
}
function renderStrat(d){
  const sb=d.strategy_breakdown;if(!sb||!sb.length)return;
  const names=sb.map(s=>s.name),pnls=sb.map(s=>s.pnl),colors=pnls.map(p=>p>=0?'#00d4aa':'#ff4454');
  const layout={paper_bgcolor:'#0e0e1a',plot_bgcolor:'#0e0e1a',font:{color:'#e0e0e0',family:'Inter',size:11},margin:{l:50,r:10,t:10,b:60},xaxis:{gridcolor:'#1e1e3a'},yaxis:{gridcolor:'#1e1e3a',tickprefix:'$'},showlegend:false};
  if(!st){Plotly.newPlot('stratChart',[{x:names,y:pnls,type:'bar',marker:{color:colors}}],layout,{responsive:true,displayModeBar:false});st=true;}
  else{Plotly.restyle('stratChart',{x:[names],y:[pnls],'marker.color':[colors]},[0]);}
}
function renderTrades(d){
  const t=d.recent_trades||[];if(!t.length)return;
  $('tradeLog').innerHTML=t.map(r=>{
    const p=r.pnl,pc=p==null?'':p>=0?'pnl-pos':'pnl-neg';
    const dir=(r.direction||'').toUpperCase(),dc=dir==='BUY'?'dir-buy':'dir-sell';
    const st=(r.status||'').toUpperCase(),sc=st==='OPEN'?'status-open':'status-closed';
    return`<tr><td>${r.symbol||'--'}</td><td class="${dc}">${dir}</td><td>${r.strategy||'--'}</td><td>${(r.lot_size||0).toFixed(2)}</td><td>${(r.entry_price||0).toFixed(5)}</td><td>${(r.sl_price||0).toFixed(5)}</td><td>${(r.tp_price||0).toFixed(5)}</td><td class="${pc}">${fmtPnL(p)}</td><td class="${sc}">${st}</td><td>${(r.open_time||'--').slice(5,16)}</td></tr>`;
  }).join('');
}
function update(d){
  $('ts').textContent=d.ts||'--';
  const sb=$('session-badge');sb.textContent=d.session||'--';sb.className='badge '+(d.trading_ok?'green':d.blocked_day?'yellow':'red');
  $('balance').textContent=fmtUSD(d.balance);$('balance').className='card-value '+((d.balance||5000)>=5000?'green-val':'red-val');
  $('equity').textContent=fmtUSD(d.equity);
  const fp=d.floating_pnl||0;$('floatingPnl').textContent='Floating: '+(fp>=0?'+':'')+'$'+Math.abs(fp).toFixed(2);$('floatingPnl').style.color=fp>=0?'var(--green)':'var(--red)';
  const dp=d.daily_pnl||0;$('dailyPnl').textContent=(dp>=0?'+$':'-$')+Math.abs(dp).toFixed(2);$('dailyPnl').className='card-value '+(dp>=0?'green-val':'red-val');
  const kp=d.kill_pct_used||0;$('killPct').textContent='Kill buffer: $'+(d.kill_buffer||225).toFixed(0)+' ('+kp+'% used)';
  const bar=$('killBar');bar.style.width=kp+'%';bar.className='progress-bar '+(kp<50?'progress-green':kp<80?'progress-yellow':'progress-red');
  const pp=d.phase1_progress||0,ppct=Math.min((pp/400)*100,100).toFixed(1);
  $('phase1').textContent=(pp>=0?'+$':'-$')+Math.abs(pp).toFixed(2);$('phase1sub').textContent='Target: +$400 ('+ppct+'% complete)';$('phaseBar').style.width=ppct+'%';
  $('openPos').textContent=d.open_positions||0;$('dailyTradesCount').textContent=(d.daily_trades||0)+' / 2 trades today';
  $('winsToday').textContent=d.wins_today||0;$('lossesToday').textContent=d.losses_today||0;$('winRateToday').textContent='Win rate: '+(d.win_rate_today||0)+'%';
  $('sessionVal').textContent=d.session||'--';
  if(d.kill_active){$('sessionSub').textContent='KILL-SWITCH ACTIVE';$('sessionSub').style.color='var(--red)';}
  else if(d.blocked_day){$('sessionSub').textContent='Blocked day (Mon/Fri)';$('sessionSub').style.color='var(--yellow)';}
  else if(d.trading_ok){$('sessionSub').textContent='Trading allowed';$('sessionSub').style.color='var(--green)';}
  else{$('sessionSub').textContent='Outside trading hours';$('sessionSub').style.color='var(--muted)';}
  d.kill_active?$('killAlert').classList.add('active'):$('killAlert').classList.remove('active');
}
function connectWS(){
  const ws=new WebSocket('ws://'+location.host+'/ws/live');
  ws.onopen=()=>$('wsDot').classList.add('connected');
  ws.onmessage=e=>{try{const d=JSON.parse(e.data);update(d);renderEquity(d);renderStrat(d);renderTrades(d);}catch(err){console.error(err);}};
  ws.onclose=()=>{$('wsDot').classList.remove('connected');setTimeout(connectWS,5000);};
}
fetch('/api/data').then(r=>r.json()).then(d=>{update(d);renderEquity(d);renderStrat(d);renderTrades(d);}).catch(console.error);
connectWS();
</script>
</body>
</html>"""


@app.get("/api/data")
async def api_data():
    return build_data()


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await asyncio.get_event_loop().run_in_executor(None, build_data)
            await ws.send_json(data)
            await asyncio.sleep(3)
    except (WebSocketDisconnect, Exception):
        pass


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=HTML)


if __name__ == "__main__":
    print("\n" + "="*55)
    print("  TradeGOD Live Dashboard -- FastAPI + WebSocket")
    print("  Open: http://localhost:8081")
    print("  Account Base: $5,000 (Funding Pips 5K Prop)")
    print("  Live update: every 3 seconds via WebSocket")
    print("=" * 55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
