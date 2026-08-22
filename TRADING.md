# TRADING.md -- Stock trading campaign driver

Design: ADR-0026 (not written yet).
Scaffolded 2026-08-21, grill closed 2026-08-22.

## Status: ready

## Next slice -- start here

- **Active:** S5c -- #838
- **Model:** sonnet

## Queue

- [x] S1a -- #831 -- OHLCV data module (yfinance)
- [x] S1b -- #832 -- Backtest engine + reference strategy
- [x] S2 -- #833 -- Cost/slippage model + OOS + walk-forward gates
- [x] S3 -- #834 -- Full gauntlet + strategy card
- [x] S4 -- #835 -- URL/web/book -> strategy spec
- [x] S5a -- #836 -- Alpaca broker integration
- [x] S5b -- #837 -- Risk limits + failure behaviour
- [ ] S5c -- #838 -- Paper forward record + auto-promotion
- [ ] S6 -- #839 -- Autonomous live execution + retirement + alerting

Per-slice model: sonnet unless the queue entry says otherwise. This checklist is
what `self_dev_campaign` parses to tick/advance -- the "Phased slices" section
below is the detailed human-readable reference for the same 9 slices.

## Landed PRs

- PR #840 -- S1a (auto-merged by self_dev_campaign; landed with failing tests
  because the sandbox never had yfinance installed -- patched by hand 2026-08-22,
  see pyproject.toml + cerebral/trading_data.py + its test)
- PR #841 -- S1b+S2 combined (auto-merged by self_dev_campaign; added
  cerebral/trading/cost_model.py + gauntlet.py with oos_test()/walk_forward()
  gates. S1b's own deliverable -- a backtest.run() engine matching
  `def strategy(data) -> signals`, per issue #832 -- was never built as its
  own thing; gauntlet.py invented a different strategy_fn interface instead
  (returns (gross_returns, trades) directly). Queue entry left ticked since
  redoing S1b now would just produce a second, conflicting engine -- but
  decision #5 in the table above no longer matches what's implemented.
  Worth a real decision before S4 builds strategy generation against
  whichever interface is meant to be canonical.)
- PR #842 -- S3 -- never cleanly auto-merged (branch was based on a local
  commit that got superseded before the merge; also independently
  reinvented cerebral/trading/gauntlet.py from scratch, colliding with
  PR #841's version). Resolved by hand 2026-08-22: merged run_gauntlet() +
  StrategyCard into the same gauntlet.py as GauntletGateResult (renamed to
  avoid the class-name collision with S2's GateResult), then fixed 6 real
  bugs the merge exposed -- a numpy.bool_ identity bug, a Monte Carlo gate
  that was statistically meaningless as originally written (permuting a
  fixed set never changes its mean), a pandas indexing bug, an int-cast
  bug, a test fixture that ignored which parameter was perturbed, and a
  wrong expected value in a compound-return test. All 29 trading tests
  pass. See commit 5a446f9 for the full account. PR #842 itself was never
  merged on GitHub -- its content now exists directly on local master via
  this fix instead.
- No PR -- 2026-08-22, a fresh self_dev_campaign call for S4 got blocked
  citing PR #840 (S1a's PR, already merged and hand-fixed hours earlier).
  Root cause: run_id was `campaign-{slug}-s{n}` where n is the loop
  position WITHIN one campaign() call, not a slice identity -- every fresh
  invocation's first-processed slice reused run_id "campaign-trading-s1",
  colliding with S1a's original (persistent, SQLite-backed) ledger entry.
  _run()'s resume logic replayed that old, already-obsolete failure
  instead of doing real work on S4. Fixed in commit (see plugins/self_dev.py
  `_campaign`): run_id now derives from the active slice's label
  (`campaign-trading-s1a`, `-s4`, etc.), stable and unique per slice
  regardless of invocation. The three polluted ledger rows
  (campaign-trading-s1/-s2/-s3) were purged from cerebral/data/openmind.db
  by hand since S1a-S3's real work is already committed to git and there
  was nothing legitimate left to resume from them.
- PR #843 -- S4 -- real fresh work (confirms the run_id fix above worked:
  its branch descended from current master, not a stale point). Added
  cerebral/trading_ideas.py (extract_from_url/from_prose/from_book_claim/
  to_strategy/compile_strategy) + tests. Tests failed on one trivial bug:
  a stub LLM in the test always returns a hardcoded `[1, 2, 3]` regardless
  of input, but the assertion checked against the input instead of what
  the stub actually returns -- fixed by hand, all 6 tests pass.
  SECURITY (found during hand-verification, not by the auto-merge gate):
  `compile_strategy` called `exec(code_str, {"__builtins__": __builtins__},
  ...)` -- unrestricted code execution with full builtins, on code
  ultimately derived from scraped web content. Hardened with two layers,
  neither a substitute for real sandboxing: (1) runs the same
  forbidden-pattern scan builder.py already uses for LLM-generated plugin
  code (cerebral.security.scan_source -- blocks os/subprocess/exec/eval/
  pickle/file-write), (2) exec() itself only gets a minimal safe-builtins
  allowlist (no open/__import__/exec/eval/input), which independently
  caught an obfuscated `__import__` the regex scan alone missed. Neither
  layer is a real sandbox -- route this through the ADR-0010 sandbox
  self_dev already uses for untrusted code before S5+ runs strategy code
  against a live broker connection.
