"""
TradeGOD — ZeroMQ Bridge
Python → MQL5 communication layer.
Sends JSON trade commands to the MetaTrader 5 EA via TCP.
"""

import zmq
import json
import time
from typing import Optional
from utils.logger import get_logger
from utils.time_ops import now_utc

log = get_logger("ZMQBridge")


class ZMQBridge:
    """
    ZeroMQ PUSH/PULL socket pair for Python ↔ MQL5 communication.
    
    Architecture:
      Python (PUSH) → MQL5 EA (PULL): Trade commands
      MQL5 EA (PUSH) → Python (PULL): Execution confirmations
    """

    def __init__(self, push_port: int = 5555, pull_port: int = 5556):
        self.push_port = push_port
        self.pull_port = pull_port
        self._ctx: Optional[zmq.Context] = None
        self._push_socket: Optional[zmq.Socket] = None
        self._pull_socket: Optional[zmq.Socket] = None
        self._connected = False

    def connect(self) -> bool:
        """Initialize ZMQ sockets."""
        try:
            self._ctx = zmq.Context()

            # PUSH: Python → MT5 (trade commands)
            self._push_socket = self._ctx.socket(zmq.PUSH)
            self._push_socket.setsockopt(zmq.SNDHWM, 1)
            self._push_socket.setsockopt(zmq.LINGER, 0)
            self._push_socket.bind(f"tcp://127.0.0.1:{self.push_port}")

            # PULL: MT5 → Python (confirmations)
            self._pull_socket = self._ctx.socket(zmq.PULL)
            self._pull_socket.setsockopt(zmq.RCVHWM, 10)
            self._pull_socket.setsockopt(zmq.RCVTIMEO, 2000)  # 2s timeout
            self._pull_socket.bind(f"tcp://127.0.0.1:{self.pull_port}")

            self._connected = True
            log.info(f"✅ ZMQ Bridge ready — PUSH:{self.push_port}, PULL:{self.pull_port}")
            return True

        except zmq.ZMQError as e:
            log.error(f"ZMQ connection failed: {e}")
            return False

    def disconnect(self):
        """Clean shutdown."""
        if self._push_socket:
            self._push_socket.close()
        if self._pull_socket:
            self._pull_socket.close()
        if self._ctx:
            self._ctx.term()
        self._connected = False
        log.info("ZMQ Bridge disconnected.")

    # ══════════════════════════════════════════════════════════════════════════
    # COMMAND SENDERS
    # ══════════════════════════════════════════════════════════════════════════

    def send_trade_command(self, command: dict) -> bool:
        """
        Send a JSON trade command to the MQL5 EA.
        
        Command schema:
        {
            "action": "OPEN" | "CLOSE" | "MODIFY" | "CLOSE_ALL" | "KEEPALIVE",
            "symbol": "EURUSD",
            "direction": "BUY" | "SELL",
            "lot_size": 0.05,
            "entry_price": 1.08500,     # 0 = market execution
            "sl_price": 1.08200,
            "tp_price": 1.09100,
            "magic": 777999,
            "comment": "S02_SMC_OB",
            "ticket": 0,                # For CLOSE/MODIFY
            "timestamp": "2024-01-15T08:30:00"
        }
        """
        if not self._connected:
            log.error("ZMQ not connected. Cannot send command.")
            return False

        command["timestamp"] = now_utc().isoformat()
        payload = json.dumps(command)

        try:
            self._push_socket.send_string(payload, zmq.NOBLOCK)
            log.info(f"📤 ZMQ → MT5: {command.get('action')} {command.get('symbol','')}")
            return True
        except zmq.ZMQError as e:
            log.error(f"ZMQ send failed: {e}")
            return False

    def send_open_buy(self, symbol: str, lot: float, entry: float,
                       sl: float, tp: float, magic: int, comment: str) -> bool:
        return self.send_trade_command({
            "action": "OPEN", "symbol": symbol, "direction": "BUY",
            "lot_size": lot, "entry_price": entry,
            "sl_price": sl, "tp_price": tp,
            "magic": magic, "comment": comment, "ticket": 0
        })

    def send_open_sell(self, symbol: str, lot: float, entry: float,
                        sl: float, tp: float, magic: int, comment: str) -> bool:
        return self.send_trade_command({
            "action": "OPEN", "symbol": symbol, "direction": "SELL",
            "lot_size": lot, "entry_price": entry,
            "sl_price": sl, "tp_price": tp,
            "magic": magic, "comment": comment, "ticket": 0
        })

    def send_close_position(self, ticket: int, symbol: str) -> bool:
        return self.send_trade_command({
            "action": "CLOSE", "ticket": ticket, "symbol": symbol,
            "magic": 0, "comment": "Python_close"
        })

    def send_close_all(self, reason: str = "kill_switch") -> bool:
        return self.send_trade_command({
            "action": "CLOSE_ALL", "comment": reason,
            "magic": 0, "symbol": ""
        })

    def send_modify_sl(self, ticket: int, symbol: str,
                        new_sl: float, new_tp: float = 0.0) -> bool:
        return self.send_trade_command({
            "action": "MODIFY", "ticket": ticket, "symbol": symbol,
            "sl_price": new_sl, "tp_price": new_tp,
            "magic": 0, "comment": "SL_modify"
        })

    def send_breakeven(self, ticket: int, symbol: str,
                        entry_price: float, spread_pips: float = 1.5,
                        pip_size: float = 0.0001) -> bool:
        """Move SL to breakeven + small spread buffer."""
        be_sl = round(entry_price + spread_pips * pip_size, 5)
        return self.send_modify_sl(ticket, symbol, be_sl)

    def send_keepalive_trade(self, symbol: str = "EURUSD",
                              magic: int = 777999) -> bool:
        """
        Account maintenance trade:
        Open 0.01 lot, let EA close after 185 seconds automatically.
        (185s > 180s minimum hold time → fully compliant with HFT rules)
        """
        return self.send_trade_command({
            "action": "KEEPALIVE",
            "symbol": symbol,
            "lot_size": 0.01,
            "magic": magic,
            "comment": "KEEPALIVE_maintenance",
            "hold_seconds": 185
        })

    # ══════════════════════════════════════════════════════════════════════════
    # CONFIRMATION RECEIVER
    # ══════════════════════════════════════════════════════════════════════════

    def receive_confirmation(self, timeout_ms: int = 2000) -> Optional[dict]:
        """
        Non-blocking receive of EA confirmation.
        Returns parsed JSON dict or None.
        
        EA sends back:
        {"status": "OK" | "ERROR", "ticket": 12345, "action": "OPEN",
         "symbol": "EURUSD", "price": 1.08500, "error": ""}
        """
        if not self._connected:
            return None
        try:
            self._pull_socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
            msg = self._pull_socket.recv_string()
            data = json.loads(msg)
            status = data.get("status", "?")
            if status == "OK":
                log.info(f"✅ MT5 confirmation: ticket={data.get('ticket')} "
                         f"action={data.get('action')} @ {data.get('price')}")
            else:
                log.error(f"❌ MT5 error: {data.get('error')}")
            return data
        except zmq.Again:
            return None  # Timeout — no message
        except (zmq.ZMQError, json.JSONDecodeError) as e:
            log.warning(f"ZMQ receive error: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        return self._connected
