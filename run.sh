# MarketScreen — automated daily screening deck

A self-running version of your daily screen. It scans the US market, applies your
"up/down on larger volume" filters, computes your 7-test composite score, pulls each
survivor's financials + peer comps, and writes a single **dashboard.html** you flip
through with the keyboard.

## Files
- **screener.py** — daily engine (scan → score → financials → peers → dashboard).
- **dashboard.py** — the deck renderer (HTML/JS). Shared by the engine and the demo.
- **make_demo.py** — builds the preview seeded from your existing workbook.
- **dashboard.html** — the deck. Open in any browser; works offline once built.
- **daily-screen.yml** — GitHub Action for hands-off cloud refresh (see below).

## Setup (once)
```bash
pip install yfinance pandas numpy
```

## What's in the detail panel
Score breakdown (the 7 tests lit/unlit) · Snapshot (mkt cap, EV, $ flow, rel vol,
52-wk range position, beta) · **Valuation multiples** (EV/EBITDA, EV/EBIT, EV/Sales,
P/E, P/FCF, Net debt/EBITDA) · **Quality & margins** (gross/EBIT/net margin, ROIC, ROE,
FCF yield) · **Peer comps** (4 peers, cheaper-than-median in green) · your model block ·
balance & cash (incl. div yield, cash/share, capex/share) · YoY/QoQ trends for Revenue,
EBIT, CFO, CFI, CFF, FCF, cash, debt.

## Using the deck
- **j / k** (or ↑/↓) flip names · **Tab** switches side · **min score** slider filters
  (default 5 = your ">4") · **filter** box searches.

# ─────────────────────────────────────────────
# DATA SOURCE — which database
# ─────────────────────────────────────────────
- **Prices + financials + most multiples:** Yahoo Finance via `yfinance` — free, no key.
- **Peer list:** Financial Modeling Prep (best) or Finnhub. Both have free keys.
  Only the *peer list* needs a key; each peer's multiples come from Yahoo.
  Without a key, peers fall back to same-side names from that day's scan.

Add keys as environment variables (engine reads them automatically):
```bash
export FMP_API_KEY=your_key          # https://site.financialmodelingprep.com  (free tier)
export FINNHUB_API_KEY=your_key      # https://finnhub.io                      (free tier)
```

# ─────────────────────────────────────────────
# DAILY AUTO-REFRESH — three ways, pick one
# ─────────────────────────────────────────────

## A) Local + scheduler (simplest)
Schedule `screener.py` to rebuild dashboard.html each morning; keep the tab open.
The page polls itself and **auto-reloads when a fresh build lands** (also on tab focus).
- **macOS/Linux (cron):** `0 7 * * 1-5 cd /path/to/folder && /usr/bin/python3 screener.py`
- **Windows (Task Scheduler):** daily trigger → action `python C:\path\screener.py`
Open `dashboard.html` once and leave it pinned.

## B) Local web server (so the self-reload polling is reliable)
```bash
python -m http.server 8000      # in the folder; then bookmark:
# http://localhost:8000/dashboard.html
```
Run the scheduler from (A) to rebuild the file; the open tab refreshes itself.

## C) Cloud, fully hands-off (recommended) — GitHub Actions + Pages
No machine needs to be on. The Action runs the screener on a daily cron *in the cloud*
and publishes the deck to a URL you bookmark.
1. Put these files in a GitHub repo.
2. The workflow is already at `.github/workflows/daily-screen.yml` — nothing to move.
3. Repo **Settings → Pages → Source: GitHub Actions**.
4. (Optional) **Settings → Secrets and variables → Actions** → add `FMP_API_KEY` / `FINNHUB_API_KEY`.
5. Bookmark the Pages URL (e.g. `https://<you>.github.io/<repo>/`). Fresh every weekday, any device.
Adjust the cron time in the workflow (it's in UTC).

## Tuning (top of screener.py)
`PRICE_MIN, CHG_ABS_MIN, AVG_VOL_MIN, VOL_MIN, REL_VOL_MIN` = the screen filters.
`SCORE_MIN` (5), `TOP_N` (10/side), `SCORE_FALLBACK`, `UNIVERSE_FILE`, `MAX_UNIVERSE`.

## Two things worth knowing
1. **Scoring fix.** Your Excel awards points when a metric cell is *blank* (Excel treats
   empty > number as TRUE), inflating banks/financials with missing net-cash fields.
   This engine fails the test when data is missing — the correct behavior. One-line revert
   in `fundamentals()` if you want to match the old numbers exactly.
2. **Multiples source.** EV/EBITDA, EV/Sales, etc. come from Yahoo's `info` fields where
   available, with statement-based fallbacks. yfinance labels shift between versions;
   `_pick()` does fuzzy lookups. Empty rows mean that figure wasn't available for the ticker.
