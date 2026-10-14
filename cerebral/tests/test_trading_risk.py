import time
import pytest
from cerebral.trading.risk_limits import RiskManager, RiskConfig, RiskEvaluation
from cerebral.trading.failure_handling import FailureHandler
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.alerts import AlertDispatcher, StructuredAlert

class TestRiskLimits:
    def test_per_trade_risk_rejects_high_risk(self):
        mgr = RiskManager()
        # Equity 10000, 2% is 200. Trade value 300 -> reject
        res = mgr.check_order(10000.0, 0, 0.0, 300.0, "AAPL", 3.0)
        assert not res.allowed
        assert res.blocked_by == "per_trade_risk"

    def test_per_trade_risk_accepts_within_limit(self):
        mgr = RiskManager(RiskConfig(max_per_trade_risk_pct=2.0))
        res = mgr.check_order(10000.0, 0, 0.0, 150.0, "AAPL", 1.5)
        assert res.allowed

    def test_per_trade_risk_tolerates_a_cent_of_float_slop_at_the_cap(self):
        """qty sized to exactly the risk budget (no headroom, 2026-09-01
        user choice) re-prices at dispatch as qty*price, which can round a
        fraction of a cent over the cap on float noise alone -- live-observed
        blocking real trades. A whole cent over must still reject."""
        mgr = RiskManager(RiskConfig(max_per_trade_risk_pct=10.0))
        just_over = mgr.check_order(100.0, 0, 0.0, 10.0 + 1e-9, "AAPL", 1.0)
        a_cent_over = mgr.check_order(100.0, 0, 0.0, 10.011, "AAPL", 1.0)
        assert just_over.allowed
        assert not a_cent_over.allowed

    def test_per_trade_risk_override(self):
        mgr = RiskManager(RiskConfig(max_per_trade_risk_pct=10.0))
        # Global 10% cap would block 25% of account. Override 25% allows it.
        res = mgr.check_order(10000.0, 0, 0.0, 2500.0, "AAPL", 25.0, max_per_trade_risk_pct_override=25.0)
        assert res.allowed
        # Without override, same trade should be blocked
        res_no_override = mgr.check_order(10000.0, 0, 0.0, 2500.0, "AAPL", 25.0)
        assert not res_no_override.allowed
        assert res_no_override.blocked_by == "per_trade_risk"

    def test_daily_loss_halts_trades(self):
        mgr = RiskManager(RiskConfig(max_daily_loss_pct=6.0))
        # 6% of 10000 = 600. Current loss 600 -> reject
        res = mgr.check_order(10000.0, 0, 600.0, 100.0, "AAPL", 1.0)
        assert not res.allowed
        assert res.blocked_by == "daily_loss_limit"

    def test_max_concurrent_positions(self):
        mgr = RiskManager(RiskConfig(max_concurrent_positions=10))
        res = mgr.check_order(10000.0, 10, 0.0, 100.0, "AAPL", 1.0)
        assert not res.allowed
        assert res.blocked_by == "max_concurrent_positions"

    def test_blocked_trade_logs_reason(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        mgr = RiskManager(RiskConfig(max_per_trade_risk_pct=2.0))
        mgr.check_order(10000.0, 0, 0.0, 300.0, "AAPL", 3.0)
        # The log message is the human-readable "Per-trade risk ..." string
        # (see risk_limits.py's `reason` variable), not the machine-readable
        # blocked_by slug "per_trade_risk" -- that's covered separately by
        # test_per_trade_risk_rejects_high_risk's res.blocked_by assertion.
        assert "Per-trade risk" in caplog.text

class TestFailureHandling:
    def test_stale_data_halts_trades(self):
        fh = FailureHandler(stale_threshold_seconds=1.0)
        assert fh._new_trades_allowed is True
        # Simulate old timestamp
        fh.update_market_data_timestamp(time.time() - 5.0)
        assert fh._is_data_stale is True
        assert fh._new_trades_allowed is False

    def test_partial_fill_keeps_partial(self):
        fh = FailureHandler()
        from cerebral.trading.failure_handling import TradeState
        fh._trade_states["oid1"] = TradeState(
            symbol="AAPL", qty=10.0, side="buy", order_id="oid1", status="PENDING"
        )
        fh.handle_partial_fill("oid1", 6.0, 10.0)
        assert fh._trade_states["oid1"].status == "PARTIALLY_FILLED"

    def test_halted_symbol_queues_close(self):
        fh = FailureHandler()
        fh.detect_halted_symbol("TSLA", True)
        assert fh._halted_symbols.get("TSLA") is True
        fh.detect_halted_symbol("TSLA", False)
        assert "TSLA" not in fh._halted_symbols

    def test_crash_recovery_reconciles(self):
        fh = FailureHandler()
        fh.crash_recovery(["AAPL", "MSFT"])
        symbols = [s.symbol for s in fh._trade_states.values()]
        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_stub_broker_partial_fill_simulation(self):
        """Verify StubBrokerClient supports partial fill simulation required by S5b."""
        client = StubBrokerClient()
        client.enable_partial_fills_for("AAPL", ratio=0.5)
        order = client.place_order("AAPL", qty=10.0, side="buy", type="market")
        assert order.filled_qty == 5.0
        assert order.status == "PARTIALLY_FILLED"

    def test_bearish_sentiment_blocks_new_open(self):
        mgr = RiskManager()
        res = mgr.check_sentiment("AAPL", "BEARISH")
        assert not res.allowed
        assert res.blocked_by == "market_sentiment"

    def test_neutral_and_bullish_sentiment_pass(self):
        mgr = RiskManager()
        assert mgr.check_sentiment("AAPL", "NEUTRAL").allowed
        assert mgr.check_sentiment("AAPL", "BULLISH").allowed

    def test_no_sentiment_reading_passes(self):
        """None -- gate off, or no reading yet -- never blocks."""
        mgr = RiskManager()
        assert mgr.check_sentiment("AAPL", None).allowed

    def test_bearish_stock_sentiment_blocks_new_open_even_when_market_is_not(self):
        """A stock's own BEARISH reading blocks independently of the
        market-wide reading -- penny stocks move on stock-specific news."""
        mgr = RiskManager()
        res = mgr.check_sentiment("PENNY", "NEUTRAL", stock_sentiment_label="BEARISH")
        assert not res.allowed
        assert res.blocked_by == "stock_sentiment"

    def test_bearish_market_sentiment_still_blocks_when_stock_is_bullish(self):
        """The market-wide check runs first -- a bullish single stock
        doesn't override a bearish overall market."""
        mgr = RiskManager()
        res = mgr.check_sentiment("PENNY", "BEARISH", stock_sentiment_label="BULLISH")
        assert not res.allowed
        assert res.blocked_by == "market_sentiment"

    def test_no_stock_sentiment_reading_passes(self):
        mgr = RiskManager()
        assert mgr.check_sentiment("AAPL", "NEUTRAL", stock_sentiment_label=None).allowed

    def test_symbol_claim_blocks_when_already_claimed(self):
        mgr = RiskManager()
        res = mgr.check_symbol_claim("AAPL", {"AAPL", "MSFT"})
        assert not res.allowed
        assert res.blocked_by == "symbol_claimed"

    def test_symbol_claim_passes_when_not_claimed(self):
        mgr = RiskManager()
        res = mgr.check_symbol_claim("AAPL", {"MSFT"})
        assert res.allowed

    def test_symbol_claim_passes_on_empty_set(self):
        mgr = RiskManager()
        assert mgr.check_symbol_claim("AAPL", set()).allowed

class TestAlerts:
    def test_alert_dispatcher_emits_structured_event(self):
        dispatcher = AlertDispatcher()
        received = []
        dispatcher.register_listener(lambda a: received.append(a))
        
        dispatcher.emit(StructuredAlert(
            severity="warning", 
            event_type="test_event", 
            message="Test message",
            context={"key": "val"}
        ))
        
        assert len(received) == 1
        assert received[0].severity == "warning"
        assert received[0].context == {"key": "val"}

    def test_alert_dispatcher_history_is_bounded_to_500(self):
        dispatcher = AlertDispatcher()
        first_alert = StructuredAlert(severity="info", event_type="test", message="first")
        dispatcher.emit(first_alert)

        for i in range(500):
            dispatcher.emit(StructuredAlert(severity="info", event_type="test", message=f"alert-{i}"))

        pending = dispatcher.get_pending()
        assert len(pending) == 500
        # Oldest alert should have been evicted
        assert pending[0].message != "first"