- PR #844 -- S5a -- real fresh work (based on current master post-S4, same
  as #843). Added cerebral/trading/broker.py (BrokerClient Protocol,
  AlpacaBrokerClient, StubBrokerClient) + tests. One real test failure:
  `StubBrokerClient.cancel_order` did `Order(**self._orders[order_id],
  status="CANCELED")` -- tried to ** an Order dataclass instance instead
  of a dict, always raised TypeError. Order is a plain mutable dataclass;
  fixed by mutating `.status` in place instead of reconstructing.
  SECURITY/CORRECTNESS (found during hand-verification, untested by the
  gate since no test exercised it): `AlpacaBrokerClient.place_order`
  hardcoded `limit_price="0.01"` for every limit order -- the
  `BrokerClient` Protocol had no way to pass a real one in the first
  place. Any live limit order would have silently submitted at one cent.
  Added `limit_price: Optional[float] = None` to the Protocol and both
  implementations; both now raise ValueError if a limit order arrives
  with no price. New test covers the missing-price rejection.
- PR #845 -- S5b -- real fresh work (based on current master post-S5a).
  Added cerebral/trading/{risk_limits,failure_handling,alerts}.py + tests.
  Two real bugs, both in the first two files, both blocking every actual
  use of the class (not edge cases):
  (1) `RiskManager.__init__`'s only settings parameter was `settings_store`
  (expected to be a store object with `.get()`), so tests constructing a
  manager with an explicit `RiskConfig(max_per_trade_risk_pct=2.0)` passed
  it positionally into that slot -- `settings_store.get(...)` then failed
  because a RiskConfig isn't a mapping. Added a distinct `config:
  Optional[RiskConfig]` parameter that wins over settings_store lookup.
  (2) `FailureHandler._last_data_timestamp` starts at None (no data has
  arrived yet on construction), but `update_market_data_timestamp` did
  `if ts > self._last_data_timestamp` before checking for None -- crashed
  on the very first tick, i.e. every real use. Guarded the comparison.
  One test itself was also wrong: asserted the literal string
  "per_trade_risk" (the machine-readable blocked_by slug) appeared in a
  log line that actually reads "Per-trade risk 300.00 exceeds..." (the
  human-readable reason text) -- fixed the assertion to match what's
  really logged. alerts.py had no issues. 11/11 new tests pass, 55/55
  across the trading suite.

## Thesis

The prompt-to-code step is commoditized; any model can turn "buy when RSI crosses 30"
into a strategy. The only part worth building is the **validation layer** that decides
whether a strategy is a real edge or an expensive illusion, plus the discipline that
keeps an unvalidated one away from real capital.

Felix is an **autonomous trading agent**: research, validation, and execution in one
pipeline. The deliverable is *evidence about a strategy* that Felix also acts on.

## Decisions (grill record)

| # | Item | Decision |
|---|---|---|
| 1 | Purpose ceiling | Full autonomous agent -- research + validation + execution, no human confirmation gate |
| 2 | Universe & horizon | All asset classes; **penny stocks first** |
| 3 | Idea source priority | Books, papers, and web content primary (user provides URLs, Felix crawls + follows links); user prose alongside; 82-book list is a wishlist (not yet acquired) |
| 4 | Data source | yfinance (free, permanent); survivorship bias on penny stocks is a known accepted limitation |
| 5 | Strategy representation | Python functions: `def strategy(data) -> signals` |
| 6 | Gate thresholds | Defaults, user-configurable (see gauntlet section) |
| 7 | Minimum trade count | 30 trades before a forward record is considered meaningful |
| 8 | Broker | Alpaca -- paper and live as separate API keys via keyring |
| 9 | Risk limits | 2% per trade, 6% daily loss, 10 max concurrent -- user-configurable |
| 10 | Failure behaviour | Conservative-continue across all modes (see section) |
| 11 | Anti-goals | See section |
| 12 | S1 split | S1a (data) and S1b (backtest engine) are separate slices |
| 13 | Cost model timing | S1b has no cost model; costs arrive in S2 |
| 14 | S4 shape | URL/web/book -> strategy spec (user provides URLs, Felix follows links within) |
| 15 | Paper auto-start | Automatic on gauntlet pass |
| 16 | Live graduation | Automatic with position-size ramp: 25% -> 50% -> 100% |
| 17 | Panel approach | Incremental -- each slice adds its view; no standalone panel slice |
| 18 | Broker slice | S5a -- dedicated Alpaca integration slice before paper trading |
| 19 | Risk/failure slice | S5b -- risk limits + failure behaviour tested on paper before live |
| 20 | Phasing | Sequential -- S4 stays after gauntlet |
| 21 | Settings | All thresholds, limits, and gate parameters are user-configurable |
| 22 | Free only | No paid data sources, no paid models, free commissions |

## What already exists (reuse, do not rebuild)

| Piece | Where | Use for |
|---|---|---|
| `market_price`, `market_quote` | `plugins/markets.py` | current quotes; NOT history (no OHLCV depth) |
| Book knowledge corpus (S1-S4 landed) | `plugins/book_ingest.py`, ADR-0025 | idea source: claims/assumptions/methods extracted per chapter |
| 82-book trading list | `AI_Trading_Book_Master_List.csv` | the corpus to ingest (books not yet acquired) |
| Claim vs fact vs inference citation | ADR-0025 S8 (#804) | "author claims X" never collapses to "X is true" -- same rule governs strategy provenance |
| `self_dev` | `plugins/self_dev.py` | Felix implements its own slices via clone -> test -> PR |
| `scheduler` | `plugins/scheduler.py` | periodic re-validation / paper-run ticks |
| Sandbox | ADR-0010, `plugins/shell.py` | run generated strategy code without trusting it |
| `browser`, `http_client` | `plugins/browser.py`, `plugins/http_client.py` | URL fetching for S4 |
| `rss_monitor` | `plugins/rss_monitor.py` | future: monitoring trading content feeds |
| Discord / notifications | existing notification paths | alerting on key trading events |

`plugins/finance.py` is receipts/OCR (personal accounting). Unrelated. Do not extend it.

## The pipeline

```
idea -> hypothesis -> data -> strategy spec -> backtest -> VALIDATION GAUNTLET
     -> paper forward record (auto) -> live autonomous execution (ramped)
