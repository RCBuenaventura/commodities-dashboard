# commodities-dashboard

A personal, daily-updating dashboard for energy and metals prices — UK and global
benchmarks — with the most relevant news for each commodity.

Built to be **lightweight, static, and free to run**: a Python script fetches prices
once a day in GitHub Actions, appends them to JSON files committed to this repo, and
renders a static site. There is no server, no database, and no subscription.

> **Status:** early development. See [Roadmap](#roadmap) for what works today.

---

## What it tracks

| Group | Instruments |
| --- | --- |
| Crude & refined | Brent, WTI, heating oil |
| Gas & LNG | TTF (Europe), Henry Hub (US) |
| LPG | Mont Belvieu propane |
| UK electricity | Wholesale (Elexon), retail Agile unit rate (Octopus), grid carbon intensity |
| Metals | Gold, silver, copper, aluminium |
| Supporting | GBP/USD and GBP/EUR, so USD- and EUR-quoted prices can be read in sterling |

---

## Architecture

```
GitHub Actions (daily cron)
        │
        ├─ python -m dashboard.fetch    → appends today's prices to data/*.json
        │                                  (committed back to the repo)
        │
        └─ python -m dashboard.render   → renders data/ into a static site/
                                           published via GitHub Pages
```

Price history lives **in git** rather than in a database. At roughly 20 values a day
this costs about 300 KB per year, gives versioned and auditable time-series for free,
and makes each morning's commit diff a readable record of what moved.

The fetcher is designed to **fail soft**: if one source is unreachable, that source is
skipped with a warning, every other instrument still updates, and the affected tile is
rendered with a staleness badge rather than disappearing.

---

## Data sources

Every source below is free. Only one — EIA — requires an API key, and that key is
stored as a GitHub Actions secret (`EIA_API_KEY`), never in the repository.

| Data | Provider | Key required |
| --- | --- | --- |
| Futures & metals | Yahoo Finance chart endpoint | No |
| Official spot prices, LPG | [U.S. EIA Open Data](https://www.eia.gov/opendata/) | Yes (free) |
| UK wholesale electricity | [Elexon Insights](https://developer.data.elexon.co.uk/) | No |
| UK retail electricity | [Octopus Energy API](https://developer.octopus.energy/) | No |
| UK grid carbon intensity | [National Grid ESO](https://carbonintensity.org.uk/) | No |
| FX reference rates | [Frankfurter](https://frankfurter.app/) (ECB data) | No |
| News | Google News RSS, EIA Today in Energy, OilPrice.com | No |

### Known substitutions

Two instruments have no free feed, and the dashboard labels them honestly rather than
implying a price it cannot source:

- **UK NBP gas** — licensed by ICE. TTF is used as a proxy; the two correlate closely
  but are not the same contract.
- **JKM (Asian LNG spot)** — proprietary to Platts. TTF is used as the European proxy.
- **Aluminium** is the COMEX contract (`ALI=F`), not the LME cash settlement, which is
  licensed. Levels differ.

---

## Running it locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
cp .env.example .env          # then add your EIA_API_KEY
python -m dashboard.fetch
python -m dashboard.render
```

The rendered site appears in `site/`; open `site/index.html` in a browser.

---

## Adding a commodity

Adding an instrument should not require touching Python. Add an entry to
`instruments.toml` pointing at an existing source:

```toml
[[instrument]]
id       = "gasoil"
name     = "ICE Gasoil"
group    = "energy"
source   = "yahoo"
symbol   = "..."
unit     = "USD/t"
```

If the commodity needs a provider that isn't wired up yet, add a module under
`src/dashboard/sources/` implementing the `Source` protocol.

---

## Roadmap

- [x] Repository and licence
- [x] Project scaffold, tooling, `CLAUDE.md`
- [ ] Domain models and the `Source` protocol
- [ ] Source modules (Yahoo, EIA, Elexon, Octopus, carbon, FX)
- [ ] JSON store and fetch orchestrator
- [ ] Static renderer and charts
- [ ] Daily GitHub Actions workflow and Pages deploy
- [ ] Per-commodity news feeds
- [ ] Installable on iOS home screen (PWA)

---

## Disclaimer

This is a personal project built for my own reference. Prices are indicative, may be
delayed, and come from free public sources with no guarantee of accuracy or
availability. Several instruments are explicitly proxies for contracts that have no
free feed. **Nothing here is investment advice.**

## Licence

[MIT](LICENSE)
