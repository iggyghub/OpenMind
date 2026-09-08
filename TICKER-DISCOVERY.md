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

- **Active:** DD1 -- #1157
- **Model:** sonnet

## Queue

- [ ] DD1 -- #1157 -- Add movers/most-actives/all-assets wrappers to AlpacaBrokerClient
- [ ] DD2 -- #1158 -- build_dynamic_universe: movers + random-sample candidate pool
- [ ] DD3 -- #1159 -- Wire extract_ticker/prefilter_candidates onto the dynamic universe
- [ ] DD4 -- #1160 -- Swap expand_strategy_ticker onto the dynamic universe (ADR-0026 decision 5)
- [ ] DD5 -- #1161 -- Finance-news query sourcing + per-source validation rollup

## Landed PRs

(none yet)
