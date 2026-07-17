"""
TradeGOD — Risk Engine Unit Tests
Tests the $50 risk cap, lot sizing, and kill-switch math.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.risk_manager import RiskManager
from config.app_config import RISK


@pytest.fixture
def rm():
    """Create a RiskManager with production config."""
    return RiskManager(RISK)


class TestLotSizing:
    """Test the core risk math: Lot = Risk / (SL_pips × Pip_Value)"""

    def test_standard_eurusd_10pip_sl(self, rm):
        """EURUSD, 10-pip SL → should give ~0.50 lots"""
        lot = rm.calculate_lot_size("EURUSD", sl_pips=10.0, pip_value_per_lot=10.0)
        # $50 / (10 pips × $10/pip) = 0.50 lots
        assert lot == pytest.approx(0.50, abs=0.01)

    def test_max_risk_cap(self, rm):
        """Never exceed $50 risk regardless of balance"""
        lot = rm.calculate_lot_size("EURUSD", sl_pips=5.0, pip_value_per_lot=10.0,
                                     current_balance=10000.0)
        # 1% of $10K = $100, but capped at $50 → $50 / (5 × $10) = 1.0
        risk = lot * 5.0 * 10.0
        assert risk <= 50.01, f"Risk ${risk:.2f} exceeds $50 cap"

    def test_minimum_lot(self, rm):
        """Should never return less than 0.01"""
        lot = rm.calculate_lot_size("EURUSD", sl_pips=500.0, pip_value_per_lot=10.0)
        assert lot >= 0.01

    def test_maximum_lot_cap(self, rm):
        """Lot size capped at 2.0"""
        lot = rm.calculate_lot_size("XAUUSD", sl_pips=0.1, pip_value_per_lot=10.0)
        assert lot <= 2.0

    def test_gold_scalp_sl(self, rm):
        """Gold 15-pip SL: $50 / (15 × $1) = ?"""
        # Gold pip value per 0.01 lot = $0.1, per lot = $1? Actually gold pip_val = $10/lot
        lot = rm.calculate_lot_size("XAUUSD", sl_pips=15.0, pip_value_per_lot=10.0)
        # $50 / (15 × $10) = 0.333 → rounds to 0.33
        assert lot == pytest.approx(0.33, abs=0.02)

    def test_jpy_pair(self, rm):
        """USDJPY, 10-pip SL with $6.5/lot pip value"""
        lot = rm.calculate_lot_size("USDJPY", sl_pips=10.0, pip_value_per_lot=6.5)
        # $50 / (10 × $6.5) = 0.769 → 0.76 lots
        assert 0.70 < lot < 0.85

    def test_invalid_sl_pips(self, rm):
        """Invalid SL should return 0.01 default safely"""
        lot = rm.calculate_lot_size("EURUSD", sl_pips=0.0)
        assert lot == 0.01
        lot = rm.calculate_lot_size("EURUSD", sl_pips=-5.0)
        assert lot == 0.01


class TestKillSwitch:
    """Test daily drawdown kill-switch logic"""

    def test_normal_trading_allowed(self, rm):
        """Balance at full $5000 — trading should be allowed"""
        rm._start_of_day_balance = 5000.0
        rm._kill_triggered = False
        assert rm.check_daily_drawdown(5000.0) is True

    def test_small_loss_allowed(self, rm):
        """$100 loss (2%) — still under $225 kill threshold"""
        rm._start_of_day_balance = 5000.0
        rm._kill_triggered = False
        assert rm.check_daily_drawdown(4900.0) is True

    def test_kill_at_225(self, rm):
        """$225 loss (4.5%) — kill-switch should trigger"""
        rm._start_of_day_balance = 5000.0
        rm._kill_triggered = False
        result = rm.check_daily_drawdown(4775.0)  # $5000 - $225 = $4775
        assert result is False
        assert rm._kill_triggered is True

    def test_kill_persists(self, rm):
        """Once kill is triggered, stays triggered even if equity recovers"""
        rm._start_of_day_balance = 5000.0
        rm._kill_triggered = True
        assert rm.check_daily_drawdown(5000.0) is False

    def test_max_drawdown_kill(self, rm):
        """Total loss of $450 (9%) kills trading"""
        result = rm.check_max_drawdown(4550.0)  # $5000 - $450 = $4550
        assert result is False

    def test_max_drawdown_safe(self, rm):
        """Total loss of $300 (6%) is still safe"""
        result = rm.check_max_drawdown(4700.0)
        assert result is True


class TestDailyTradeLimit:
    """Test max 2 trades per day rule"""

    def test_first_trade_allowed(self, rm):
        rm._daily_trades = 0
        assert rm.check_daily_trade_limit() is True

    def test_second_trade_allowed(self, rm):
        rm._daily_trades = 1
        assert rm.check_daily_trade_limit() is True

    def test_third_trade_blocked(self, rm):
        rm._daily_trades = 2
        assert rm.check_daily_trade_limit() is False


class TestConsistencyRule:
    """Test the 60% concentration cap ($240 max single trade)"""

    def test_below_cap(self, rm):
        should_close, pct = rm.should_partial_close(100.0)
        assert should_close is False
        assert pct == 0.0

    def test_at_cap(self, rm):
        """$240 = 60% of $400 phase target → should trigger partial"""
        should_close, pct = rm.should_partial_close(240.0)
        assert should_close is True
        assert pct == 50.0

    def test_above_cap(self, rm):
        should_close, pct = rm.should_partial_close(300.0)
        assert should_close is True


class TestRiskMath:
    """Pure math verification"""

    def test_risk_per_trade_is_1pct(self):
        """Verify $50 = 1% of $5000"""
        account = 5000.0
        risk_pct = 1.0
        expected = account * (risk_pct / 100)
        assert expected == 50.0

    def test_kill_switch_is_4_5pct(self):
        """Verify $225 = 4.5% of $5000"""
        account = 5000.0
        kill_pct = 4.5
        expected = account * (kill_pct / 100)
        assert expected == 225.0

    def test_max_dd_is_9pct(self):
        """Verify $450 = 9% of $5000"""
        expected = 5000.0 * 0.09
        assert expected == 450.0

    def test_hold_time_minimum(self):
        """180 seconds = 3 minutes (HFT compliance)"""
        from config.app_config import RISK
        assert RISK["position_rules"]["min_hold_time_seconds"] == 180

    def test_keepalive_hold_exceeds_minimum(self):
        """KeepAlive hold time (185s) > 180s minimum"""
        from config.app_config import RISK
        keepalive_hold = RISK["activity_keepalive"]["hold_time_seconds"]
        min_hold       = RISK["position_rules"]["min_hold_time_seconds"]
        assert keepalive_hold > min_hold


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
