# Fixtures

Saved provider payloads. The test suite parses these and never opens a socket.

## Provenance

Files with a plain name are **recorded**: real responses captured from the live API on
2026-08-05 and saved verbatim, so the schemas here are what the providers actually
return, not what their docs claim.

Files prefixed `synthetic_` are **hand-built**, for edge cases the live responses did
not happen to contain. Each is derived from a recorded payload where possible — the
null-close fixture is the real Brent response with one close set to `null`. They are
labelled because a synthetic fixture proves a guard works; it cannot prove a provider
ever sends that shape.

`synthetic_eia_*` are the exception with no recorded counterpart: EIA is the only
provider needing a credential, and the key lives in a GitHub Actions secret rather
than on the machine these were recorded from. Those two follow EIA's published v2
schema. **The EIA parser is the one parser not yet checked against a real response** —
re-record it from somewhere that has the key.

The numbers in the synthetic files are invented and must never be copied into `data/`.

## Re-recording

The recorded payloads are pinned in time and the tests pass a fixed `today`, so they
stay deterministic. Re-record when a provider changes its schema, and fix the parser
where a fresh payload disagrees with the old one — the fresh payload is right.

## What each file covers

| File | Covers |
| --- | --- |
| `yahoo_brent.json` | A month of real daily bars for `BZ=F` |
| `yahoo_unknown_symbol.json` | The real 404 body for a ticker that does not exist |
| `synthetic_yahoo_null_closes.json` | A holiday: one bar with a `null` close |
| `synthetic_yahoo_gbp_mismatch.json` | A ticker reporting `GBp` where config says `USD` |
| `elexon_market_index.json` | Four settlement days: two complete, one at 46/48, one 3 periods in — plus the second data provider to filter out |
| `octopus_agile.json` | Three days of real half-hourly Agile rates |
| `carbon_intensity.json` | A real London day of half-hourly intensity |
| `synthetic_carbon_partial.json` | A day too sparse to average |
| `frankfurter_latest.json` | A real ECB fixing for GBP against USD and EUR |
| `google_news.xml` | Real Google News RSS, trimmed to six items |
| `synthetic_eia_propane.json` | Newest-first rows, a null value, and a numeric string |
| `synthetic_eia_bad_key.json` | The top-level `error` shape for a rejected key |
