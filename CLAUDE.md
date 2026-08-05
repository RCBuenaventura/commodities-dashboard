# CLAUDE.md

Guidance for Claude Code when working in this repository — local sessions, cloud
sessions, and routines alike.

## What this project is

A personal, daily-updating dashboard of energy and metals prices with per-commodity
news. It is deliberately **static, serverless, and free to run**: a Python script
fetches prices once a day in GitHub Actions, appends them to JSON files committed to
this repo, and renders a static site published on GitHub Pages.

Price history lives **in git**, not in a database. That is a deliberate choice, not an
oversight — see `README.md` for the reasoning. Do not propose adding a database, a
backend service, or a runtime API.

## Current state

The repository is early. As of the last update it contains only `README.md`, `LICENSE`,
`.gitignore`, `.gitattributes` and this file. **The `src/` tree described below does not
exist yet.** Read before you assume: check what is actually on disk rather than trusting
the layout section as a description of the present.

The `Roadmap` section of `README.md` tracks what has landed. Update its checkboxes in the
same change that lands the work.

## Layout

```
.github/workflows/     update.yml (daily cron), ci.yml (lint + tests)
src/dashboard/
  models.py            Quote, SeriesPoint, NewsItem — frozen dataclasses
  config.py            loads instruments.toml into typed Instrument objects
  sources/
    base.py            the Source protocol + FetchError
    yahoo.py           futures and metals
    eia.py             LPG, official spot history
    elexon.py          UK wholesale power
    octopus.py         UK retail (Agile) power
    carbon.py          grid carbon intensity
    fx.py              GBP reference rates (Frankfurter / ECB)
    news.py            RSS via feedparser
  store.py             append-only JSON history
  fetch.py             orchestrator, entry point
  render.py            Jinja2 -> site/
templates/             index.html.j2 and partials
static/                style.css, app.js, manifest.webmanifest
data/                  committed price history — the "database"
tests/                 fixtures are recorded API responses; no live calls
instruments.toml       the only file you edit to add a commodity
```

## The rules that matter

These are the ones that are expensive to get wrong. Everything else is ordinary taste.

**1. Never fabricate a price.** This is the single most important rule in the project. If
a source is unreachable, returns malformed data, or has no value for today, the correct
behaviour is to skip it and let the UI show the last known value with a staleness badge.
Never interpolate, never carry a value forward silently, never substitute a plausible
number. A wrong price shown as current is worse than no price.

**2. Fail soft, per source.** `fetch.py` wraps every source in its own `try`/`except`. One
failing source logs a warning and is skipped; every other instrument still updates and the
run still exits zero. Exit non-zero **only** if every source failed. Record per-source
last-success timestamps in `data/_meta.json` so the renderer can badge stale tiles.

**3. Label proxies honestly.** Three instruments are stand-ins for contracts with no free
feed, and the dashboard says so rather than implying a price it cannot source:
- UK NBP gas — licensed by ICE; TTF is used as a proxy
- JKM (Asian LNG spot) — proprietary to Platts; TTF is used as the European proxy
- Aluminium — COMEX `ALI=F`, **not** LME cash settlement; levels differ

Never relabel a proxy as the real contract, and never drop the caveat from the UI to make
a tile look tidier.

**4. Secrets stay out of the repo.** `EIA_API_KEY` is the only credential in the project.
It lives in a GitHub Actions secret and, locally, in `.env` (gitignored). `.env.example`
carries the key name with an empty value and nothing else. Never write a real key into a
file, a test fixture, a commit message, or a log line.

**5. Adding a commodity must not require touching Python.** New instruments are an entry
in `instruments.toml` pointing at an existing source. Only add a module under
`src/dashboard/sources/` when a genuinely new provider is needed. If a change would make
`instruments.toml` insufficient, say so before writing it.

**6. Nothing here is investment advice.** Keep the disclaimer in `README.md` and on the
rendered page. Do not add price forecasts, buy/sell signals, or commentary that reads as a
recommendation.

