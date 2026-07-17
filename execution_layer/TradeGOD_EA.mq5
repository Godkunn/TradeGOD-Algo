//+------------------------------------------------------------------+
//|  TradeGOD_EA.mq5                                                 |
//|  Execution Muscle — Receives JSON commands via ZeroMQ            |
//|  Calibrated for Funding Pips $5K Account                         |
//+------------------------------------------------------------------+
#property copyright "TradeGOD Quant Fund"
#property version   "2.0"
#property strict

// ZeroMQ includes (requires ZeroMQ DLL in MT5/Libraries/)
// Download: https://github.com/dingmaotu/mql-zmq/releases
#include <Zmq/Zmq.mqh>
#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>

//──── Input Parameters ──────────────────────────────────────────────────────
input int    InpPushPort     = 5555;    // ZMQ PULL port (receive from Python)
input int    InpPullPort     = 5556;    // ZMQ PUSH port (send confirmations)
input double InpMaxDailyLoss = 225.0;  // Kill-switch: $225 = 4.5% of $5K
input double InpMaxTotalLoss = 450.0;  // Max drawdown: $450 = 9% of $5K
input int    InpMagic        = 777999; // Bot identity tag
input int    InpSlippage     = 30;     // Max slippage in points (3 pips)
input bool   InpDebugMode    = true;   // Print debug messages

//──── ZMQ Objects ────────────────────────────────────────────────────────────
Context g_ctx("TradeGOD");
Socket  g_pull(g_ctx, ZMQ_PULL);
Socket  g_push(g_ctx, ZMQ_PUSH);

//──── Trade Object ───────────────────────────────────────────────────────────
CTrade g_trade;

