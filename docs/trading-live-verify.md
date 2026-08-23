# Live Broker Verification Guide

This document covers verification steps for features requiring a real Alpaca live connection.
All unit tests in this repository use `StubBrokerClient` and do not hit real APIs.

## Prerequisites
- `alpaca-py` installed: `pip install alpaca-py`
- Valid `alpaca_live_key` and `alpaca_live_secret` stored in your OS keyring under `cerebral_alpaca`.
- Paper trading mode is verified via CI; live mode requires manual execution.

## Preflight (S21/#874)

Before a graduated strategy is ever routed to a real Alpaca connection,
`dispatch_due_events` calls `AlpacaBrokerClient.preflight()` once per tick
it would otherwise go live. On failure it emits a `critical`
`live_preflight_failed` alert and keeps the strategy on the paper broker
instead of silently error-looping.

To verify by hand against a REAL PAPER (never live) Alpaca account:
1. Confirm `alpaca-py` is installed: `pip show alpaca-py`.
2. With no keyring credentials set, graduate a strategy to live status and
   confirm the dispatcher logs `live_preflight_failed` with reason
   "Missing Alpaca credentials for env: live" and the strategy's fills stay
   `phase="paper"`.
3. Set real `alpaca_live_key`/`alpaca_live_secret` in the `cerebral_alpaca`
   keyring service and confirm `preflight()` now reports success against a
   real (paper-mode) account.

## Verification Steps

### 1. Graduation to Live
1. Run a full gauntlet backtest for a strategy.
2. Ensure paper forward trades exceed 30 and rolling CI excludes zero.
3. Observe the `paper_to_live_graduation` alert in Discord/logs.
4. Verify `StrategyState.status` transitions to `"live"` and position size starts at 25%.

### 2. Position-Size Ramp
1. Execute 30 live trades manually or via simulator.
2. Verify alerts transition at trade 30 (50%) and trade 60 (100%).
3. Confirm orders use `AlpacaBrokerClient(env="live")` which reads `alpaca_live_*` credentials.

### 3. Strategy Retirement
1. Force a drawdown event exceeding 2x the gauntlet worst drawdown.
2. Verify `strategy_retirement` alert fires.
3. Check that `StrategyState.status` becomes `"halted"`.
4. Confirm no new orders are placed and existing positions are scheduled for closure.

### 4. Multi-Strategy Correlation
1. Place two highly correlated positions (>0.7 correlation).
2. Verify the `correlation_block` alert triggers.
3. Confirm the second order is rejected and logged under `max_concurrent_positions`/`correlation`.

### 5. Alerts & Panel
1. Check `cerebral/trading/alerts.py` dispatches to registered listeners (Discord/webhook).
2. Open the Trading Panel UI.
3. Verify `renderPositionsView` shows live/halted statuses and trade counts.
4. Verify `renderAlertsView` populates the event log with structured alerts.

## Notes
- Live trades are isolated from paper/backtest trades via the `phase` column in `forward_fills.db`.
- All historical data is preserved for re-validation and re-promotion.
- Revert to paper mode by resetting `StrategyState.status` and clearing live equity curve.
