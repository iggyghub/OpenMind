# TICKER-DISCOVERY.md -- self_dev campaign driver: dynamic candidate-ticker universe

Source: 2026-09-08 Claude Code grill session (`grill-with-docs`), following on from the
TRADING-AUDIT-FIXES.md campaign. Replaces `cerebral/trading/discovery.py`'s hardcoded
`_KNOWN_TICKERS` frozenset (36 symbols, manually edited 2026-08-21/09-01) with a dynamic universe
built from Alpaca's real movers/most-actives screeners plus a random sample of the broader
tradable universe -- the user explicitly wants tickers with real day/swing volatility ("large
jumps in a day or few"), not stable mega-caps, and wants the pool to stay current on its own
instead of needing a human to hand-edit source code periodically.

Also adds named finance-news query sourcing (Motley Fool, Benzinga, MarketWatch, Zacks, CNBC --
via the existing `web_search` path, no new scraper) and a per-source-domain validation rollup, so
which sites' "top picks" actually pan out becomes visible over time.

**Read before running this campaign:** every single slice landed against `cerebral/trading/`
across this project's history (TRADING.md, 48+ slices, then TRADING-AUDIT-FIXES.md's 21) has
needed hand-review, and the large majority shipped a real bug self_dev's own tests didn't catch.
Assume the same is true here. **Hand-verify every PR's actual diff before merging, even on a green
sandbox test run** -- see `feedback_selfdev_squash_diff_trap` / TRADING.md's own S17 history for
the reword-and-purge-ledger remedy if a slice comes back with "Edit step produced no commit" more
than once or twice.

**None of these fixes require touching `tray/`.** Standard practice for this whole project: never
send `tray/` to self_dev. The related UI ask (paper/live tabs on the Trading Panel, sortable by
%change + strategy) is being built separately, directly, not through this campaign.

Slices are STRICTLY ORDERED -- each depends on the previous landing first (DD1 -> DD2 -> DD3 ->
DD4; DD5 is independent of DD2-DD4 but still queued last since it's the lowest-severity addition).

## Status: ready

## Next slice -- start here

- **Active:** DD2 -- #1158
- **Model:** sonnet

## Queue

- [x] DD1 -- #1157 -- Add movers/most-actives/all-assets wrappers to AlpacaBrokerClient
- [ ] DD2 -- #1158 -- build_dynamic_universe: movers + random-sample candidate pool
- [ ] DD3 -- #1159 -- Wire extract_ticker/prefilter_candidates onto the dynamic universe
- [ ] DD4 -- #1160 -- Swap expand_strategy_ticker onto the dynamic universe (ADR-0026 decision 5)
- [ ] DD5 -- #1161 -- Finance-news query sourcing + per-source validation rollup

## Landed PRs

- PR #1163 -- DD1 (self_dev generated the production `broker.py` code correctly --
  `_screener`/`_connect_screener()` cleanly mirrors the existing `_client`/`_connect()` pattern,
  all three methods match the issue's spec. Hand-fixed: the PR shipped with ZERO tests for the
  three new methods (only the test fixture's signature was extended to accept a `screener` param,
  nothing actually called `get_all_assets`/`get_market_movers`/`get_most_actives`) -- added 3 direct
  unit tests against fake `TradingClient`/`ScreenerClient` doubles. Campaign's own `tests_failed`
  verdict was the known environmental sandbox flake (collection cut off ~7%, not a real failure) --
  full broker/trading suite re-run locally clean, 442 passed. Also surfaced a real gap in
  `trigger_campaign.py`/the tray IPC: its WebSocket response isn't correlated to the request that
  asked for it -- two concurrent `self_dev_campaign` triggers on separate connections can each
  receive the WRONG one's result. Not yet fixed -- worth a proper request-id-matching fix, and
  `self_dev.py`'s own `_campaign()` has no lock preventing concurrent runs at all, contradicting
  ADR-0028 rule 5. Neither caused any real corruption here since DD1/UI1 touch disjoint files, but
  don't rely on that next time).