//──── State Variables ────────────────────────────────────────────────────────
double g_start_balance     = 0.0;
double g_start_of_day_bal  = 0.0;
int    g_last_day          = -1;
bool   g_kill_triggered    = false;
int    g_keepalive_ticket  = 0;
long   g_keepalive_open_time = 0;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
    g_trade.SetExpertMagicNumber(InpMagic);
    g_trade.SetDeviationInPoints(InpSlippage);
    g_trade.SetTypeFilling(ORDER_FILLING_FOK);

    // Connect ZMQ sockets
    g_pull.setReceiveHighWaterMark(100);
    if(!g_pull.connect("tcp://127.0.0.1:" + IntegerToString(InpPushPort)))
    {
        Print("❌ Failed to connect PULL socket on port ", InpPushPort);
        return INIT_FAILED;
    }

    g_push.setSendHighWaterMark(100);
    if(!g_push.connect("tcp://127.0.0.1:" + IntegerToString(InpPullPort)))
    {
        Print("❌ Failed to connect PUSH socket on port ", InpPullPort);
        return INIT_FAILED;
    }

    g_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
    Print("✅ TradeGOD EA Initialized | Balance=$", g_start_balance,
          " | Magic=", InpMagic);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    g_pull.disconnect("tcp://127.0.0.1:" + IntegerToString(InpPushPort));
    g_push.disconnect("tcp://127.0.0.1:" + IntegerToString(InpPullPort));
    Print("TradeGOD EA shut down. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick handler                                               |
//+------------------------------------------------------------------+
void OnTick()
{
    // 1. Daily reset
    CheckDailyReset();

    // 2. Kill-switch check (every tick)
    if(CheckKillSwitch())
        return;

    // 3. Weekend close
    if(IsFridayClose())
    {
        CloseAllPositions("weekend_close");
        return;
    }

    // 4. KeepAlive auto-close
    CheckKeepaliveClose();

    // 5. Process ZMQ messages
    ZmqMsg msg;
    while(g_pull.recv(msg, true))  // Non-blocking
    {
        string payload = msg.getData();
        if(InpDebugMode) Print("📥 Received: ", payload);
        ProcessCommand(payload);
    }
}

//+------------------------------------------------------------------+
//| Daily Balance Snapshot                                            |
//+------------------------------------------------------------------+
void CheckDailyReset()
{
    MqlDateTime dt;
    TimeCurrent(dt);
    if(dt.day != g_last_day)
    {
        g_start_of_day_bal = AccountInfoDouble(ACCOUNT_BALANCE);
        g_kill_triggered   = false;
        g_last_day         = dt.day;
        Print("📅 New day — Balance snapshot: $", g_start_of_day_bal);
    }
}

//+------------------------------------------------------------------+
//| Kill-Switch: Block trading if daily loss >= $225                  |
//+------------------------------------------------------------------+
bool CheckKillSwitch()
{
    if(g_kill_triggered) return true;

    double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
    double daily_loss = g_start_of_day_bal - equity;
    double total_loss = g_start_balance - AccountInfoDouble(ACCOUNT_BALANCE);

    if(daily_loss >= InpMaxDailyLoss)
    {
        g_kill_triggered = true;
        CloseAllPositions("daily_kill_switch");
        Print("🚨 DAILY KILL-SWITCH: loss=$", daily_loss,
              " >= $", InpMaxDailyLoss);
        SendConfirmation(0, "KILL_SWITCH", "EURUSD", 0.0,
                         "Daily kill-switch triggered $" + DoubleToString(daily_loss,2));
        return true;
    }

    if(total_loss >= InpMaxTotalLoss)
    {
        g_kill_triggered = true;
        CloseAllPositions("max_drawdown");
        Print("🚨 MAX DRAWDOWN: total loss=$", total_loss);
        return true;
    }

    return false;
}

//+------------------------------------------------------------------+
//| Friday close: Friday >= 21:00 UTC                                 |
//+------------------------------------------------------------------+
bool IsFridayClose()
{
    MqlDateTime dt;
    TimeGMT(dt);
    return (dt.day_of_week == 5 && dt.hour >= 21);
}

//+------------------------------------------------------------------+
//| KeepAlive trade auto-close after 185 seconds                     |
//+------------------------------------------------------------------+
void CheckKeepaliveClose()
{
    if(g_keepalive_ticket == 0) return;

    long elapsed = TimeCurrent() - g_keepalive_open_time;
    if(elapsed >= 185)  // 185 seconds > 180s minimum hold
    {
        if(PositionSelectByTicket(g_keepalive_ticket))
        {
            g_trade.PositionClose(g_keepalive_ticket);
            Print("✅ KeepAlive trade closed (held ", elapsed, "s)");
        }
        g_keepalive_ticket = 0;
        g_keepalive_open_time = 0;
    }
}

//+------------------------------------------------------------------+
//| Parse and route JSON command                                      |
//+------------------------------------------------------------------+
void ProcessCommand(string json)
{
    string action   = JsonGetString(json, "action");
    string symbol   = JsonGetString(json, "symbol");
    string dir      = JsonGetString(json, "direction");
    double lot      = JsonGetDouble(json, "lot_size");
    double entry    = JsonGetDouble(json, "entry_price");
    double sl       = JsonGetDouble(json, "sl_price");
    double tp       = JsonGetDouble(json, "tp_price");
    int    ticket   = (int)JsonGetDouble(json, "ticket");
    double new_sl   = JsonGetDouble(json, "sl_price");
    int    hold_sec = (int)JsonGetDouble(json, "hold_seconds");
    string comment  = JsonGetString(json, "comment");

    if(action == "OPEN")        ExecuteOpen(symbol, dir, lot, entry, sl, tp, comment);
    else if(action == "CLOSE")  ExecuteClose(ticket, symbol);
    else if(action == "MODIFY") ExecuteModifySL(ticket, symbol, new_sl, tp);
    else if(action == "CLOSE_ALL") CloseAllPositions(comment);
    else if(action == "KEEPALIVE") ExecuteKeepalive(symbol, lot);
    else Print("⚠️ Unknown action: ", action);
}

//+------------------------------------------------------------------+
//| Open a new position                                               |
//+------------------------------------------------------------------+
void ExecuteOpen(string symbol, string direction, double lot,
                  double entry_price, double sl, double tp, string comment)
{
    if(g_kill_triggered)
    {
        Print("⛔ Trade blocked — kill-switch active");
        return;
    }

    // Validate spread
    MqlTick tick;
    if(!SymbolInfoTick(symbol, tick)) return;
    double spread_pips = (tick.ask - tick.bid) / SymbolInfoDouble(symbol, SYMBOL_POINT) / 10;
    if(spread_pips > 10.0)
    {
        Print("⚠️ Spread too wide: ", spread_pips, " pips — trade blocked");
        return;
    }

    // Check existing positions for this symbol (max 1)
    int symbol_positions = 0;
    for(int i = 0; i < PositionsTotal(); i++)
    {
        if(PositionGetSymbol(i) == symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic)
            symbol_positions++;
    }
    if(symbol_positions >= 1)
    {
        Print("⚠️ Already have position on ", symbol, " — skipping");
        return;
    }

    ENUM_ORDER_TYPE order_type;
    double price;

    if(direction == "BUY")
    {
        order_type = ORDER_TYPE_BUY;
        price      = (entry_price > 0) ? entry_price : tick.ask;
    }
    else
    {
        order_type = ORDER_TYPE_SELL;
        price      = (entry_price > 0) ? entry_price : tick.bid;
    }

    // Normalize
    int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
    price = NormalizeDouble(price, digits);
    sl    = NormalizeDouble(sl,    digits);
    tp    = NormalizeDouble(tp,    digits);
    lot   = NormalizeDouble(lot,   2);

    bool success = g_trade.PositionOpen(symbol, order_type, lot, price, sl, tp,
                                         "TG|" + comment);
    if(success)
    {
        ulong ticket = g_trade.ResultOrder();
        Print("✅ Order opened: ", symbol, " ", direction, " ", lot, " lots ",
              "@ ", price, " SL=", sl, " TP=", tp, " #", ticket);
        SendConfirmation((int)ticket, "OPEN", symbol, price, "");
    }
    else
    {
        int err = (int)g_trade.ResultRetcode();
        Print("❌ Order failed: ", g_trade.ResultRetcodeDescription());
        SendConfirmation(0, "OPEN_ERROR", symbol, 0.0,
                         g_trade.ResultRetcodeDescription());
    }
}

//+------------------------------------------------------------------+
//| Close a specific position by ticket                               |
//+------------------------------------------------------------------+
void ExecuteClose(int ticket, string symbol)
{
    if(!PositionSelectByTicket(ticket))
    {
        Print("⚠️ Position #", ticket, " not found");
        return;
    }

    bool success = g_trade.PositionClose(ticket, InpSlippage);
    if(success)
    {
        Print("✅ Position #", ticket, " closed");
        SendConfirmation(ticket, "CLOSE", symbol,
                         g_trade.ResultPrice(), "");
    }
    else
    {
        Print("❌ Close failed: ", g_trade.ResultRetcodeDescription());
    }
}

//+------------------------------------------------------------------+
//| Modify SL/TP of existing position                                 |
//+------------------------------------------------------------------+
void ExecuteModifySL(int ticket, string symbol, double new_sl, double new_tp)
{
    if(!PositionSelectByTicket(ticket)) return;
    int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
    new_sl = NormalizeDouble(new_sl, digits);
    new_tp = NormalizeDouble(new_tp, digits);
    bool success = g_trade.PositionModify(ticket, new_sl, new_tp);
    if(success)
        Print("✅ Modified #", ticket, " SL=", new_sl, " TP=", new_tp);
    else
        Print("❌ Modify failed: ", g_trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Close all open positions                                          |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{
    Print("🔴 Closing ALL positions. Reason: ", reason);
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(PositionGetInteger(POSITION_MAGIC) == InpMagic)
            g_trade.PositionClose(ticket);
    }
}

//+------------------------------------------------------------------+
//| KeepAlive maintenance trade                                       |
//+------------------------------------------------------------------+
void ExecuteKeepalive(string symbol, double lot)
{
    if(g_keepalive_ticket != 0) return;  // Already have one

    MqlTick tick;
    if(!SymbolInfoTick(symbol, tick)) return;

    bool success = g_trade.Buy(lot, symbol, tick.ask, 0, 0, "KEEPALIVE");
    if(success)
    {
        g_keepalive_ticket    = (int)g_trade.ResultOrder();
        g_keepalive_open_time = TimeCurrent();
        Print("🟡 KeepAlive trade opened: #", g_keepalive_ticket,
              " will auto-close in 185s");
    }
}

//+------------------------------------------------------------------+
//| Send confirmation back to Python                                  |
//+------------------------------------------------------------------+
void SendConfirmation(int ticket, string action, string symbol,
                       double price, string error_msg)
{
    string status = (error_msg == "") ? "OK" : "ERROR";
    string payload = StringFormat(
        "{\"status\":\"%s\",\"ticket\":%d,\"action\":\"%s\","
        "\"symbol\":\"%s\",\"price\":%.5f,\"error\":\"%s\"}",
        status, ticket, action, symbol, price, error_msg
    );
    ZmqMsg reply(payload);
    g_push.send(reply, true);
}

//+------------------------------------------------------------------+
//| Minimal JSON parser helpers                                       |
//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
{
    string search = "\"" + key + "\":\"";
    int start = StringFind(json, search);
    if(start < 0) return "";
    start += StringLen(search);
    int end = StringFind(json, "\"", start);
    if(end < 0) return "";
    return StringSubstr(json, start, end - start);
}

double JsonGetDouble(string json, string key)
{
    string search = "\"" + key + "\":";
    int start = StringFind(json, search);
    if(start < 0) return 0.0;
    start += StringLen(search);
    // Skip opening quote if string number
    if(StringSubstr(json, start, 1) == "\"") start++;
    int end = start;
    string chars = "0123456789.-+eE";
    while(end < StringLen(json) && StringFind(chars, StringSubstr(json, end, 1)) >= 0)
        end++;
    string val = StringSubstr(json, start, end - start);
    return StringToDouble(val);
}