## Conventions

**Python 3.12**, `src/` layout, package name `dashboard`, built with hatchling.
Runtime deps: `httpx`, `jinja2`, `feedparser`. Dev deps: `pytest`, `ruff`, `mypy`.

- `ruff` for both lint and format; `mypy` in strict mode. Both configured in
  `pyproject.toml`. Code must pass `ruff check`, `ruff format --check`, and `mypy` before
  it is committed.
- Domain types are **frozen dataclasses**. Dates are `datetime.date`, not strings,
  everywhere except at the JSON boundary.
- Every source implements the same `Source` protocol: an `id: str` and
  `fetch(instruments) -> list[Quote]`. Raise `FetchError` for anything the orchestrator
  should treat as a skippable source failure.
- HTTP goes through `httpx` with an explicit timeout. Yahoo's endpoint is undocumented and
  needs a browser `User-Agent`; it carries no uptime promise, which is exactly why rule 2
  exists.

### Data format

One JSON file per instrument in `data/`:

```json
{"instrument": "brent", "unit": "USD/bbl", "points": [{"d": "2026-08-04", "v": 80.67}]}
```

Appending today's point is **idempotent** — re-running the fetcher must never duplicate a
date. Points stay sorted by date and are pruned to the last 400 on write.

### Tests

`pytest`, with fixtures in `tests/fixtures/` that are **recorded API responses**. The test
suite makes no live network calls, ever — it must pass on a plane. When adding a source,
save a real response as a fixture and test the parser against it. Always cover the failure
path, not just the happy one.

### Commits

- Conventional-commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.
- The daily data commit is `chore: data YYYY-MM-DD [skip ci]`. The `[skip ci]` marker is
  load-bearing — without it the bot's own commit retriggers the workflow in a loop.
- Commit author email is set repo-locally to the GitHub noreply address
  (`75373676+RCBuenaventura@users.noreply.github.com`). This repo is intended to go public,
  so the owner's personal address must never enter the history. If you are running in a
  cloud session or routine and git is not already configured with that address, set it
  before committing.

### Never commit

`site/` (generated by `render.py` and published by Actions), `.env`, `.venv/`, or anything
under a tooling cache. All are gitignored — do not add exceptions.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
cp .env.example .env          # then add your EIA_API_KEY
python -m dashboard.fetch
python -m dashboard.render
```

Output lands in `site/`; open `site/index.html`.

## Working autonomously

For cloud sessions and routines, where there is no one to ask mid-run:

- **You have only what is in this repo.** No local config, no `.env`, no saved credentials.
  If a task genuinely needs `EIA_API_KEY`, it must come from the cloud environment's
  variables — do not invent one and do not skip the check that would have caught its
  absence.
- **Network access is allowlisted.** The default cloud environment permits package
  registries and common development domains only. Requests to the price providers
  (`query1.finance.yahoo.com`, `api.eia.gov`, `data.elexon.co.uk`, `api.octopus.energy`,
  `api.carbonintensity.org.uk`, `api.frankfurter.app`, `news.google.com`) fail with `403`
  and `x-deny-reason: host_not_allowed` unless those hosts have been added to the
  environment. A `403` from one of these is an **environment** problem, not a broken
  source — report it, do not work around it by hardcoding values or disabling the source.
- **Push to a branch, never to `main`.** Open a PR and let it be reviewed. The daily data
  commit from `update.yml` is the only thing that writes to `main`.
- **Prefer stopping to guessing.** If a task is ambiguous in a way that could put a wrong
  number on the dashboard, do the unambiguous part, then say plainly what you did not do
  and why. Rule 1 outranks finishing the task.

## House style for prose

The README and the rendered page are written for someone who knows commodities and does
not need crude, TTF, or Mont Belvieu explained. Keep the register plain and factual. State
limitations directly rather than hedging around them — the honesty about proxies and data
quality is a feature of this project, not a disclaimer to be minimised.
