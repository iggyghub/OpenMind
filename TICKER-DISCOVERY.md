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

## Status: done

## Next slice -- start here

- **Active:** none -- all 5 slices landed 2026-09-08

## Queue

- [x] DD1 -- #1157 -- Add movers/most-actives/all-assets wrappers to AlpacaBrokerClient
- [x] DD2 -- #1158 -- build_dynamic_universe: movers + random-sample candidate pool
- [x] DD3 -- #1159 -- Wire extract_ticker/prefilter_candidates onto the dynamic universe
- [x] DD4 -- #1160 -- Swap expand_strategy_ticker onto the dynamic universe (ADR-0026 decision 5)
- [x] DD5 -- #1161 -- Finance-news query sourcing + per-source validation rollup

## Landed PRs

- PR #1163 -- DD1 (self_dev generated the production `broker.py` code correctly --
  `_screener`/`_connect_screener()` cleanly mirrors the existing `_client`/`_connect()` pattern,
  all three methods match the issue's spec. Hand-fixed: the PR shipped with ZERO tests for the
  three new methods (only the test fixture's signature was extended to accept a `screener` param,
  nothing actually called `get_all_assets`/`get_market_movers`/`get_most_actives`) -- added 3 direct
  unit tests against fake `TradingClient`/`ScreenerClient` doubles. Campaign's own `tests_failed`
  verdict was the known environmental sandbox flake (collection cut off ~7%, not a real failure) --
  full broker/trading suite re-run locally clean, 442 passed. Also surfaced a real gap: two
  concurrent `self_dev_campaign` triggers could each receive the WRONG one's result (the tray IPC
  broadcasts `tool_result` to every connected client, filtered client-side by tool name only, not a
  request id) -- **fixed 2026-09-08** in `plugins/self_dev.py`'s `_campaign()`, which now refuses a
  second concurrent campaign outright rather than letting two run at once. Verified live: DD2 was
  triggered while this fix's own restart was still settling, and a second concurrent trigger against
  the live process got the new "already running" refusal instead of a wrong/duplicate result).
- PR #1165 -- DD2 (self_dev generated `build_dynamic_universe` matching the spec closely, including
  all 3 required tests (fallback-on-exception, fallback-on-empty-ranked-result, movers/actives/
  random-sample inclusion). Hand-fixed one cosmetic issue: a mojibake'd em-dash in the new docstring
  (UTF-8 bytes misread as latin-1/cp1252 somewhere in the self_dev edit pipeline, rendered as
  `â€”`) -- replaced with plain ASCII `--`, matching this codebase's own convention. Campaign's own
  `tests_failed` verdict was the same known environmental sandbox flake as DD1 -- full
  discovery/trading suite re-run locally clean, 471 passed).
- PR #1166 -- DD3 (self_dev's `discovery.py` wiring -- `extract_ticker`/`prefilter_candidates`/
  `process_idea`/`run_discovery_pass` all threading an optional `known_tickers` param -- was correct
  and matched the spec exactly. `scheduler.py`'s half of the wiring was NOT: it added the `broker`
  parameter to `_run_discovery` and imported `build_dynamic_universe`, but never actually called it
  -- `known_tickers=known_tickers` referenced a variable that was never assigned anywhere in
  `_run_discovery`'s scope (the only other `known_tickers` in the file is an unrelated local inside
  `_check_ipo_calendar`, a different method). A real `NameError` that would have crashed every
  single discovery pass -- not caught by the sandbox's own pytest run. Hand-fixed: lazily constructs
  `AlpacaBrokerClient(env="paper")` when no broker is injected (matching `_source_ideas`' own
  `BrowserPlugin()` convention), calls `build_dynamic_universe(broker, fetch_fn)` once per pass, and
  converts its `list` result to a `set` before passing to `known_tickers` -- `prefilter_candidates`
  does `known_tickers - set(existing)`, which raises `TypeError` on a plain list, a second bug the
  first one was hiding. Full discovery+scheduler suite re-run locally clean, 167 passed, including
  every pre-existing `_run_discovery` test running credential-less against the real lazy-broker path
  -- safe only because `build_dynamic_universe`'s own try/except falls back to `_KNOWN_TICKERS` on
  any broker-call failure).
- PR #1167 -- DD4 (two self_dev attempts collided: the first trigger's client-side connection died
  ("1001 going away", the auto-update restart racing the driver-advance push -- see DD1/DD2/DD3's
  own restart-timing notes), but the server-side run continued independently and DID open this PR;
  a second trigger against the same still-Active slice then failed cleanly with "branch already
  exists" rather than corrupting anything -- the concurrency guard covers overlapping CAMPAIGNS, not
  a slice retried against a branch name that's deterministic per issue, a narrower edge this same
  fix doesn't reach. The PR itself repeated AF13's exact failure class from TRADING-AUDIT-FIXES: a
  real, correctly-targeted new test (`broker=fake_broker` injection, asserting candidates come from
  the fake universe) but ZERO production change -- `_expand_strategy_ticker` still read
  `_KNOWN_TICKERS` directly. The test's own `FakeBroker` also didn't match the real
  `AlpacaBrokerClient` interface DD1 built (`get_movers`/`get_actives`/`get_assets` instead of
  `get_market_movers`/`get_most_actives`/`get_all_assets`) -- `build_dynamic_universe`'s own
  try/except would have swallowed the resulting `AttributeError` and silently fallen back to
  `_KNOWN_TICKERS`, passing the test for the wrong reason. Hand-implemented the actual wiring (same
  lazy-broker convention as DD3), fixed the fake to match the real interface, added a liquid-enough
  fake fetch so the $5M floor doesn't also fall through to the fallback. Full scheduler+discovery
  suite re-run locally clean, 167 passed).
- PR #1169 -- DD5 (the cleanest slice of the whole campaign -- landed unchanged, no fix needed. All
  three parts present and correct: the 5 named finance-news `site:` queries appended to
  `_run_discovery`'s defaults, `DiscoveryAttempts.get_source_performance()` rolling up by domain
  parsed from `idea_url` (no schema change, as specified), and the new
  `get_discovery_source_performance` tool wired into both `list_tools`/`call_tool` dispatch. Both
  required tests present and correct. Full discovery+scheduler suite re-run locally clean, 169
  passed. **Campaign complete.**

## Campaign retrospective

All 5 slices required hand-review; the sandbox's own `tests_failed` verdict was the known
environmental flake in every single case (never a real signal) -- but 3 of 5 slices (DD1, DD3, DD4)
had real, independent bugs the sandbox's own green-ish run didn't catch: missing tests (DD1),
a `NameError` that would crash every discovery pass (DD3), and a slice that shipped a correct test
with zero production code change (DD4, repeating AF13's exact failure class from the earlier
TRADING-AUDIT-FIXES campaign). Only DD2 and DD5 landed clean. This matches the driver file's own
opening warning almost exactly -- assume every slice needs real review, not just a glance at a green
checkmark.

Also surfaced, hand-fixed, and merged along the way (not part of the original 5-slice design, found
live while running this campaign):
- `plugins/self_dev.py`'s `_campaign()` had no concurrency guard at all -- fixed same-day, now
  refuses a second concurrent campaign outright.
- `tray/main.js`'s auto-update restart considers Felix idle purely from chat/voice wake state, blind
  to a self_dev campaign running via the direct-IPC `trigger_campaign.py` path -- caused 3 restart
  collisions in this session alone. Filed as issue #1168, driver `SELF-DEV-IDLE-FIX.md`, queued to
  run next.
