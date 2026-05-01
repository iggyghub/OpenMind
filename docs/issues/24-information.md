## Parent
#1 — PRD: OpenMind v1

## What to build
Information MCP servers: Wikipedia (search and article summary, no API key), Weather (Open-Meteo: forecast and alerts, fully open source), News (headlines via RSS aggregation), Stocks/Crypto (price lookup via public APIs, read-only).

## Acceptance criteria
- [ ] Wikipedia: search by term, return summary and key facts
- [ ] Weather: current conditions and 7-day forecast for any location via Open-Meteo
- [ ] News: top headlines by topic or source via configurable RSS feeds
- [ ] Stocks/Crypto: price, 24h change, market cap for any ticker or coin symbol
- [ ] All work offline for cached data; live data requires internet
- [ ] Demo: "Felix, what is the weather in London this week?" → spoken 7-day forecast
- [ ] Demo: "Felix, what is the Bitcoin price?" → spoken current price and 24h change

## Blocked by
- #7 (MCP orchestrator)