```

Each arrow is a gate. A strategy that fails a gate does not proceed -- it goes back to
hypothesis, with the failure recorded.

1. **Idea.** Prose from the user, or a claim/method extracted from a URL/book/paper.
   Every strategy carries provenance: source URL, book/chapter/claim, or "user, verbatim".
   User provides URLs; Felix crawls the page and follows links within it.
2. **Hypothesis.** Stated as something falsifiable *before* seeing results: what
   inefficiency, why it should exist, why it should persist, what would disprove it.
   No hypothesis, no backtest.
3. **Data.** yfinance (free, permanent). Handles splits/dividends. Survivorship bias on
   penny stocks is a known, accepted limitation -- strategy cards carry the caveat.
4. **Strategy spec.** A Python function: `def strategy(data) -> signals`. Inspectable,
   executable without a compile step.
5. **Backtest.** Produces an equity curve and a trade log -- never a summary alone.
6. **Gauntlet.** Below.
7. **Paper.** Starts automatically on gauntlet pass. Forward record via Alpaca paper API.
   Numbers from the broker, never re-derived. Minimum 30 trades before the record is
   considered meaningful.
8. **Live.** Automatic graduation when paper CI excludes zero after 30+ trades.
   Position-size ramp: 25% for first 30 live trades, 50% for next 30, then 100%.

## The validation gauntlet

Mandatory pass/fail gates. All thresholds are user-configurable; defaults below.
A strategy that skips a gate is marked UNVALIDATED and cannot reach paper.

- **Out-of-sample.** 20-30% of the historical period, held out, never touched during
  development.
- **Walk-forward.** 5:1 ratio (e.g., fit on 5 years, test on 1 year, roll forward).
  Must be profitable on forward windows in aggregate.
- **Monte Carlo permutation.** p < 0.05. Shuffle returns/labels; less than 5% chance
  that noise produces this result.
- **Vs-random benchmark.** Strategy must beat the 95th percentile of random-entry
  returns (same holding period and position sizing).
- **Vs-benchmark.** Strategy must beat buy-and-hold of an appropriate index (SPY for
  equities, BTC for crypto, etc.). A strategy that beats random but loses to the
  benchmark isn't worth the complexity.
- **Noise / synthetic data.** Perturb prices +/- 1-2%. Sharpe must not drop by more
  than 50%.
- **Parameter sensitivity.** Vary each parameter +/- 20%. Performance must not flip
  from profitable to losing.
- **Costs.** For penny stocks: spread modelled at 1-3% of price, plus slippage.
  Report gross and net.
- **Capacity & liquidity.** Position size must be under 5-10% of average daily volume.

The output of the gauntlet is a **strategy card**: hypothesis, provenance, every gate's
result, the equity curve, and the honest verdict including confidence intervals.

## Risk limits

Enforced in code. A trade that would breach a limit does not execute.
All values are user-configurable; defaults below.

- **Per-trade risk:** 2% of account
- **Daily loss limit:** 6% of account -- halts trading until next session
- **Max concurrent positions:** 10

## Failure behaviour

Conservative-continue across all modes:

- **Stale data / broken feed:** halt new trades until the feed recovers; leave existing
  positions alone.
- **Partial fill:** keep the partial, cancel remainder, adjust position size math.
- **Halted symbol:** queue a close order for when trading resumes.
- **Felix crash mid-position:** on restart, reconcile with the broker (read open
  positions) and resume managing them.

## Strategy retirement

A live strategy is not permanent. Felix monitors its rolling forward performance:

- **CI re-enters zero.** If the rolling confidence interval on expectancy (over the
  last 30+ trades) starts containing zero again, the strategy is halted automatically.
  No new trades; existing positions managed to close.
- **Drawdown breach.** If a strategy's drawdown exceeds 2x the worst drawdown seen in
  the gauntlet backtest, it is halted.
- **Retirement is reversible.** A retired strategy can be re-validated and re-promoted
  if conditions change. Its full history is preserved.

## Alerting

Felix notifies on key events (via existing notification paths -- Discord, etc.):

- Strategy passed the gauntlet (with card summary)
- Strategy graduated from paper to live
- Daily loss limit hit -- trading halted
- Strategy retired (CI re-entered zero or drawdown breach)
- Crash recovery -- reconciled N positions on restart
- Risk limit prevented a trade

## Multi-strategy correlation

When running multiple strategies simultaneously, Felix checks pairwise correlation
of positions before entry. If a new trade would push portfolio exposure above the
concurrent position limit into correlated names (>0.7 correlation over trailing 60
days), it is blocked. This prevents 10 "independent" strategies from all betting on
the same penny stock sector.

## Phased slices (sequential, each gated on the previous having real data)

- S1a -- #831 -- OHLCV data module. Fetch and cache daily bars via yfinance, handle
  splits/dividends, return a DataFrame.
- S1b -- #832 -- Backtest engine. Takes a strategy function + DataFrame, produces equity
  curve + trade log. One reference strategy (MA cross). Panel: backtest results view.
- S2  -- #833 -- Cost/slippage model (penny stock spreads) + out-of-sample + walk-forward gates.
- S3  -- #834 -- Monte Carlo permutation, vs-random, vs-benchmark, noise, parameter
  sensitivity. Strategy card with confidence intervals. Panel: strategy card view.
- S4  -- #835 -- URL/web/book -> strategy spec. User provides URL, Felix crawls + follows
  links, extracts testable claims, generates `def strategy(data) -> signals` with provenance.
- S5a -- #836 -- Alpaca broker integration. Auth via keyring, read account/positions,
  place/cancel orders. Tested against stubs.
- S5b -- #837 -- Risk limits + failure behaviour. Tested on Alpaca paper. All limits
  user-configurable.
- S5c -- #838 -- Paper forward record. Auto-starts on gauntlet pass. Confidence intervals,
  30-trade minimum. Panel: forward record view.
- S6  -- #839 -- Autonomous live execution. Auto-graduation with position-size ramp
  (25% -> 50% -> 100%). Strategy retirement. Multi-strategy correlation check.
  Alerting on key events. Panel: open positions + alerts view.

## Anti-goals

**Will not build:**
- Manual chart-drawing / discretionary TA tools
- Strategy marketplace or sharing/selling features
- Social / copy-trading
- Portfolio optimization / rebalancing (different problem)
- Options pricing / Greeks engine (separate campaign if needed)
- ML model training for strategy generation (separate campaign if needed)
- Signal-space search / genetic algorithm (Build Alpha's lane, separate campaign)
- Autonomous web crawling / source discovery (user provides URLs)

**Honesty rule (not negotiable):**
- Felix never presents a number without its uncertainty. Backtest, paper, and live
  results always carry confidence intervals.
- Every surfaced number is labelled backtest / paper / live. Mixing them is a bug.
- A forward record under 30 trades is labelled "insufficient sample" regardless of
  how good the returns look.
- Survivorship bias caveat on all yfinance penny stock backtests.

## SAFETY

- **No credentials in the repo.** Broker keys via keyring only (the #160 pattern).
  Paper keys and live keys are separate credentials, never the same account.
- **Tests never hit a real broker, a real market API, or real money.** Inject fixtures
  at the same seams `markets` / `book_ingest` already use.
- **Anything needing a real broker connection to verify** -> append to
  `docs/trading-live-verify.md`; do not perform it in a loop session.
- Seam rule (#153/#385): no `from plugins.<x> import ...` inside `cerebral/`.
- **`trading_ideas.compile_strategy` is not real sandboxing.** It exec()s
  code ultimately derived from scraped web content, hardened with a
  forbidden-pattern scan + a minimal builtins allowlist (2026-08-22, see
  PR #843 in Landed PRs) -- but that's a partial mitigation, not the
  ADR-0010 sandbox self_dev uses for untrusted code. Route strategy
  execution through that sandbox for real before S5+ runs generated
  strategy code against a live broker connection.

## Future campaigns (explicitly out of scope for S1-S6)

- ML-generated strategies (train models per market/timeframe)
- Signal-space search / genetic strategy generation
- Multi-timeframe analysis (intraday + daily)
- Idea enhancement step (AI critiques hypothesis against book corpus before backtest)
- Autonomous source discovery (crawl trading subreddits, monitor RSS feeds)
- Delayed-fill simulation in the gauntlet
- PR #840 -- S1a (auto-merged by self_dev_campaign)
- PR #840 -- S1a (auto-merged by self_dev_campaign)
