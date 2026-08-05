# Fixtures

Saved provider payloads. The test suite parses these and never opens a socket.

## Provenance — read this before trusting a parser test

These files were **hand-built to each provider's documented response schema**, not
recorded from a live call. The environment they were written in cannot reach any of
the price providers: every one of `query1.finance.yahoo.com`, `api.eia.gov`,
`data.elexon.co.uk`, `api.octopus.energy`, `api.carbonintensity.org.uk` and
`api.frankfurter.app` is blocked by the network allowlist and answers `403` at
CONNECT.

That matters for what these tests can and cannot prove. They pin down the parsing
logic — null handling, date bucketing, partial-day refusal, unit and currency
checks — against the shape each API documents. They **cannot** prove that shape is
what the API actually returns today, and an undocumented endpoint like Yahoo's can
drift without notice.

So: the first time this runs somewhere with network access, replace each file with a
genuinely recorded response and re-run the suite. Where a real payload differs, the
real one is right and the parser needs fixing. Until then, treat a green suite as
evidence the parsers are internally correct, not as evidence the integrations work.

The numbers in these files are invented and must never be copied into `data/`.

## What each file covers

| File | Covers |
| --- | --- |
| `yahoo_brent.json` | Five daily bars, one a holiday with a null close |
| `yahoo_unknown_symbol.json` | The `chart.error` shape for a delisted ticker |
| `yahoo_gbp_mismatch.json` | A ticker reporting `GBp` where config says `USD` |
| `eia_propane.json` | Newest-first rows, a null value, and a numeric string |
| `eia_bad_key.json` | The top-level `error` shape for a rejected key |
| `elexon_market_index.json` | A complete settlement day, a partial next day, and a second data provider to filter out |
| `octopus_agile.json` | A full London day including a negative rate, plus a partial day |
| `carbon_intensity.json` | A day with two periods not yet settled (`actual: null`) |
| `carbon_intensity_partial.json` | A day too sparse to average |
| `frankfurter_latest.json` | An ECB fixing for GBP against USD and EUR |
| `google_news.xml` | Google News RSS, including the nested `<source>` publisher |
