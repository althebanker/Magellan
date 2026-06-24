#!/usr/bin/env python3
"""
MarketScreen - automated daily screening deck (independent of Excel/TradingView).

Pipeline:
  1. Screen the market (TradingView by default, or a Yahoo universe scan).
  2. Compute the 7-test composite score (your Excel logic) from the screen data.
  3. Keep score >= SCORE_MIN, take the TOP_N most liquid per side.
  4. Pull statements (Yahoo) for the few finalists -> YoY/QoQ trends + peer comps.
  5. Write a single self-contained dashboard.html (data embedded, opens offline).

Run it daily (cron / Task Scheduler / GitHub Actions). Re-running overwrites dashboard.html.
"""

import os, sys, json, time, math, subprocess, datetime as dt
# self-install third-party deps so the GitHub workflow's install line never needs editing
for _pkg, _imp in [("tradingview-screener", "tradingview_screener"),
                    ("yfinance", "yfinance"), ("pandas", "pandas")]:
    try:
        __import__(_imp)
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", _pkg], check=False)

FMP_KEY     = os.getenv("FMP_API_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")

# ----------------------------------------------------------------------------- CONFIG
CONFIG = dict(
    SOURCE         = "tradingview",
    PRICE_MIN      = 3.0,
    CHG_ABS_MIN    = 2.0,
    AVG_VOL_MIN    = 250_000,
    VOL_MIN        = 100_000,
    REL_VOL_MIN    = 1.3,
    SCORE_MIN      = 5,
    TOP_N          = 10,
    SCORE_FALLBACK = 4,
    ENRICH_MAX     = 45,
    DETAIL_N       = 40,
    TV_LIMIT       = 2000,
    UNIVERSE_FILE  = "",
    MAX_UNIVERSE   = 0,
    OUT_HTML       = "dashboard.html",
    HISTORY_FILE   = "history.json",  # accumulating daily archive of score>=HISTORY_SCORE picks
    HISTORY_SCORE  = 4,               # min score a name needs to be saved to the history archive
                                      #   (the backtest score slider re-filters this 4..7 after the fact)
    DL_CHUNK       = 250,
    FUND_SLEEP     = 0.4,
    WORKER_URL     = "",   # Cloudflare Worker base URL for live price + Magellan score of
                           #   tickers not in today's screen (see worker.js). Leave blank to
                           #   fall back to the legacy in-browser price fetch (price only).
)

# ----------------------------------------------------------------------------- MARKET window
# "What's priced in today" landing dashboard. Everything is fetched at build time and baked
# into the page (same pattern as histmap) so it renders on GitHub Pages with no cross-origin
# fetch. Sources: equity/commodity/FX tiles + Fed funds futures + the SPY option chain from
# Yahoo; US yields, 2s10s, 10Y real (TIPS), 5y5y, HY OAS from FRED (free, no key); German
# yields from the ECB data portal (euro-area AAA curve, a Bund proxy); Japan yields from the
# Japanese MoF; macro headlines from public RSS. Any feed that fails degrades to a dash.
#   The two policy anchors (Fed target midpoint, ECB deposit rate) move only a few times a
#   year - update them here when the central banks change rates, and roll the FED contract.
MARKET = dict(
    # (label, yahoo_symbol) tiles, grouped:
    INDEX = [("S&P fut", "ES=F"), ("Nasdaq fut", "NQ=F"), ("Dow fut", "YM=F"), ("Euro Stoxx", "^STOXX50E")],
    COMMODITIES = [("Gold", "GC=F"), ("Silver", "SI=F"), ("WTI crude", "CL=F"), ("Brent crude", "BZ=F")],
    FX_EUR = [("EUR/USD", "EURUSD=X"), ("EUR/GBP", "EURGBP=X"), ("EUR/JPY", "EURJPY=X"), ("EUR/CHF", "EURCHF=X")],
    FX_USD = [("DXY", "DX-Y.NYB"), ("USD/JPY", "USDJPY=X"), ("GBP/USD", "GBPUSD=X"), ("USD/CHF", "USDCHF=X"), ("USD/CAD", "USDCAD=X")],
    US_BONDS = [("US 2Y", "DGS2"), ("US 5Y", "DGS5"), ("US 10Y", "DGS10"), ("US 30Y", "DGS30")],  # FRED ids
    DE_BONDS = [("Germany 2Y", "2Y"), ("Germany 10Y", "10Y")],   # ECB AAA euro-area curve (Bund proxy)
    JP_BONDS = [("Japan 2Y", "2"), ("Japan 10Y", "10")],         # Japan MoF JGB column years
    # what's-priced-into-rates FRED series (free, no key):
    FRED = dict(two_ten="T10Y2Y", real_10y="DFII10", five_y_five="T5YIFR", hy_oas="BAMLH0A0HYM2"),
    # Fed: implied cuts from a dated 30-day Fed Funds future. Update contract + target_mid as policy moves.
    FED = dict(target_mid=3.625, contract="ZQU26=F", label="Fed \u2013 cuts priced by Sep", horizon_bp=50),
    # ECB: no free futures feed, so estimated from German 2Y vs the deposit rate (update depo on ECB moves).
    ECB = dict(depo=2.00, label="ECB \u2013 2Y vs deposit (est.)", horizon_bp=50),
    # Alpha Risk Matrix: 25-delta 1M risk reversal = IV(25d put) - IV(25d call) on the option chain.
    ARM = dict(underlying="SPY", target_days=30, r=0.04, q=0.012),
    HISTORY_FILE = "market_history.json",   # rolling daily store that builds the 30-day percentile
    # top macro headlines (first feeds that return items win; all fail -> graceful note):
    NEWS_FEEDS = [
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "https://www.investing.com/rss/news_25.rss",
    ],
)

# ----------------------------------------------------------------------------- universe
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

def load_universe(cfg):
    if cfg["UNIVERSE_FILE"]:
        import pandas as pd
        df = pd.read_csv(cfg["UNIVERSE_FILE"])
        syms = df.iloc[:, 0].astype(str).str.strip().str.upper().tolist()
    else:
        syms = _fetch_nasdaqtrader()
    out = []
    for s in syms:
        s = s.strip().upper()
        if not s or any(c in s for c in " $/^"):
            continue
        s = s.replace(".", "-")
        if len(s) > 6:
            continue
        out.append(s)
    out = sorted(set(out))
    if cfg["MAX_UNIVERSE"]:
        out = out[: cfg["MAX_UNIVERSE"]]
    return out

def _fetch_nasdaqtrader():
    import urllib.request
    syms = []
    for url, sym_col, test_col in [(NASDAQ_LISTED, 0, 3), (OTHER_LISTED, 0, 6)]:
        try:
            raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"  ! could not fetch {url}: {e}")
            continue
        lines = raw.splitlines()
        for ln in lines[1:]:
            if ln.startswith("File Creation Time"):
                continue
            parts = ln.split("|")
            if len(parts) <= max(sym_col, test_col):
                continue
            if parts[test_col].strip().upper() == "Y":
                continue
            syms.append(parts[sym_col].strip())
    return syms

# ----------------------------------------------------------------------------- stage 1: price/volume screen
def screen_volume(universe, cfg):
    import yfinance as yf
    import pandas as pd, numpy as np
    rows = {}
    chunks = [universe[i:i+cfg["DL_CHUNK"]] for i in range(0, len(universe), cfg["DL_CHUNK"])]
    for ci, chunk in enumerate(chunks, 1):
        print(f"  price/vol batch {ci}/{len(chunks)} ({len(chunk)} tickers)")
        try:
            data = yf.download(chunk, period="4mo", interval="1d",
                               group_by="ticker", auto_adjust=False,
                               threads=True, progress=False)
        except Exception as e:
            print(f"    ! batch failed: {e}"); continue
        for sym in chunk:
            try:
                df = data[sym] if len(chunk) > 1 else data
                df = df.dropna(subset=["Close", "Volume"])
                if len(df) < 30:
                    continue
                close = df["Close"]; vol = df["Volume"]
                price = float(close.iloc[-1]); prev = float(close.iloc[-2])
                if prev <= 0: continue
                chg = (price/prev - 1) * 100
                v_today = float(vol.iloc[-1])
                avg60 = float(vol.iloc[-61:-1].mean()) if len(vol) > 61 else float(vol.iloc[:-1].mean())
                if avg60 <= 0: continue
                relvol = v_today / avg60
                if (price > cfg["PRICE_MIN"] and abs(chg) > cfg["CHG_ABS_MIN"]
                        and avg60 > cfg["AVG_VOL_MIN"] and v_today > cfg["VOL_MIN"]
                        and relvol > cfg["REL_VOL_MIN"]):
                    rows[sym] = dict(sym=sym, price=price, chg=chg, vol=v_today,
                                     relvol=relvol, flowmn=price*v_today/1e6)
            except Exception:
                continue
    ups   = [r for r in rows.values() if r["chg"] > 0]
    downs = [r for r in rows.values() if r["chg"] < 0]
    print(f"  screen survivors: {len(ups)} up, {len(downs)} down")
    return ups, downs

# ----------------------------------------------------------------------------- stage 2: fundamentals + score
def _pick(df, *names):
    if df is None or getattr(df, "empty", True):
        return None
    idx = {str(i).lower(): i for i in df.index}
    for n in names:
        nl = n.lower()
        if nl in idx:
            return df.loc[idx[nl]]
        for k, orig in idx.items():
            if nl in k:
                return df.loc[orig]
    return None

def _series(row):
    if row is None: return []
    out = []
    for v in row.tolist():
        try:
            f = float(v)
            out.append(None if (f != f) else f)
        except Exception:
            out.append(None)
    return out

def fundamentals(sym, base, cfg, sector_pool=None, with_peers=False):
    import yfinance as yf
    t = yf.Ticker(sym)
    info = {}
    try: info = t.info or {}
    except Exception: pass
    inc_a, inc_q = _safe(t, "income_stmt"), _safe(t, "quarterly_income_stmt")
    cf_a,  cf_q  = _safe(t, "cashflow"),    _safe(t, "quarterly_cashflow")
    bs_a,  bs_q  = _safe(t, "balance_sheet"), _safe(t, "quarterly_balance_sheet")

    name   = info.get("longName") or info.get("shortName") or sym
    sector = info.get("sector") or info.get("industry") or ""
    mcap   = info.get("marketCap")
    pe     = info.get("trailingPE")
    eps    = info.get("trailingEps")
    shares = info.get("sharesOutstanding")

    rev_a = _pick(inc_a, "Total Revenue", "Revenue")
    ebit_a = _pick(inc_a, "EBIT", "Operating Income")
    ni_row = _pick(inc_a, "Net Income", "Net Income Common Stockholders")
    ni = float(ni_row.iloc[0]) if ni_row is not None and len(ni_row) else info.get("netIncomeToCommon")

    cash_row = _pick(bs_a, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash")
    cash = float(cash_row.iloc[0]) if cash_row is not None and len(cash_row) else info.get("totalCash")
    ltd_row = _pick(bs_a, "Long Term Debt")
    ltdebt = float(ltd_row.iloc[0]) if ltd_row is not None and len(ltd_row) else (info.get("longTermDebt") or 0)
    eq_row = _pick(bs_a, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
    equity = float(eq_row.iloc[0]) if eq_row is not None and len(eq_row) else None

    roe = info.get("returnOnEquity")
    roe = roe*100 if roe is not None else (ni/equity*100 if (ni and equity) else None)

    fcf_a  = _pick(cf_a, "Free Cash Flow")
    cfo_a  = _pick(cf_a, "Operating Cash Flow", "Total Cash From Operating Activities")
    capex_a= _pick(cf_a, "Capital Expenditure", "Capital Expenditures")
    fcf_ttm = info.get("freeCashflow")
    fcfps = (fcf_ttm/shares) if (fcf_ttm and shares) else (
            (float(fcf_a.iloc[0])/shares) if (fcf_a is not None and len(fcf_a) and shares) else None)

    epsg = info.get("earningsGrowth")
    epsg = epsg*100 if epsg is not None else _ttm_yoy(_pick(inc_q, "Net Income"))

    netcash = ((cash - ltdebt)/shares) if (cash is not None and shares) else None
    swc     = (base["price"] - netcash) if netcash is not None else None
    newpe   = (swc/eps) if (swc is not None and eps) else None
    fv      = (eps*20) if eps else None
    updown  = ((fv - base["price"])/base["price"]) if fv else None
    peg     = (newpe/epsg) if (newpe is not None and epsg) else None
    debtcov = (1 if (ni is not None and ni < 0) else ((ltdebt - 2*ni)/1e6 if ni is not None else None))
    pediff  = (newpe - pe) if (newpe is not None and pe) else None
    cashport= (netcash/base["price"]) if netcash is not None else None

    rev_ttm = info.get("totalRevenue") or (float(rev_a.iloc[0]) if rev_a is not None and len(rev_a) else None)
    om      = info.get("operatingMargins")
    ebit_ttm= (om*rev_ttm) if (om is not None and rev_ttm) else (float(ebit_a.iloc[0]) if ebit_a is not None and len(ebit_a) else None)
    ev      = info.get("enterpriseValue") or ((mcap + (info.get("totalDebt") or ltdebt) - (cash or 0)) if mcap else None)
    total_debt = info.get("totalDebt") or ltdebt or 0
    ebitda  = info.get("ebitda")
    fcf_ttm2= info.get("freeCashflow")
    dy      = info.get("dividendYield")
    div_yield = (dy if (dy and dy > 1) else (dy*100 if dy else None))
    hi, lo, px = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow"), base["price"]
    metrics = dict(
        ev=ev,
        evEbitda=info.get("enterpriseToEbitda") or ((ev/ebitda) if (ev and ebitda) else None),
        evEbit=(ev/ebit_ttm) if (ev and ebit_ttm and ebit_ttm > 0) else None,
        evSales=info.get("enterpriseToRevenue") or ((ev/rev_ttm) if (ev and rev_ttm) else None),
        pfcf=(mcap/fcf_ttm2) if (mcap and fcf_ttm2 and fcf_ttm2 > 0) else None,
        netDebtEbitda=((total_debt-(cash or 0))/ebitda) if ebitda else None,
        grossMargin=(info.get("grossMargins")*100) if info.get("grossMargins") is not None else None,
        ebitMargin=(om*100) if om is not None else None,
        netMargin=(info.get("profitMargins")*100) if info.get("profitMargins") is not None else None,
        roic=((ebit_ttm*0.79)/(total_debt+equity)*100) if (ebit_ttm and equity and (total_debt+equity)) else None,
        fcfYield=(fcf_ttm2/mcap*100) if (fcf_ttm2 and mcap) else None,
        beta=info.get("beta"),
        divYield=div_yield,
        range52=((px-lo)/(hi-lo)) if (hi and lo and hi > lo) else None,
        revg=base.get("revg") or ((info.get("revenueGrowth")*100) if info.get("revenueGrowth") is not None else None),
    )

    tests = [
        (updown  is not None and updown > 0,                    "Upside vs fair value"),
        (peg     is not None and 0.5 <= peg <= 1,               "PEG 0.5-1.0"),
        (debtcov is not None and debtcov < 0,                   "Debt covered by earnings"),
        (pediff  is not None and pediff < 0,                    "Cheaper ex-cash P/E"),
        (netcash is not None and netcash > 0,                   "Net cash positive"),
        (cashport is not None and cashport > 0.1,               "Cash > 10% of price"),
        (roe     is not None and roe > 15,                      "ROE > 15%"),
    ]
    score = sum(1 for ok, _ in tests if ok)

    def trend(row_a, row_q, label, kind):
        return dict(label=label, kind=kind,
                    annual=dict(dates=_dates(inc_a if kind=="inc" else cf_a if kind=="cf" else bs_a),
                                values=_series(row_a)),
                    quarter=dict(dates=_dates(inc_q if kind=="inc" else cf_q if kind=="cf" else bs_q),
                                 values=_series(row_q)))

    peers = []
    if with_peers:
        try:
            plist = get_peers(sym, sector_pool or [])
            peers = [peer_multiples(p) for p in plist]
        except Exception as e:
            print(f"      peers failed for {sym}: {e}")

    return dict(
        name=name, sector=sector, **base,
        mcap=mcap, mcapmn=(mcap/1e6 if mcap else None), pe=pe, eps=eps, roe=roe,
        cash=cash, ltdebt=ltdebt, shares=shares, ni=ni, fcfps=fcfps,
        netcash=netcash, swc=swc, newpe=newpe, fv=fv, updown=updown,
        peg=peg, debtcov=debtcov, pediff=pediff, cashport=cashport, peers=peers, **metrics,
        cashps=(cash/shares if (cash and shares) else None),
        capexps=(abs(float(capex_a.iloc[0]))/shares if (capex_a is not None and len(capex_a) and shares) else None),
        score=score, tests=[dict(ok=ok, label=l) for ok, l in tests],
        trends=dict(
            revenue=trend(rev_a, _pick(inc_q,"Total Revenue","Revenue"), "Revenue", "inc"),
            ebit   =trend(ebit_a, _pick(inc_q,"EBIT","Operating Income"), "EBIT", "inc"),
            cfo    =trend(cfo_a, _pick(cf_q,"Operating Cash Flow","Total Cash From Operating Activities"), "CFO", "cf"),
            cfi    =trend(_pick(cf_a,"Investing Cash Flow","Total Cashflows From Investing Activities"),
                          _pick(cf_q,"Investing Cash Flow","Total Cashflows From Investing Activities"), "CFI", "cf"),
            cff    =trend(_pick(cf_a,"Financing Cash Flow","Total Cash From Financing Activities"),
                          _pick(cf_q,"Financing Cash Flow","Total Cash From Financing Activities"), "CFF", "cf"),
            fcf    =trend(fcf_a, _pick(cf_q,"Free Cash Flow"), "FCF", "cf"),
            cash   =trend(cash_row, _pick(bs_q,"Cash And Cash Equivalents","Cash"), "Cash & equiv.", "bs"),
            ltdebt =trend(ltd_row, _pick(bs_q,"Long Term Debt"), "Long-term debt", "bs"),
        ),
    )

def _safe(t, attr):
    try: return getattr(t, attr)
    except Exception: return None

def _dates(df):
    if df is None or getattr(df, "empty", True): return []
    return [str(c)[:10] for c in df.columns]

def _ttm_yoy(row):
    s = _series(row)
    s = [x for x in s if x is not None]
    if len(s) < 8: return None
    cur, prior = sum(s[:4]), sum(s[4:8])
    return (cur/prior - 1)*100 if prior else None

# ----------------------------------------------------------------------------- peers / comps
def get_peers(sym, sector_pool):
    import urllib.request
    try:
        if FMP_KEY:
            u=f"https://financialmodelingprep.com/api/v4/stock_peers?symbol={sym}&apikey={FMP_KEY}"
            d=json.loads(urllib.request.urlopen(u,timeout=15).read())
            ps=d[0].get("peersList",[]) if d else []
            if ps: return ps[:4]
        if FINNHUB_KEY:
            u=f"https://finnhub.io/api/v1/stock/peers?symbol={sym}&token={FINNHUB_KEY}"
            ps=[p for p in json.loads(urllib.request.urlopen(u,timeout=15).read()) if p!=sym]
            if ps: return ps[:4]
    except Exception as e:
        print(f"      peer lookup failed for {sym}: {e}")
    return [s for s in sector_pool if s!=sym][:4]

def peer_multiples(sym):
    import yfinance as yf
    try:
        i=yf.Ticker(sym).info or {}
        rev=i.get("totalRevenue"); om=i.get("operatingMargins")
        ev=i.get("enterpriseValue"); ebit=(om*rev) if (om and rev) else None
        return dict(sym=sym,
            evEbitda=i.get("enterpriseToEbitda"),
            evEbit=(ev/ebit) if (ev and ebit) else None,
            pe=i.get("trailingPE"),
            evSales=i.get("enterpriseToRevenue"),
            revg=(i.get("revenueGrowth")*100) if i.get("revenueGrowth") is not None else None,
            ebitMargin=(om*100) if om is not None else None)
    except Exception:
        return dict(sym=sym)

# ----------------------------------------------------------------------------- shared scoring
def compute_derived(d, cfg):
    price=d.get("price"); eps=d.get("eps"); pe=d.get("pe"); roe=d.get("roe")
    epsg=d.get("epsg"); cash=d.get("cash"); ltdebt=d.get("ltdebt") or 0
    shares=d.get("shares"); ni=d.get("ni")
    netcash = ((cash-ltdebt)/shares) if (cash is not None and shares) else None
    swc     = (price-netcash) if (netcash is not None and price is not None) else None
    newpe   = (swc/eps) if (swc is not None and eps) else None
    fv      = (eps*20) if eps else None
    updown  = ((fv-price)/price) if (fv and price) else None
    peg     = (newpe/epsg) if (newpe is not None and epsg) else None
    debtcov = (1 if (ni is not None and ni<0) else ((ltdebt-2*ni)/1e6 if ni is not None else None))
    pediff  = (newpe-pe) if (newpe is not None and pe) else None
    cashport= (netcash/price) if (netcash is not None and price) else None
    tests = [
        (updown   is not None and updown>0,            "Upside vs fair value"),
        (peg      is not None and 0.5<=peg<=1,         "PEG 0.5-1.0"),
        (debtcov  is not None and debtcov<0,           "Debt covered by earnings"),
        (pediff   is not None and pediff<0,            "Cheaper ex-cash P/E"),
        (netcash  is not None and netcash>0,           "Net cash positive"),
        (cashport is not None and cashport>0.1,        "Cash > 10% of price"),
        (roe      is not None and roe>15,              "ROE > 15%"),
    ]
    mcap=d.get("mcap"); ebitda=d.get("ebitda"); rev=d.get("rev"); ebit=d.get("ebit")
    fcf=d.get("fcf"); total_debt=d.get("ltdebt") or ltdebt
    ev=(mcap+total_debt-(cash or 0)) if mcap else None
    d.update(
        netcash=netcash, swc=swc, newpe=newpe, fv=fv, updown=updown, peg=peg,
        debtcov=debtcov, pediff=pediff, cashport=cashport,
        mcapmn=(mcap/1e6 if mcap else None), fcfps=d.get("fcfps"),
        cashps=(cash/shares if (cash and shares) else None), capexps=d.get("capexps"),
        ev=ev, evEbitda=(ev/ebitda if (ev and ebitda) else None),
        evEbit=(ev/ebit if (ev and ebit and ebit>0) else None),
        evSales=(ev/rev if (ev and rev) else None),
        pfcf=(mcap/fcf if (mcap and fcf and fcf>0) else None),
        netDebtEbitda=((total_debt-(cash or 0))/ebitda if ebitda else None),
        ebitMargin=(ebit/rev*100 if (ebit and rev) else None),
        netMargin=(ni/rev*100 if (ni and rev) else None),
        grossMargin=d.get("grossMargin"), roic=d.get("roic"),
        fcfYield=(fcf/mcap*100 if (fcf and mcap) else None),
        beta=d.get("beta"), divYield=d.get("divYield"), range52=d.get("range52"),
        score=sum(1 for ok,_ in tests if ok),
        tests=[dict(ok=ok, label=l) for ok,l in tests], peers=[],
    )
    return d

# ----------------------------------------------------------------------------- TradingView screen
def screen_tradingview(cfg):
    from tradingview_screener import Query, Column as C
    core  = ["name","description","type","close","change","volume","relative_volume_10d_calc",
             "market_cap_basic","price_earnings_ttm","sector","earnings_per_share_diluted_ttm",
             "return_on_equity"]
    extra = ["revenue_growth_fy_yoy","earnings_per_share_diluted_yoy_growth_ttm",
             "total_shares_outstanding_fundamental","long_term_debt_fy","net_income_fy",
             "free_cash_flow_per_share_ttm","cash_n_equivalents_fy","total_revenue_ttm","ebitda_ttm"]
    base_flt = [C("close") > cfg["PRICE_MIN"], C("volume") > cfg["VOL_MIN"],
                C("average_volume_60d_calc") > cfg["AVG_VOL_MIN"],
                C("relative_volume_10d_calc") > cfg["REL_VOL_MIN"]]
    def run(fields, change_flt, ascending):
        return Query().select(*fields).where(*(base_flt + [change_flt]))\
                      .order_by("change", ascending=ascending).limit(cfg["TV_LIMIT"]).get_scanner_data()
    def fetch(fields):
        _, up   = run(fields, C("change") >  cfg["CHG_ABS_MIN"], False)
        _, down = run(fields, C("change") < -cfg["CHG_ABS_MIN"], True)
        return up, down
    try:
        up_df, down_df = fetch(core + extra)
    except Exception as e:
        print(f"  TradingView extended select failed ({e}); falling back to core fields.")
        up_df, down_df = fetch(core)
    print(f"  TradingView: {len(up_df)} up, {len(down_df)} down")
    print(f"  columns: {list(up_df.columns)}")
    def to_recs(df):
        out = []
        for _, row in df.iterrows():
            g = lambda k: (row[k] if k in df.columns and row[k]==row[k] else None)
            sym = (g("name") or "").upper()
            if not sym: continue
            typ = (g("type") or "").lower()
            if typ in ("fund","etf","etn","structured","right","warrant","spc","bond"):
                continue
            price = g("close"); vol = g("volume")
            shares = g("total_shares_outstanding_fundamental")
            fcfps = g("free_cash_flow_per_share_ttm")
            d = dict(sym=sym, name=g("description") or sym, sector=g("sector") or "",
                     price=price, chg=g("change"), vol=vol, relvol=g("relative_volume_10d_calc"),
                     flowmn=(price*vol/1e6 if (price and vol) else None),
                     mcap=g("market_cap_basic"), pe=g("price_earnings_ttm"),
                     eps=g("earnings_per_share_diluted_ttm"), roe=g("return_on_equity"),
                     epsg=g("earnings_per_share_diluted_yoy_growth_ttm"),
                     revg=g("revenue_growth_fy_yoy"), shares=shares,
                     ltdebt=g("long_term_debt_fy"), ni=g("net_income_fy"),
                     cash=g("cash_n_equivalents_fy"),
                     rev=g("total_revenue_ttm"), ebitda=g("ebitda_ttm"),
                     fcfps=fcfps, fcf=(fcfps*shares if (fcfps and shares) else None))
            out.append(compute_derived(d, cfg))
        return out
    ups, downs = to_recs(up_df), to_recs(down_df)
    print(f"  scored: {len(ups)} up, {len(downs)} down")
    return ups, downs

# ----------------------------------------------------------------------------- Yahoo trends
def yahoo_trends(sym):
    import yfinance as yf
    t = yf.Ticker(sym)
    inc_a, inc_q = _safe(t,"income_stmt"), _safe(t,"quarterly_income_stmt")
    cf_a,  cf_q  = _safe(t,"cashflow"),    _safe(t,"quarterly_cashflow")
    bs_a,  bs_q  = _safe(t,"balance_sheet"), _safe(t,"quarterly_balance_sheet")
    def trend(ra, rq, label, kind):
        ad = inc_a if kind=="inc" else cf_a if kind=="cf" else bs_a
        qd = inc_q if kind=="inc" else cf_q if kind=="cf" else bs_q
        return dict(label=label, annual=dict(dates=_dates(ad), values=_series(ra)),
                    quarter=dict(dates=_dates(qd), values=_series(rq)))
    return dict(
        revenue   =trend(_pick(inc_a,"Total Revenue","Revenue"), _pick(inc_q,"Total Revenue","Revenue"), "Revenue","inc"),
        ebit      =trend(_pick(inc_a,"EBIT","Operating Income"), _pick(inc_q,"EBIT","Operating Income"), "EBIT","inc"),
        ni        =trend(_pick(inc_a,"Net Income","Net Income Common Stockholders"),
                         _pick(inc_q,"Net Income","Net Income Common Stockholders"), "Net income","inc"),
        eps       =trend(_pick(inc_a,"Diluted EPS","Basic EPS"), _pick(inc_q,"Diluted EPS","Basic EPS"), "EPS","inc"),
        cfo       =trend(_pick(cf_a,"Operating Cash Flow","Total Cash From Operating Activities"),
                         _pick(cf_q,"Operating Cash Flow","Total Cash From Operating Activities"), "Cash from operating","cf"),
        cfi       =trend(_pick(cf_a,"Investing Cash Flow","Total Cashflows From Investing Activities"),
                         _pick(cf_q,"Investing Cash Flow","Total Cashflows From Investing Activities"), "Cash from investing","cf"),
        fcf       =trend(_pick(cf_a,"Free Cash Flow"), _pick(cf_q,"Free Cash Flow"), "Free cash flow","cf"),
        cash      =trend(_pick(bs_a,"Cash And Cash Equivalents","Cash Cash Equivalents And Short Term Investments","Cash"),
                         _pick(bs_q,"Cash And Cash Equivalents","Cash Cash Equivalents And Short Term Investments","Cash"),
                         "Cash & cash equivalents","bs"),
        recv      =trend(_pick(bs_a,"Accounts Receivable","Receivables","Net Receivables"),
                         _pick(bs_q,"Accounts Receivable","Receivables","Net Receivables"), "Receivables","bs"),
        pay       =trend(_pick(bs_a,"Accounts Payable","Payables","Payables And Accrued Expenses"),
                         _pick(bs_q,"Accounts Payable","Payables","Payables And Accrued Expenses"), "Payables","bs"),
        assets    =trend(_pick(bs_a,"Total Assets"), _pick(bs_q,"Total Assets"), "Total assets","bs"),
        liab      =trend(_pick(bs_a,"Total Liabilities Net Minority Interest","Total Liabilities","Total Liab"),
                         _pick(bs_q,"Total Liabilities Net Minority Interest","Total Liabilities","Total Liab"),
                         "Total liabilities","bs"),
        ltdebt    =trend(_pick(bs_a,"Long Term Debt"), _pick(bs_q,"Long Term Debt"), "Long-term debt","bs"),
    )

_HISTCACHE = {}   # sym -> (hist1[~64], hist5[~80], mapser{d,c}~150); one 5y pull per ticker

def yahoo_hist_both(sym):
    """ONE 5y daily pull (cached) -> (1y closes ~64, 5y closes ~80, dated 5y series {d:[],c:[]} ~150).
    Everything the backtest needs is baked into the page from here, so the browser never has to
    make a cross-origin Yahoo call (static hosts like GitHub Pages block those via CORS)."""
    sym = sym.upper()
    if sym in _HISTCACHE:
        return _HISTCACHE[sym]
    import yfinance as yf
    def _ds(arr, n):
        if len(arr) <= n: return arr
        step = len(arr) / n
        return [arr[min(len(arr)-1, int(k*step))] for k in range(n)]
    try:
        h = yf.Ticker(sym).history(period="5y", interval="1d")
        paired = [(str(i)[:10], float(c)) for i, c in zip(h.index, h["Close"].tolist()) if c == c]
        c_all = [p[1] for p in paired]
        if not c_all:
            res = ([], [], dict(d=[], c=[]))
        else:
            c1 = c_all[-252:] if len(c_all) > 252 else c_all
            hist1 = [round(x, 4) for x in _ds(c1, 64)]
            hist5 = [round(x, 4) for x in _ds(c_all, 80)]
            mp = _ds(paired, 150)
            mapser = dict(d=[p[0] for p in mp], c=[round(p[1], 4) for p in mp])
            res = (hist1, hist5, mapser)
    except Exception as e:
        print(f"      history failed {sym}: {e}")
        res = ([], [], dict(d=[], c=[]))
    _HISTCACHE[sym] = res
    return res

def yahoo_history(sym, points=64):
    # back-compat: 1y closes, served from the single cached 5y pull above
    return yahoo_hist_both(sym)[0]

# ----------------------------------------------------------------------------- main
def main():
    cfg = dict(CONFIG)
    if "--quick" in sys.argv:
        cfg["MAX_UNIVERSE"] = 500
        print("[--quick] scanning a reduced universe for a fast test run")
    print("MarketScreen - daily run", dt.date.today())

    if cfg["SOURCE"] == "tradingview":
        print("Screening via TradingView...")
        ups, downs = screen_tradingview(cfg)

        def rank_and_detail(cands):
            ranked = sorted(cands, key=lambda r: (-r["score"], -(r["flowmn"] or 0)))
            pool = [c["sym"] for c in ranked]
            detail_set = ranked[: cfg["DETAIL_N"]]
            for i, r in enumerate(detail_set, 1):
                print(f"      detail {i}/{len(detail_set)}: {r['sym']}")
                try: r["trends"] = yahoo_trends(r["sym"])
                except Exception as e: print(f"      trends failed {r['sym']}: {e}"); r["trends"] = {}
                try: r["hist"] = yahoo_history(r["sym"])
                except Exception as e: print(f"      history failed {r['sym']}: {e}"); r["hist"] = []
                try: r["peers"] = [peer_multiples(p) for p in get_peers(r["sym"], pool)]
                except Exception as e: print(f"      peers failed {r['sym']}: {e}")
                if cfg["FUND_SLEEP"]: time.sleep(cfg["FUND_SLEEP"])
            return ranked

        print("Building UP detail...");   up_final   = rank_and_detail(ups)
        print("Building DOWN detail..."); down_final = rank_and_detail(downs)
    else:
        print("Loading universe...")
        universe = load_universe(cfg)
        print(f"  {len(universe)} tickers")
        print("Screening on price/volume...")
        ups, downs = screen_volume(universe, cfg)

        def enrich_and_rank(cands):
            scored = []
            cands = sorted(cands, key=lambda r: -r["flowmn"])
            cands = cands[: cfg["ENRICH_MAX"]]
            pool = [c["sym"] for c in cands]
            for i, base in enumerate(cands, 1):
                print(f"    fundamentals {i}/{len(cands)}: {base['sym']}")
                try:
                    rec = fundamentals(base["sym"], base, cfg, sector_pool=pool, with_peers=False)
                    scored.append(rec)
                except Exception as e:
                    print(f"      ! {base['sym']}: {e}")
                if cfg["FUND_SLEEP"]: time.sleep(cfg["FUND_SLEEP"])
            ranked = sorted(scored, key=lambda r: (-r["score"], -(r["flowmn"] or 0)))
            for r in ranked[: cfg["DETAIL_N"]]:
                try: r["peers"] = [peer_multiples(p) for p in get_peers(r["sym"], pool)]
                except Exception as e: print(f"      peers failed for {r['sym']}: {e}")
                try: r["hist"] = yahoo_history(r["sym"])
                except Exception as e: print(f"      history failed {r['sym']}: {e}"); r["hist"] = []
            return ranked

        print("Enriching UP side...");   up_final   = enrich_and_rank(ups)
        print("Enriching DOWN side..."); down_final = enrich_and_rank(downs)

    print("Fetching S&P 500 benchmark history...")
    _b1, _b5, _bmap = yahoo_hist_both("SPY")
    bench = dict(sym="SPY", name="S&P 500 (SPY)", hist=_b1, hist5y=_b5)
    histmap = {"SPY": _bmap}
    history = update_history_archive(up_final, down_final, cfg)

    # Bake a 5y dated series for everything the backtest can reference, so the browser never
    # needs a cross-origin Yahoo fetch (GitHub Pages blocks those via CORS). Detailed names are
    # already cached from the detail loop above (no re-pull); archive-only names pull once here.
    for r in (up_final + down_final):
        if r.get("sym") and r.get("hist"):          # detailed names (cached) -> add 5y + map
            _h1, _h5, _mp = yahoo_hist_both(r["sym"])
            r["hist5y"] = _h5
            histmap[r["sym"]] = _mp
    arch_syms = {p["sym"] for d in history for p in d.get("picks", []) if p.get("sym")}
    for _sym in sorted(arch_syms - set(histmap)):
        _, _, _mp = yahoo_hist_both(_sym)
        histmap[_sym] = _mp
        if cfg["FUND_SLEEP"]: time.sleep(cfg["FUND_SLEEP"])

    print("Building market snapshot (what's priced in)...")
    market = build_market_snapshot(MARKET)

    payload = dict(generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   config=cfg, up=up_final, down=down_final, bench=bench,
                   history=history, histmap=histmap, market=market, demo=False)
    build_dashboard(payload, cfg["OUT_HTML"])
    print(f"Done -> {cfg['OUT_HTML']}  ({len(up_final)} up, {len(down_final)} down)")
    if "--no-open" not in sys.argv:
        try:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(cfg["OUT_HTML"]))
        except Exception:
            pass

# ===================== daily history archive =====================
def update_history_archive(up_final, down_final, cfg):
    """Append today's score>=HISTORY_SCORE picks to a rolling JSON archive on disk,
    then return the full archive (list of {date, picks:[{sym,score,price,side}]}).
    Re-running on the same day overwrites that day's entry, so it's idempotent."""
    path = cfg.get("HISTORY_FILE", "history.json")
    min_score = cfg.get("HISTORY_SCORE", 6)
    today = dt.date.today().isoformat()

    archive = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                archive = json.load(f)
            if not isinstance(archive, list):
                archive = []
        except Exception as e:
            print(f"  ! could not read {path} ({e}); starting fresh")
            archive = []

    picks = []
    for side, recs in (("up", up_final), ("down", down_final)):
        for r in recs:
            if (r.get("score") or 0) >= min_score and r.get("sym"):
                picks.append(dict(sym=r["sym"], score=r.get("score"),
                                  price=r.get("price"), side=side))
    # de-dupe by symbol, keep the higher score
    seen = {}
    for p in picks:
        if p["sym"] not in seen or (p["score"] or 0) > (seen[p["sym"]]["score"] or 0):
            seen[p["sym"]] = p
    picks = sorted(seen.values(), key=lambda p: (-(p["score"] or 0), p["sym"]))

    archive = [d for d in archive if d.get("date") != today]  # replace today if re-run
    archive.append(dict(date=today, picks=picks))
    archive.sort(key=lambda d: d.get("date", ""))

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(archive, f, indent=1, default=str)
        print(f"  history archive: {len(archive)} days, {len(picks)} picks today -> {path}")
    except Exception as e:
        print(f"  ! could not write {path}: {e}")
    return archive

# ===================== market snapshot (what's priced in) =====================
def build_market_snapshot(mkt):
    """Pull the whole 'what's priced in' dashboard at build time and return a display-ready
    dict baked into the page (no client-side cross-origin fetch). Every sub-fetch is wrapped;
    anything that fails degrades to None/dash so the page always renders."""
    import yfinance as yf, math, urllib.request

    def _http(url, timeout=15):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Magellan screener)",
                                                        "Accept": "text/csv,text/xml,application/xml,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            print(f"      http failed {url[:60]}: {e}"); return None

    def _closes(sym, period="1mo"):
        try:
            h = yf.Ticker(sym).history(period=period, interval="1d")
            return [float(x) for x in h["Close"].tolist() if x == x]
        except Exception as e:
            print(f"      yahoo failed {sym}: {e}"); return []

    def _ds(arr, n=22):
        if len(arr) <= n: return [round(x, 4) for x in arr]
        step = len(arr) / n
        return [round(arr[min(len(arr)-1, int(k*step))], 4) for k in range(n)]

    def _sep(x, dp):
        return f"{x:,.{dp}f}".replace(",", "\u202f")

    def _sign(x, dp=2, suf="%"):
        return f"{'+' if x >= 0 else '-'}{abs(x):.{dp}f}{suf}"

    def _dir(x):
        return "up" if x > 0 else "dn" if x < 0 else "flat"

    mv = {}   # numeric reads kept for the regime + narrative

    def _price_tile(label, sym, fx=False, level=False):
        c = _closes(sym)
        if len(c) < 2: return None, None
        last, prev = c[-1], c[-2]
        if level:
            diff = last - prev
            tile = dict(label=label, value=f"{last:.1f}", chg=_sign(diff, 1, ""), dir=_dir(diff), spark=_ds(c))
            return tile, diff
        p = (last/prev - 1) * 100 if prev else 0.0
        if fx:
            value = f"{last:.4f}"
        else:
            value = _sep(last, 0) if last >= 1000 else f"{last:,.2f}"
        return dict(label=label, value=value, chg=_sign(p), dir=_dir(p), spark=_ds(c)), p

    def _yield_tile(label, last, prev):
        bp = round((last - prev) * 100)
        return dict(label=label, value=f"{last:.2f}%", chg=f"{'+' if bp >= 0 else '-'}{abs(bp)} bp",
                    dir=_dir(bp), spark=[])

    # ---- FRED: (date,value) ascending, last 90d ----
    def _fred(series, days=120):
        cosd = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        txt = _http(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={cosd}")
        out = []
        if txt:
            for line in txt.splitlines()[1:]:
                parts = line.split(",")
                if len(parts) < 2: continue
                v = parts[-1].strip()
                if v and v != ".":
                    try: out.append(float(v))
                    except Exception: pass
        return out

    # ---- ECB data portal: euro-area AAA government spot yield, last 3 obs ----
    def _ecb(tenor):
        key = f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor}"
        txt = _http(f"https://data-api.ecb.europa.eu/service/data/YC/{key}?lastNObservations=3&format=csvdata")
        out = []
        if txt:
            lines = txt.splitlines()
            if lines:
                hdr = lines[0].split(",")
                try: idx = hdr.index("OBS_VALUE")
                except ValueError: idx = len(hdr) - 1
                for line in lines[1:]:
                    parts = line.split(",")
                    if len(parts) > idx and parts[idx].strip():
                        try: out.append(float(parts[idx]))
                        except Exception: pass
        return out

    # ---- Japan MoF: latest two JGB rows -> {colyear: [prev,last]} ----
    _jgb_cache = {}
    def _jgb():
        if _jgb_cache: return _jgb_cache.get("data", {})
        txt = _http("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv")
        data = {}
        if txt:
            rows = [r.split(",") for r in txt.splitlines() if r.strip()]
            if len(rows) >= 2:
                hdr = [h.strip().replace("Y", "") for h in rows[0]]
                good = [r for r in rows[1:] if len(r) == len(rows[0])]
                for col in ("2", "10"):
                    if col in hdr:
                        ci = hdr.index(col)
                        vals = []
                        for r in good:
                            try: vals.append(float(r[ci]))
                            except Exception: pass
                        if len(vals) >= 2: data[col] = vals[-2:]
        _jgb_cache["data"] = data
        return data

    # ===== build tile groups =====
    groups = []
    g_index = []
    for label, sym in mkt.get("INDEX", []):
        t, d = _price_tile(label, sym)
        if t: g_index.append(t); mv[label] = d
    if g_index: groups.append(dict(name="Index futures", tiles=g_index))

    g_bonds = []
    for label, sid in mkt.get("US_BONDS", []):
        s = _fred(sid)
        if len(s) >= 2:
            t = _yield_tile(label, s[-1], s[-2]); g_bonds.append(t); mv[label] = s[-1] - s[-2]
    for label, tenor in mkt.get("DE_BONDS", []):
        s = _ecb(tenor)
        if len(s) >= 2: g_bonds.append(_yield_tile(label, s[-1], s[-2]))
    jp = _jgb()
    for label, col in mkt.get("JP_BONDS", []):
        s = jp.get(col)
        if s and len(s) >= 2: g_bonds.append(_yield_tile(label, s[-1], s[-2]))
    if g_bonds: groups.append(dict(name="Bonds", tiles=g_bonds))

    g_comm = []
    for label, sym in mkt.get("COMMODITIES", []):
        t, d = _price_tile(label, sym)
        if t: g_comm.append(t); mv[label] = d
    if g_comm: groups.append(dict(name="Commodities", tiles=g_comm))

    g_eur = []
    for label, sym in mkt.get("FX_EUR", []):
        t, d = _price_tile(label, sym, fx=True)
        if t: g_eur.append(t)
    if g_eur: groups.append(dict(name="FX \u2013 EUR majors", tiles=g_eur))

    g_usd = []
    for label, sym in mkt.get("FX_USD", []):
        lvl = (sym == "DX-Y.NYB")
        t, d = _price_tile(label, sym, fx=not lvl, level=lvl)
        if t: g_usd.append(t); mv[label] = d
    if g_usd: groups.append(dict(name="FX \u2013 USD majors", tiles=g_usd))

    # VIX (for the risk panel + regime)
    vix_c = _closes("^VIX")
    vix = {}
    if len(vix_c) >= 2:
        diff = vix_c[-1] - vix_c[-2]
        vix = dict(val=f"{vix_c[-1]:.1f}", chg=_sign(diff, 1, ""), dir=_dir(diff)); mv["VIX"] = diff

    # ===== what's priced into rates (FRED) =====
    fr = mkt.get("FRED", {})
    def _last2(series):
        s = _fred(series); return (s[-1], s[-2]) if len(s) >= 2 else (None, None)
    tt_l, tt_p = _last2(fr.get("two_ten", "T10Y2Y"))
    rl_l, _ = _last2(fr.get("real_10y", "DFII10"))
    ff_l, _ = _last2(fr.get("five_y_five", "T5YIFR"))
    oas_l, oas_p = _last2(fr.get("hy_oas", "BAMLH0A0HYM2"))
    rates = dict(
        two_ten=(f"{'+' if tt_l >= 0 else '-'}{abs(round(tt_l*100))} bp" if tt_l is not None else None),
        real_10y=(f"{rl_l:.2f}%" if rl_l is not None else None),
        five_y_five=(f"{ff_l:.2f}%" if ff_l is not None else None),
    )
    hy_oas = None
    if oas_l is not None:
        hy_bp = round(oas_l * 100); hy_d = round((oas_l - oas_p) * 100) if oas_p is not None else 0
        hy_oas = dict(bp=f"{hy_bp} bp", chg=f"{'+' if hy_d >= 0 else '-'}{abs(hy_d)}", dir=_dir(hy_d))
        mv["HYOAS"] = hy_d

    # ===== Fed (futures-implied) + ECB (2Y vs deposit, est.) =====
    def _bar(cuts_bp, horizon):
        pct = max(0, min(100, round((cuts_bp / horizon) * 100))) if cuts_bp and cuts_bp > 0 else 0
        if cuts_bp is None: return dict(text="-", pct=None)
        if cuts_bp <= 2:    return dict(text="~flat / none priced", pct=0)
        return dict(text=f"~{round(cuts_bp)} bp of cuts", pct=pct)
    fed_cfg = mkt.get("FED", {}); fed = dict(label=fed_cfg.get("label", "Fed"))
    fc = _closes(fed_cfg.get("contract", "ZQU26=F"))
    if fc:
        implied = 100.0 - fc[-1]; fed.update(_bar((fed_cfg.get("target_mid", 0) - implied) * 100, fed_cfg.get("horizon_bp", 50)))
    else:
        fed.update(text="-", pct=None)
    ecb_cfg = mkt.get("ECB", {}); ecb = dict(label=ecb_cfg.get("label", "ECB"))
    de2 = _ecb("2Y")
    if de2:
        ecb.update(_bar((ecb_cfg.get("depo", 0) - de2[-1]) * 100, ecb_cfg.get("horizon_bp", 50)))
    else:
        ecb.update(text="-", pct=None)

    # ===== credit ratio HYG/SPY =====
    credit = {}
    hyg, spy = _closes("HYG"), _closes("SPY")
    if len(hyg) >= 2 and len(spy) >= 2:
        r_last, r_prev = hyg[-1]/spy[-1], hyg[-2]/spy[-2]
        p = (r_last/r_prev - 1) * 100 if r_prev else 0
        credit = dict(hyg_spx=f"{r_last:.4f}", hyg_chg=_sign(p), hyg_dir=_dir(p)); mv["HYG"] = p

    # ===== Alpha Risk Matrix: 25d 1M risk reversal on the option chain =====
    arm = _risk_reversal(mkt.get("ARM", {}))
    arm = _arm_percentile(arm, mkt.get("HISTORY_FILE", "market_history.json"))

    # ===== regime (risk-on/off) from the reads above =====
    eq = [mv.get(l) for l in ("S&P fut", "Nasdaq fut", "Dow fut", "Euro Stoxx") if mv.get(l) is not None]
    sig = []
    def add(name, off_cond, on_cond):
        if off_cond: sig.append(dict(name=name, dir="off"))
        elif on_cond: sig.append(dict(name=name, dir="on"))
    if eq: add("Equities " + ("\u2193" if sum(eq) < 0 else "\u2191"), sum(eq) < 0, sum(eq) > 0)
    if "VIX" in mv: add("VIX " + ("\u2191" if mv["VIX"] > 0 else "\u2193"), mv["VIX"] > 0, mv["VIX"] < 0)
    if "DXY" in mv: add("USD " + ("\u2191" if mv["DXY"] > 0 else "\u2193"), mv["DXY"] > 0, mv["DXY"] < 0)
    if "WTI crude" in mv: add("Oil " + ("\u2193" if mv["WTI crude"] < 0 else "\u2191"), mv["WTI crude"] < 0, mv["WTI crude"] > 0)
    cred_off = (mv.get("HYG", 0) < 0) or (mv.get("HYOAS", 0) > 0)
    cred_on = (mv.get("HYG", 0) > 0) and (mv.get("HYOAS", 0) <= 0)
    if ("HYG" in mv) or ("HYOAS" in mv):
        sig.append(dict(name="Credit " + ("soft" if cred_off else "firm"), dir="off" if cred_off else "on"))
    n_off = sum(1 for s in sig if s["dir"] == "off"); n_on = len(sig) - n_off
    regime = "Risk-off" if n_off > n_on else "Risk-on" if n_on > n_off else "Neutral"
    rdir = "off" if n_off > n_on else "on" if n_on > n_off else "neutral"
    score = round((n_on / len(sig)) * 100) if sig else 50   # 0 = full risk-off, 100 = full risk-on
    risk = dict(regime=regime, dir=rdir, count=f"{max(n_off, n_on)} of {len(sig)} signals" if sig else "",
                pos=score, signals=sig, vix=vix, **credit)
    if hy_oas: risk["hy_oas"] = hy_oas

    setup = _market_setup(mv, regime, vix, rates)
    news = _macro_news(mkt.get("NEWS_FEEDS", []), _http)

    return dict(asof=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), groups=groups,
                rates=rates, fed=fed, ecb=ecb, risk=risk, arm=arm, setup=setup, news=news)


def _risk_reversal(cfg):
    """25-delta 1M risk reversal = IV(25d put) - IV(25d call), in vol points, from the Yahoo
    option chain. Positive => downside skew (puts bid). None on any failure."""
    import yfinance as yf, math
    try:
        sym = cfg.get("underlying", "SPY"); r = cfg.get("r", 0.04); q = cfg.get("q", 0.012)
        target = cfg.get("target_days", 30)
        t = yf.Ticker(sym)
        h = t.history(period="5d"); spot = float(h["Close"].dropna().iloc[-1])
        exps = list(t.options or [])
        today = dt.date.today(); best = None
        for e in exps:
            d = (dt.date.fromisoformat(e) - today).days
            if d <= 1: continue
            if best is None or abs(d - target) < abs(best[1] - target): best = (e, d)
        if not best: return None
        exp, days = best; T = max(days, 1) / 365.0
        oc = t.option_chain(exp)
        N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
        def iv_at_25(df, is_call):
            rows = []
            for K, iv in zip(df["strike"].tolist(), df["impliedVolatility"].tolist()):
                try: K = float(K); iv = float(iv)
                except Exception: continue
                if not (0.01 < iv < 3.0) or K <= 0: continue
                d1 = (math.log(spot / K) + (r - q + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
                delta = math.exp(-q * T) * (N(d1) if is_call else (N(d1) - 1))
                rows.append((abs(delta), iv))
            if len(rows) < 2: return None
            rows.sort()
            for i in range(1, len(rows)):
                a, b = rows[i-1], rows[i]
                if (a[0] - 0.25) * (b[0] - 0.25) <= 0:
                    if b[0] == a[0]: return a[1]
                    w = (0.25 - a[0]) / (b[0] - a[0]); return a[1] + w * (b[1] - a[1])
            return min(rows, key=lambda x: abs(x[0] - 0.25))[1]
        civ, piv = iv_at_25(oc.calls, True), iv_at_25(oc.puts, False)
        if civ is None or piv is None: return None
        return round((piv - civ) * 100, 2)   # vol points
    except Exception as e:
        print(f"      risk-reversal failed: {e}"); return None


def _arm_percentile(rr, path):
    """Append today's risk reversal to a rolling file and return display dict with the 30-day
    percentile. Builds up over ~30 daily runs; shows 'building' until it has enough history."""
    today = dt.date.today().isoformat(); arr = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: arr = json.load(f)
            if not isinstance(arr, list): arr = []
        except Exception: arr = []
    if rr is not None:
        arr = [x for x in arr if x.get("date") != today]
        arr.append(dict(date=today, rr=rr)); arr.sort(key=lambda x: x.get("date", ""))
        arr = arr[-90:]
        try:
            with open(path, "w", encoding="utf-8") as f: json.dump(arr, f, indent=1)
        except Exception as e:
            print(f"      ! market_history write: {e}")
    last30 = [x["rr"] for x in arr[-30:] if isinstance(x.get("rr"), (int, float))]
    n = len(last30)
    if rr is None:
        return dict(value=None, n=n, building=(n < 10))
    lo, hi = (min(last30), max(last30)) if last30 else (rr, rr)
    below = sum(1 for v in last30 if v < rr)
    pct = round(below / max(1, n - 1) * 100) if n > 1 else 50
    pos = round((rr - lo) / ((hi - lo) or 1) * 100)
    return dict(value=f"{'+' if rr >= 0 else '-'}{abs(rr):.1f}", raw=rr, lo=f"{lo:.1f}", hi=f"{hi:.1f}",
                pct=pct, pos=pos, n=n, building=(n < 10),
                elevated=(pct >= 70), depressed=(pct <= 30))


def _market_setup(mv, regime, vix, rates):
    """Generate the short 'today's setup' story + what-to-price-in / what-to-watch bullets
    straight from the numeric reads, so it rewrites itself each update."""
    def mo(k, dp=2):
        v = mv.get(k); return None if v is None else (f"{'+' if v >= 0 else '-'}{abs(v):.{dp}f}%")
    eq = [mv.get(l) for l in ("S&P fut", "Nasdaq fut", "Dow fut", "Euro Stoxx") if mv.get(l) is not None]
    tone = {"Risk-off": "Risk-off tape", "Risk-on": "Risk-on tape", "Neutral": "Mixed, two-way tape"}[regime]
    eq_word = "broadly lower" if (eq and sum(eq) < 0) else "broadly higher" if (eq and sum(eq) > 0) else "mixed"
    bits = [f"{tone} into the open - equities are {eq_word}"]
    if vix.get("val"): bits.append(f"the VIX is at {vix['val']} ({vix.get('chg','')})")
    if mv.get("DXY") is not None: bits.append("the dollar is " + ("firm" if mv["DXY"] > 0 else "softer"))
    if mv.get("WTI crude") is not None: bits.append("oil is " + ("off" if mv["WTI crude"] < 0 else "up"))
    story = ", ".join(bits) + ". The read is driven by what's moving together: watch whether credit and gold confirm or fade the equity move."
    price_in = []
    if eq:
        parts = [f"{l} {mo(l)}" for l in ("S&P fut", "Nasdaq fut", "Dow fut", "Euro Stoxx") if mo(l)]
        if parts: price_in.append("Equities: " + ", ".join(parts[:3]))
    if vix.get("val"): price_in.append(f"Volatility: VIX {vix['val']} ({vix.get('chg','')})")
    if mv.get("DXY") is not None: price_in.append("Dollar " + ("bid" if mv["DXY"] > 0 else "offered") + " - watch risk-sensitive FX")
    if mo("WTI crude"): price_in.append(f"Oil (WTI) {mo('WTI crude')} - growth/demand read")
    if mv.get("HYG") is not None: price_in.append("Credit (HYG/SPX) " + ("firm" if mv["HYG"] >= 0 else "soft") + " vs equities")
    watch = []
    if rates.get("two_ten"): watch.append(f"2s10s ({rates['two_ten']}) - flattening on a selloff = growth scare")
    if rates.get("real_10y"): watch.append(f"10Y real yield ({rates['real_10y']}) - the lever under growth/tech")
    watch.append("HY OAS - if it widens, an equity wobble is turning into a credit event")
    watch.append("Gold vs the dollar - is it catching a haven bid, or is this real-yield/USD led?")
    if not price_in: price_in = ["Market snapshot still populating - run a Magellan update."]
    hook = {"Risk-off": "Defensive tape - risk is being taken off the table.",
            "Risk-on": "Constructive tape - risk is being put to work.",
            "Neutral": "Mixed tape - no clear risk signal yet."}.get(regime, "")
    return dict(hook=hook, story=story, price_in=price_in, watch=watch)


def _macro_news(feeds, http, k=3):
    """Top macro headlines from public RSS, baked in as clickable links."""
    import re, html as _html
    def src_of(u):
        for name in ("marketwatch", "cnbc", "investing", "reuters", "bloomberg", "ft"):
            if name in u: return name.capitalize()
        return "News"
    items = []
    for url in feeds:
        txt = http(url)
        if not txt: continue
        for m in re.finditer(r"<item[ >](.*?)</item>", txt, re.S):
            block = m.group(1)
            tm = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S)
            lm = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", block, re.S)
            if tm and lm:
                hd = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(1)).strip())
                lk = _html.unescape(lm.group(1).strip())
                if hd and lk.startswith("http"):
                    items.append(dict(hd=hd[:130], url=lk, src=src_of(url)))
            if len(items) >= k: break
        if len(items) >= k: break
    return items[:k]

# ===================== inlined dashboard renderer =====================
def build_dashboard(payload, out_path="dashboard.html"):
    html = _TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, default=str))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path

_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Market Screen by Magellan</title>
<style>
:root{--bg:#0b1220;--panel:#111b2e;--panel2:#0e1726;--line:#1f2c44;--ink:#e8eef9;
--muted:#8aa0c2;--dim:#5b6f92;--up:#3fe08a;--down:#ff5d6c;--gold:#e8c170;--accent:#5b8cff;--blue:#9fc0ff;}
*{box-sizing:border-box}
html{overflow-y:scroll;scrollbar-gutter:stable}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#152b4a 0,transparent 60%),var(--bg);
color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px;}
header{display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--line);padding-bottom:18px;flex-wrap:wrap;}
.compass{width:42px;height:42px;flex:none}
.brand h1{margin:0;font-size:22px;letter-spacing:.5px;font-weight:700}
.brand .sub{color:var(--muted);font-size:12px;letter-spacing:2px;text-transform:uppercase}
.asof{margin-left:auto;color:var(--dim);font-size:12px;text-align:right}
nav.tabs{display:flex;gap:6px;margin:18px 0 8px;flex-wrap:wrap}
nav.tabs button{background:transparent;border:1px solid var(--line);color:var(--muted);
padding:8px 16px;border-radius:999px;cursor:pointer;font-size:13px}
nav.tabs button.active{background:var(--panel);color:var(--ink);border-color:var(--accent)}
.view{display:none}.view.active{display:block}
.mktq{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:11px 16px;color:var(--blue);font-size:14px;margin:6px 0 4px}
.mktq b{color:var(--ink)}
.mksub{color:var(--dim);font-size:11px;letter-spacing:1px;text-transform:uppercase;margin:0 2px 16px}
.mkgrp{color:var(--ink);font-size:12.5px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;margin:24px 2px 11px;padding-left:10px;border-left:3px solid var(--accent)}
.mktiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px}
.mktile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px;transition:border-color .15s ease}
.mktile:hover{border-color:var(--accent)}
.mktile .tg{float:right;color:var(--dim);font-size:9px;letter-spacing:1px;text-transform:uppercase}
.mktile .tl{color:var(--blue);font-size:12.5px;font-weight:500;margin-bottom:4px;letter-spacing:.2px}
.mktile .tv{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.mktile .tc{font-size:12px;font-variant-numeric:tabular-nums;margin-top:2px}
.mkrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px}
.mkcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-top:14px}
.mkrow .mkcard{margin-top:14px}
.mkh{color:var(--ink);font-size:12.5px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;margin:0 0 13px;padding-left:10px;border-left:3px solid var(--accent)}
.mkhero{display:grid;grid-template-columns:120px 1fr;gap:22px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin:14px 0 6px}
.mkdial{width:120px;height:120px;flex:none}
.mkhlead{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:3px}
.mkhreg{font-size:28px;font-weight:700;line-height:1.1}
.mkhcount{font-size:13px;font-weight:400;color:var(--muted);margin-left:10px}
.mkhhook{font-size:15px;color:var(--ink);margin:9px 0 12px}
.mkhchips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.mkhchip{font-size:12.5px;padding:4px 10px;border-radius:8px;background:rgba(127,140,170,.13)}
.mkhcta{font-size:13px;color:var(--accent);font-weight:600}
@media(max-width:560px){.mkhero{grid-template-columns:1fr;justify-items:center;text-align:center}.mkhhook{text-align:left}}
.mkrowln{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-top:1px solid var(--line)}
.mkrowln:first-child{border-top:none}
.mkrowln .k{color:var(--muted)}.mkrowln .v{font-variant-numeric:tabular-nums}
.mkbarw{margin-top:12px}
.mkbarl{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-bottom:5px}
.mkbar{height:7px;background:var(--panel2);border-radius:4px;overflow:hidden}
.mkbar>i{display:block;height:100%;background:var(--accent)}
.mkregime{font-size:22px;font-weight:700;margin-bottom:6px}
.mkcat{width:100%;border-collapse:collapse;font-size:13px}
.mkcat th{color:var(--dim);font-size:10px;letter-spacing:1px;text-transform:uppercase;text-align:left;font-weight:600;padding:4px 0}
.mkcat td{padding:7px 0;border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
.mkchain{border-left:3px solid var(--gold)}
.mkchaintext{color:var(--blue);font-size:14px;line-height:1.6}
.mksetup{border-left:3px solid var(--gold);border-radius:12px}
.mkstory{color:var(--blue);font-size:14px;line-height:1.65;margin:0 0 12px}
.mkcols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.mkcolh{font-size:12px;font-weight:600;margin:0 0 6px}
.mkbul{margin:0;padding-left:18px}.mkbul li{font-size:13px;line-height:1.55;margin:3px 0;color:var(--ink)}
.mkchip{display:inline-block;font-size:12px;padding:3px 9px;border-radius:7px;margin:0 6px 6px 0}
.mktrk{height:8px;background:var(--panel2);border-radius:4px;position:relative;margin-top:4px}
.mktrk>i{position:absolute;top:-3px;width:3px;height:14px;border-radius:2px}
.mkends{display:flex;justify-content:space-between;font-size:11px;color:var(--dim);margin:10px 0 4px}
.mkbig{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.mknw{display:flex;gap:10px;padding:9px 0;border-top:1px solid var(--line);text-decoration:none}
.mknw:first-child{border-top:none}
.mknw .hd{font-size:13px;color:var(--ink);line-height:1.4}
.mknw .mt{font-size:11px;color:var(--dim);margin-top:2px;font-variant-numeric:tabular-nums}
.scorebar{display:flex;align-items:center;gap:14px;background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:12px 16px;margin:12px 0 16px}
.scorebar input[type=range]{flex:1;accent-color:var(--accent)}
.scorebar .lab{color:var(--muted);font-size:12px;white-space:nowrap}
.scorebar .cnt{color:var(--gold);font-weight:700;font-variant-numeric:tabular-nums}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:820px){.cols{grid-template-columns:1fr}}
.colhead{display:flex;align-items:center;gap:8px;font-size:12px;letter-spacing:2px;
text-transform:uppercase;color:var(--muted);margin:0 2px 8px}
.dot{width:8px;height:8px;border-radius:50%}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:11px 13px;
margin-bottom:9px;cursor:pointer;transition:border-color .15s}
.card:hover{border-color:var(--accent)}
.crow{display:flex;align-items:baseline;gap:10px}
.sym{font-weight:700;font-size:15px}
.nm{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.px{font-variant-numeric:tabular-nums;font-weight:600}
.chg{font-variant-numeric:tabular-nums;font-size:13px}
.meta{display:flex;gap:14px;color:var(--dim);font-size:11.5px;margin-top:5px;font-variant-numeric:tabular-nums}
.score{margin-left:auto;color:var(--gold);font-weight:700}
.pos{color:var(--up)}.neg{color:var(--down)}
.scrim{position:fixed;inset:0;background:rgba(4,8,16,.72);backdrop-filter:blur(3px);
display:none;align-items:flex-start;justify-content:center;padding:30px 16px;overflow:auto;z-index:30}
.scrim.open{display:flex}
.modal{width:100%;max-width:780px;background:var(--panel2);border:1px solid var(--line);
border-radius:16px;padding:18px 20px 24px}
.mhead{display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--line);padding-bottom:12px}
.mhead .x{margin-left:auto;background:none;border:1px solid var(--line);color:var(--muted);
border-radius:8px;cursor:pointer;width:30px;height:30px;font-size:16px}
.subtabs{display:flex;gap:6px;margin:16px 0 12px;flex-wrap:wrap}
.subtabs button{background:transparent;border:1px solid var(--line);color:var(--muted);
padding:6px 16px;border-radius:8px;cursor:pointer;font-size:12.5px}
.subtabs button.active{background:var(--panel);color:var(--ink);border-color:var(--accent)}
.pane{display:none}.pane.active{display:block}
.bigscore{display:flex;align-items:center;gap:14px;margin:4px 0 14px}
.bigscore .n{font-size:40px;font-weight:800;color:var(--gold);line-height:1}
.bigscore .of{color:var(--dim);font-size:13px}
.mgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
@media(max-width:560px){.mgrid{grid-template-columns:repeat(2,1fr)}}
.mcell{background:var(--panel2);padding:10px 12px}
.mcell .k{color:var(--muted);font-size:10.5px;letter-spacing:.4px;text-transform:uppercase}
.mcell .v{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}
.devhdr{margin:18px 2px 8px;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}
.devtoggle{display:flex;gap:6px;margin:0 0 12px}
.devtoggle button{background:transparent;border:1px solid var(--line);color:var(--muted);
padding:5px 14px;border-radius:8px;cursor:pointer;font-size:12px}
.devtoggle button.active{background:var(--panel);color:var(--ink);border-color:var(--accent)}
.dev5{grid-template-columns:repeat(2,1fr);gap:12px}
.devset{display:none}.devset.active{display:grid}
@media(max-width:560px){.dev5{grid-template-columns:1fr}}
.devcard{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.devtop{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:4px}
.devtop .l{font-size:12.5px;color:var(--ink);font-weight:600}
.devtop .g{font-size:11.5px;font-variant-numeric:tabular-nums}
.devcard svg text{fill:var(--dim)}
.tvlink{font-size:11.5px;color:var(--accent);text-decoration:none;border:1px solid var(--line);
border-radius:8px;padding:5px 10px;white-space:nowrap}
.tvlink:hover{border-color:var(--accent)}
.tvcard{font-size:10.5px;color:var(--dim);text-decoration:none;border:1px solid var(--line);
border-radius:6px;padding:0 6px;margin-left:8px}
.tvcard:hover{color:var(--accent);border-color:var(--accent)}
.peerrow.me{background:var(--panel)}
.peerrow.me td:first-child{color:var(--gold);font-weight:700}
label.f{display:block;color:var(--muted);font-size:11px;margin:0 0 4px}
input.f{width:100%;background:var(--panel);border:1px solid var(--line);border-radius:8px;
color:var(--blue);padding:8px 10px;font-size:13px;font-variant-numeric:tabular-nums;font-weight:600}
input.f:focus{outline:none;border-color:var(--accent)}
.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
@media(max-width:620px){.grid5{grid-template-columns:repeat(2,1fr)}}
.baseline{display:flex;flex-wrap:wrap;gap:10px 22px;background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:12px 14px;margin:14px 0;font-size:12px}
.baseline span b{color:var(--ink)}.baseline span{color:var(--dim)}
.hint{color:var(--dim);font-size:11px;margin:10px 2px}
.scen{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}
.scen .box{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.scen .lab{font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted)}
.scen .val{font-size:24px;font-weight:700;margin:6px 0 2px;font-variant-numeric:tabular-nums}
.scen .ud{font-size:12px;font-variant-numeric:tabular-nums}
.scen .worst{border-color:#3a2030}.scen .opt{border-color:#1f3a2c}
.addbar{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;align-items:end;
background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}
@media(max-width:680px){.addbar{grid-template-columns:1fr 1fr}}
.btn{background:var(--accent);border:none;color:#08122b;font-weight:700;border-radius:8px;padding:9px 16px;cursor:pointer;font-size:13px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600;font-size:11px;letter-spacing:.6px;text-transform:uppercase}
.totrow td{font-weight:700;border-top:1px solid var(--accent);border-bottom:none}
.rm{background:none;border:none;color:var(--down);cursor:pointer;font-size:14px}
.empty{color:var(--dim);text-align:center;padding:30px;border:1px dashed var(--line);border-radius:12px}
.badge{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
svg .ax{stroke:var(--line);stroke-width:1}
.status{font-size:11px;color:var(--dim);margin-left:6px}
.seg{display:inline-flex;gap:2px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:3px}
.seg button{background:none;border:0;color:var(--muted);font-size:12px;padding:6px 13px;border-radius:6px;cursor:pointer}
.seg button.on{background:var(--panel);color:var(--ink)}
.chip{display:inline-flex;align-items:center;gap:6px;background:var(--panel2);border:1px solid var(--line);
border-radius:99px;padding:4px 6px 4px 11px;margin:0 6px 6px 0;font-size:12px;font-weight:600}
.chip .x{cursor:pointer;color:var(--down);font-size:14px;line-height:1;border:none;background:none;padding:0}
.chip.bench{border-color:var(--accent);color:var(--blue)}
.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--gold);
border-radius:8px;padding:12px 14px;font-size:12.5px;color:var(--muted);line-height:1.6;margin:14px 0}
.daybadge{font-size:10px;color:var(--gold);border:1px solid var(--line);border-radius:6px;padding:1px 7px;margin-left:8px}
.compass{cursor:pointer;transition:transform .6s ease}
.compass:hover{transform:rotate(20deg)}
.compass.spin{animation:cspin 1s linear}
@keyframes cspin{to{transform:rotate(360deg)}}
.disc{position:fixed;inset:0;background:rgba(4,8,16,.86);backdrop-filter:blur(4px);
display:none;align-items:center;justify-content:center;padding:24px;z-index:100}
.disc.open{display:flex}
.disc .box{max-width:540px;background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:26px 28px}
.disc h2{margin:0 0 4px;font-size:18px;display:flex;align-items:center;gap:9px}
.disc h2 .ic{color:var(--gold)}
.disc .sub2{color:var(--muted);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:14px}
.disc p{color:var(--muted);font-size:12.5px;line-height:1.65;margin:0 0 14px}
.disc .ok{background:var(--accent);border:none;color:#08122b;font-weight:700;border-radius:9px;
padding:11px 20px;cursor:pointer;font-size:13px;width:100%}
.disc .ok:hover{filter:brightness(1.08)}
/* ---- Thesis tab ---- */
.th{font-size:13.5px}
.th .verdict{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 6px}
.th .verdict .v{font-size:15px;font-weight:700}
.th .verdict .m{color:var(--dim);font-size:11.5px}
.th .stance{color:var(--blue);font-size:11.5px;margin-bottom:12px}
.th .sum{color:var(--muted);line-height:1.65;margin:0 0 14px}
.th .gauge{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin-bottom:16px}
.th .gauge .row{display:flex;justify-content:space-between;font-size:10.5px;color:var(--dim)}
.th .gauge .track{position:relative;height:8px;border-radius:5px;background:var(--panel2);overflow:hidden;margin:7px 0;border:1px solid var(--line)}
.th .gauge .fv{position:absolute;left:0;top:0;height:100%;background:var(--up);opacity:.5}
.th .gauge .prem{position:absolute;top:0;height:100%;background:repeating-linear-gradient(45deg,rgba(255,93,108,.5),rgba(255,93,108,.5) 4px,transparent 4px,transparent 8px)}
.th .gauge .tick{position:absolute;top:-3px;height:14px;width:1.5px;background:var(--ink)}
.th .cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
@media(max-width:560px){.th .cols{grid-template-columns:1fr}}
.th .c{border-radius:10px;padding:12px 14px;border:1px solid var(--line)}
.th .c.str{background:rgba(63,224,138,.05);border-color:rgba(63,224,138,.25)}
.th .c.rsk{background:rgba(255,93,108,.05);border-color:rgba(255,93,108,.25)}
.th .c.wch{background:rgba(91,140,255,.05);border-color:rgba(91,140,255,.22);margin-bottom:14px}
.th .c h4{margin:0 0 10px;font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px}
.th .c.str h4{color:var(--up)}.th .c.rsk h4{color:var(--down)}.th .c.wch h4{color:var(--blue)}
.th .c .b{display:block;margin-bottom:9px;line-height:1.5;font-size:12.5px;color:var(--ink)}
.th .c .b:last-child{margin-bottom:0}
.th .src{color:var(--dim);font-size:10px}
.th .foot{display:flex;align-items:center;flex-wrap:wrap;gap:7px;padding-top:11px;border-top:1px solid var(--line);font-size:10px;color:var(--dim)}
.th .foot .chip2{background:var(--panel);border:1px solid var(--line);padding:2px 7px;border-radius:5px}
.th .foot .sp{margin-left:auto}
@media(max-width:600px){
  .wrap{padding:14px 12px 48px}
  .compass{width:34px;height:34px}
  .brand h1{font-size:18px;letter-spacing:.3px}
  .brand .sub{font-size:11px;letter-spacing:1.2px}
  .asof{margin-left:0;width:100%;text-align:left;margin-top:2px}
  nav.tabs{gap:5px;margin:14px 0 6px}
  nav.tabs button{padding:7px 12px;font-size:12.5px}
  .scorebar{flex-wrap:wrap;gap:10px;padding:11px 13px}
  .mktiles{grid-template-columns:repeat(auto-fit,minmax(116px,1fr));gap:8px}
  .mktile .tv{font-size:17px}
  .mkhero{padding:16px;gap:14px}
  .mkhreg{font-size:23px}
  .mkcard{padding:14px 14px}
  table{font-size:12.5px}
  #btBody,#histBody,#pBody{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .modal{padding:16px 14px}
}
@media(max-width:430px){
  .addbar{grid-template-columns:1fr}
  .mkhchips .mkhchip{font-size:12px;padding:3px 8px}
}
</style></head><body>
<div class="disc" id="disc"><div class="box">
<h2><span class="ic">&#9888;</span> Important notice</h2>
<div class="sub2">Educational &amp; research tool only</div>
<p>Magellan Market Screen is an automated, educational research tool provided
&ldquo;as is&rdquo; and &ldquo;as available,&rdquo; without warranty of any kind, express or implied.
Nothing displayed here is investment, financial, legal, or tax advice, nor a recommendation,
solicitation, or offer to buy or sell any security.</p>
<p>All data may be delayed, incomplete, or inaccurate. Scores and valuations are illustrative
estimates only. You are solely responsible for your own investment decisions and bear all
associated risk. To the fullest extent permitted by law, Magellan and its authors accept no
liability for any loss or damage arising from use of this tool. Consult a licensed financial
advisor before investing.</p>
<button class="ok" id="discOk">I understand and accept</button>
</div></div>
<div class="wrap">
<header>
<svg class="compass" id="compass" viewBox="0 0 100 100" fill="none" title="Reload latest data">
<circle cx="50" cy="50" r="44" stroke="#2a3a5c" stroke-width="3"/>
<circle cx="50" cy="50" r="3" fill="#e8c170"/>
<polygon points="50,12 57,50 50,46 43,50" fill="#ff5d6c"/>
<polygon points="50,88 43,50 50,54 57,50" fill="#5b8cff"/></svg>
<div class="brand"><h1>Market Screen <span style="color:var(--gold)">by Magellan</span></h1>
<div class="sub">Daily US screen &middot; score &amp; valuation</div></div>
<div class="asof" id="asof"></div>
</header>

<nav class="tabs">
<button class="active" data-tab="market">Market</button>
<button data-tab="screen">Screen</button>
<button data-tab="magellan">Magellan - Portfolio</button>
<button data-tab="backtest">Backtest</button>
<button data-tab="history">History</button>
</nav>

<section class="view active" id="market">
<div class="mktq">What is the market <b>expecting</b> today? - not what happened yesterday.</div>
<div class="mksub" id="mkAsof"></div>
<div class="mkhero" id="mkHero"></div>
<div id="mkGroups"></div>
<div class="mkcard"><div class="mkh">What&rsquo;s priced into rates</div><div id="mkRates"></div><div id="mkFedEcb"></div></div>
<div class="mkrow">
<div class="mkcard"><div class="mkh">Risk appetite</div><div id="mkRisk"></div></div>
<div class="mkcard"><div class="mkh">Alpha Risk Matrix</div><div id="mkArm"></div></div>
</div>
<div class="mkcard mksetup"><div class="mkh">Today&rsquo;s setup</div><div id="mkSetup"></div></div>
<div class="mkcard"><div class="mkh">Top macro news</div><div id="mkNews"></div></div>
</section>

<section class="view" id="screen">
<div class="scorebar">
<span class="lab">Minimum Magellan score</span>
<input type="range" id="scoreSlider" min="0" max="6" step="1" value="4"/>
<span class="lab">&ge; <b id="scoreVal" class="cnt">4</b></span>
<span class="lab" style="margin-left:auto"><b class="cnt" id="upCnt">0</b> up &middot; <b class="cnt" id="dnCnt">0</b> down</span>
</div>
<div class="cols">
<div><div class="colhead"><span class="dot" style="background:var(--up)"></span>Up on larger volume</div><div id="upList"></div></div>
<div><div class="colhead"><span class="dot" style="background:var(--down)"></span>Down on larger volume</div><div id="downList"></div></div>
</div>
</section>

<section class="view" id="magellan">
<div class="addbar">
<div><label class="f">Ticker</label><input class="f" id="pTicker" placeholder="e.g. ABNB" style="text-transform:uppercase"/></div>
<div><label class="f">Buy price</label><input class="f" id="pBuy" type="number" step="any" placeholder="0.00"/></div>
<div><label class="f">Shares</label><input class="f" id="pSh" type="number" step="any" placeholder="0"/></div>
<button class="btn" id="pAdd">Add holding</button>
</div>
<div id="pStatus" class="status"></div>
<div id="pBody"></div>
</section>

<section class="view" id="backtest">
<div class="scorebar">
<span class="lab">Portfolio source</span>
<div class="seg" id="srcSeg">
<button class="on" data-src="screen">Today's screen</button>
<button data-src="portfolio">My portfolio</button>
<button data-src="history">From history</button>
</div>
<span class="lab" style="margin-left:auto">Window</span>
<div class="seg" id="winSeg">
<button class="on" data-win="1y">1Y</button>
<button data-win="5y">5Y</button>
</div>
<span class="lab" id="srcExtra"></span>
</div>
<div class="scorebar" id="screenCtl">
<span class="lab">Min score</span>
<input type="range" id="btSlider" min="4" max="7" step="1" value="6"/>
<span class="lab">&ge; <b id="btScoreVal" class="cnt">6</b></span>
<span class="lab" style="margin-left:auto"><b class="cnt" id="btN">0</b> names &middot; equal weight</span>
</div>
<div class="addbar" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap">
<div><label class="f">Add ticker manually</label><input class="f" id="btTicker" placeholder="e.g. MSFT" style="text-transform:uppercase;width:130px"/></div>
<button class="btn" id="btAdd">Add to backtest</button>
<button class="btn" id="btReset" style="background:transparent;color:var(--muted);border:1px solid var(--line)">Reset to source</button>
<span class="lab" id="btAddStatus" style="align-self:center;color:var(--dim)"></span>
</div>
<div id="btChips" style="margin-bottom:6px"></div>
<div id="btBody"></div>
</section>

<section class="view" id="history">
<div class="note" id="histNote">Each time you run <b>Update Magellan</b> after the close, that day's score-&ge;4 names are
appended to <b>history.json</b> on disk (with their score &amp; close). This archive powers the
<b>&ldquo;From history&rdquo;</b> backtest - set the <b>Min score</b> slider to backtest any threshold (4-7),
each name entering on the first date it crossed it, tracked forward against the S&amp;P 500.</div>
<div class="subt" style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);margin:18px 2px 8px">Saved daily screens</div>
<div id="histBody"></div>
</section>
</div>

<div class="scrim" id="scrim"><div class="modal" id="modal"></div></div>

<script>
const DATA = /*__DATA__*/;
const PXMAP={},SCOREMAP={};[...DATA.up,...DATA.down].forEach(s=>{if(s.sym){PXMAP[s.sym.toUpperCase()]=s.price;if(s.score!=null)SCOREMAP[s.sym.toUpperCase()]=s.score;}});
const WORKER_URL=((DATA.config||{}).WORKER_URL||'').replace(/\/+$/,'');

/* ---- disclaimer: shows on entry, auto-closes after 10s, closable anytime, once per session ---- */
(function(){
 const disc=document.getElementById('disc'),ok=document.getElementById('discOk');
 if(sessionStorage.getItem('magellan_disc_ack')){return;}
 disc.classList.add('open');
 let left=10,timer;
 const close=()=>{disc.classList.remove('open');sessionStorage.setItem('magellan_disc_ack','1');clearInterval(timer);};
 ok.textContent='I understand and accept ('+left+')';
 timer=setInterval(()=>{left--;ok.textContent=left>0?'I understand and accept ('+left+')':'I understand and accept';
  if(left<=0){close();}},1000);
 ok.onclick=close;
})();

/* ---- compass: click to pull in the latest generated snapshot ---- */
(function(){
 const c=document.getElementById('compass');
 if(!c)return;
 c.onclick=async()=>{
  c.classList.add('spin');
  // re-fetch this page from disk/server; if the generated stamp changed, reload to show new data
  try{
   const txt=await (await fetch(location.href,{cache:'no-store'})).text();
   const m=txt.match(/const DATA = (\{[\s\S]*?\});\n/);
   if(m){const g=JSON.parse(m[1]).generated; if(g&&g!==DATA.generated){location.reload();return;}}
  }catch(e){}
  setTimeout(()=>c.classList.remove('spin'),1000);
 };
})();
const fmt=(n,d=2)=>(n==null||isNaN(n))?'-':Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>(n==null||isNaN(n))?'-':(n>=0?'+':'')+Number(n).toFixed(1)+'%';
const fmtCap=m=>{ if(m==null||isNaN(m)) return '-';
  if(m>=1e9) return '$'+fmt(m/1e9,1)+'B';
  if(m>=1e7) return '$'+fmt(m/1e6,0)+'M';
  return '$'+fmt(m/1e6,1)+'M'; };
document.getElementById('asof').textContent=(DATA.demo?'Demo seed \u00b7 ':'Live \u00b7 ')+DATA.generated;

/* ---- Market: what's priced in today ---- */
function mkspark(arr,color){
 if(!arr||arr.length<2)return '';
 const w=60,h=20,mn=Math.min(...arr),mx=Math.max(...arr),rng=(mx-mn)||1;
 const pts=arr.map((v,i)=>((i/(arr.length-1))*w).toFixed(1)+','+(h-((v-mn)/rng)*h).toFixed(1)).join(' ');
 return '<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" style="display:block;margin-top:6px"><polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="1.5"/></svg>';
}
function renderMarket(){
 const M=DATA.market||{};
 const col=d=>d==='up'?'var(--up)':d==='dn'?'var(--down)':'var(--muted)';
 const dash=v=>(v&&String(v).trim())?v:'-';
 const asof=document.getElementById('mkAsof');
 if(asof)asof.textContent='Snapshot as of '+(M.asof||DATA.generated||'-')+' \u00b7 refreshed each Magellan update';

 const Kh=M.risk||{}, Sh=M.setup||{}, H=document.getElementById('mkHero');
 if(H){
  const regc=Kh.dir==='off'?'var(--down)':Kh.dir==='on'?'var(--up)':'var(--muted)';
  const ang=(-90+(Kh.pos!=null?Kh.pos:50)*1.8).toFixed(1);
  const chips=(Kh.signals||[]).map(s=>'<span class="mkhchip" style="color:'+(s.dir==='off'?'var(--down)':'var(--up)')+'">'+s.name+'</span>').join('');
  H.innerHTML=
   '<svg class="mkdial" viewBox="0 0 120 120" aria-hidden="true">'+
     '<circle cx="60" cy="60" r="50" fill="none" stroke="var(--line)" stroke-width="2"/>'+
     '<g stroke="var(--dim)" stroke-width="1.4"><line x1="60" y1="13" x2="60" y2="19"/><line x1="13" y1="60" x2="19" y2="60"/><line x1="101" y1="60" x2="107" y2="60"/></g>'+
     '<text x="6" y="74" font-size="7.5" fill="var(--down)">off</text>'+
     '<text x="114" y="74" text-anchor="end" font-size="7.5" fill="var(--up)">on</text>'+
     '<g style="transform-origin:60px 60px;transform:rotate('+ang+'deg);transition:transform 1s cubic-bezier(.2,.8,.2,1)">'+
       '<path d="M60 24 L64 60 L60 65 L56 60 Z" fill="'+regc+'"/><path d="M60 96 L56 60 L60 55 L64 60 Z" fill="var(--dim)"/>'+
     '</g>'+
     '<circle cx="60" cy="60" r="4.5" fill="var(--panel)" stroke="var(--ink)" stroke-width="1.5"/>'+
   '</svg>'+
   '<div><div class="mkhlead">Today\u2019s bearing</div>'+
     '<div class="mkhreg" style="color:'+regc+'">'+(Kh.regime||'-')+'<span class="mkhcount">'+(Kh.count||'')+'</span></div>'+
     '<div class="mkhhook">'+(Sh.hook||'')+'</div>'+
     (chips?'<div class="mkhchips">'+chips+'</div>':'')+
     '<div class="mkhcta">Dive in below for rates, risk and the full setup \u2193</div>'+
   '</div>';
 }

 const G=M.groups||[];
 document.getElementById('mkGroups').innerHTML = G.length
  ? G.map(g=>'<div class="mkgrp">'+g.name+'</div><div class="mktiles">'+
      g.tiles.map(t=>'<div class="mktile"><div class="tl">'+t.label+'</div><div class="tv">'+t.value+'</div><div class="tc" style="color:'+col(t.dir)+'">'+t.chg+'</div>'+(t.spark&&t.spark.length?mkspark(t.spark,col(t.dir)):'')+'</div>').join('')+'</div>').join('')
  : '<div class="empty">Market snapshot unavailable - run a Magellan update to populate it.</div>';

 const R=M.rates||{};
 document.getElementById('mkRates').innerHTML =
  '<div class="mkrowln"><span class="k">2s10s spread</span><span class="v">'+dash(R.two_ten)+'</span></div>'+
  '<div class="mkrowln"><span class="k">10Y real yield (TIPS)</span><span class="v">'+dash(R.real_10y)+'</span></div>'+
  '<div class="mkrowln"><span class="k">5y5y inflation</span><span class="v">'+dash(R.five_y_five)+'</span></div>';
 const bar=o=>{o=o||{};const has=o.pct!=null;const p=has?Math.max(0,Math.min(100,o.pct)):0;
  return '<div class="mkbarw"><div class="mkbarl"><span>'+(o.label||'')+'</span><span class="v">'+(o.text||'-')+'</span></div><div class="mkbar"><i style="width:'+p+'%"></i></div></div>';};
 document.getElementById('mkFedEcb').innerHTML = bar(M.fed)+bar(M.ecb);

 const K=M.risk||{}, vix=K.vix||{};
 const regcol=K.dir==='off'?'var(--down)':K.dir==='on'?'var(--up)':'var(--muted)';
 let riskH='<div style="display:flex;align-items:baseline;gap:8px"><span class="mkbig" style="color:'+regcol+'">'+(K.regime||'-')+'</span><span style="font-size:12px;color:var(--muted)">'+(K.count||'')+'</span></div>';
 if(K.signals&&K.signals.length){
  riskH+='<div class="mkends"><span>risk-off</span><span>risk-on</span></div><div class="mktrk"><i style="left:'+(K.pos!=null?K.pos:50)+'%;background:var(--ink)"></i></div>';
  riskH+='<div style="margin-top:12px">'+K.signals.map(s=>'<span class="mkchip" style="background:rgba(127,127,127,.10);color:'+(s.dir==='off'?'var(--down)':'var(--up)')+'">'+s.name+'</span>').join('')+'</div>';
 }
 if(K.hyg_spx) riskH+='<div class="mkrowln"><span class="k">HYG / SPX</span><span class="v">'+K.hyg_spx+' <span style="color:'+col(K.hyg_dir)+'">'+(K.hyg_chg||'')+'</span></span></div>';
 if(K.hy_oas) riskH+='<div class="mkrowln"><span class="k">HY OAS spread</span><span class="v">'+K.hy_oas.bp+' <span style="color:'+col(K.hy_oas.dir)+'">'+(K.hy_oas.chg||'')+'</span></span></div>';
 if(vix.val) riskH+='<div class="mkrowln"><span class="k">VIX</span><span class="v">'+vix.val+' <span style="color:'+col(vix.dir)+'">'+(vix.chg||'')+'</span></span></div>';
 document.getElementById('mkRisk').innerHTML=riskH;

 const A=M.arm||{};
 let armH;
 if(A.value==null){
  armH='<div style="color:var(--muted);font-size:13px">Building history'+(A.n!=null?' ('+A.n+'/30)':'')+' - the matrix needs a few daily runs to calibrate.</div>';
 } else {
  const tag=A.elevated?'<span style="color:var(--down)">'+A.pct+'th pctile (30d) - elevated</span>':A.depressed?'<span style="color:var(--up)">'+A.pct+'th pctile (30d) - low</span>':'<span style="color:var(--muted)">'+A.pct+'th pctile (30d)</span>';
  const mk=A.elevated?'var(--down)':A.depressed?'var(--up)':'var(--ink)';
  armH='<div style="display:flex;align-items:baseline;gap:10px"><span class="mkbig">'+A.value+'</span>'+tag+'</div>';
  if(A.building) armH+='<div style="font-size:11px;color:var(--dim);margin-top:2px">building ('+A.n+'/30) - percentile still calibrating</div>';
  armH+='<div style="font-size:12px;color:var(--muted);margin:8px 0 0">Today vs its 30-day range</div>';
  armH+='<div class="mkends"><span>'+A.lo+'</span><span>'+A.hi+'</span></div><div class="mktrk"><i style="left:'+(A.pos!=null?A.pos:50)+'%;background:'+mk+'"></i></div>';
  armH+='<div style="font-size:12px;color:var(--muted);margin-top:12px;line-height:1.5">'+(A.elevated?'More downside protection is being bid than usual - a defensive tilt.':A.depressed?'Less downside protection than usual - complacent / risk-on positioning.':'Downside-protection demand is around its 30-day norm.')+'</div>';
 }
 document.getElementById('mkArm').innerHTML=armH;

 const S=M.setup||{};
 let setH='<p class="mkstory">'+(S.story||'')+'</p><div class="mkcols">';
 setH+='<div><p class="mkcolh" style="color:var(--accent)">What to price in today</p><ul class="mkbul">'+((S.price_in||[]).map(x=>'<li>'+x+'</li>').join(''))+'</ul></div>';
 setH+='<div><p class="mkcolh" style="color:var(--gold)">What to watch</p><ul class="mkbul">'+((S.watch||[]).map(x=>'<li>'+x+'</li>').join(''))+'</ul></div></div>';
 document.getElementById('mkSetup').innerHTML=setH;

 const NW=M.news||[];
 document.getElementById('mkNews').innerHTML = NW.length
  ? NW.map(n=>'<a class="mknw" href="'+n.url+'" target="_blank" rel="noopener"><span style="color:var(--dim)">\u2197</span><div><div class="hd">'+n.hd+'</div><div class="mt">'+(n.src||'')+'</div></div></a>').join('')
  : '<p class="hint">Macro headlines unavailable right now - they refresh on the next update.</p>';
}

document.querySelectorAll('nav.tabs button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('nav.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
 document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
 document.getElementById(b.dataset.tab).classList.add('active');
 if(b.dataset.tab==='market') renderMarket();
 if(b.dataset.tab==='backtest') renderBacktest();
 if(b.dataset.tab==='history') renderHistory();});

/* ---- screen + score slider ---- */
const slider=document.getElementById('scoreSlider');
slider.oninput=renderScreen;
function card(s){const c=s.chg>=0;return `<div class="card" data-sym="${s.sym}">
<div class="crow"><span class="sym">${s.sym}</span><span class="nm">${s.name||''}</span>
<span class="px">$${fmt(s.price)}</span><span class="chg ${c?'pos':'neg'}">${pct(s.chg)}</span>
<a class="tvcard" href="https://www.tradingview.com/symbols/${s.sym}/" target="_blank" rel="noopener" title="Open ${s.sym} in TradingView">TV \u2197</a></div>
<div class="meta"><span>P/E ${fmt(s.pe,1)}</span><span>Rev g ${pct(s.revg)}</span>
<span>${s.sector||''}</span><span class="score">Score ${s.score}</span></div></div>`;}
function renderScreen(){
 const min=+slider.value;document.getElementById('scoreVal').textContent=min;
 const up=DATA.up.filter(s=>s.score>=min),dn=DATA.down.filter(s=>s.score>=min);
 document.getElementById('upCnt').textContent=up.length;
 document.getElementById('dnCnt').textContent=dn.length;
 document.getElementById('upList').innerHTML=up.map(card).join('')||emptyMsg();
 document.getElementById('downList').innerHTML=dn.map(card).join('')||emptyMsg();
 document.querySelectorAll('.card').forEach(el=>el.onclick=(e)=>{if(e.target.closest('a'))return;openDetail(el.dataset.sym);});
}
function emptyMsg(){return '<div class="empty">No names at this score. Lower the slider.</div>';}
function findStock(sym){return [...DATA.up,...DATA.down].find(s=>s.sym===sym);}
renderScreen();
renderMarket();

/* ---- keep every dash off the page: clean rendered text + any dynamically inserted text ---- */
(function(){
 const fix=s=>s.replace(/[\u2014\u2013\u2212]/g,'-');
 function clean(node){
  try{const tw=document.createTreeWalker(node,NodeFilter.SHOW_TEXT,null);let n;
   while(n=tw.nextNode()){const w=fix(n.nodeValue);if(w!==n.nodeValue)n.nodeValue=w;}}catch(e){}
 }
 clean(document.body);
 new MutationObserver(ms=>{for(const m of ms){
  if(m.type==='characterData'){const w=fix(m.target.nodeValue);if(w!==m.target.nodeValue)m.target.nodeValue=w;}
  else m.addedNodes.forEach(nd=>{if(nd.nodeType===3){const w=fix(nd.nodeValue);if(w!==nd.nodeValue)nd.nodeValue=w;}else if(nd.nodeType===1)clean(nd);});
 }}).observe(document.body,{childList:true,subtree:true,characterData:true});
})();

/* ---- 1Y price chart: uses real Yahoo history (s.hist) when available ---- */
function synthSeries(sym,last){let s=0;for(const ch of sym)s=(s*31+ch.charCodeAt(0))%9973;
 const rnd=()=>{s=(s*1103515245+12345)&0x7fffffff;return s/0x7fffffff;};
 const n=252;let v=last*(0.6+rnd()*0.5);const a=[];for(let i=0;i<n;i++){v*=1+(rnd()-0.48)*0.03;a.push(v);}
 const k=last/a[n-1];return a.map(x=>x*k);}
function sparkPath(a,w,h,p=6){const mn=Math.min(...a),mx=Math.max(...a),r=(mx-mn)||1;
 return a.map((y,i)=>{const X=p+i*(w-2*p)/(a.length-1),Y=h-p-((y-mn)/r)*(h-2*p);
 return (i?'L':'M')+X.toFixed(1)+' '+Y.toFixed(1);}).join(' ');}
function chartSVG(s){
 const a=(s.hist&&s.hist.length>1)?s.hist:synthSeries(s.sym,s.price);
 const w=720,h=170;const c=a[a.length-1]>=a[0]?'var(--up)':'var(--down)';
 const label=s.hist&&s.hist.length>1?'1Y (live)':'1Y (est.)';
 return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto">
 <line class="ax" x1="0" y1="${h-1}" x2="${w}" y2="${h-1}"/>
 <path d="${sparkPath(a,w,h)}" fill="none" stroke="${c}" stroke-width="2"/>
 <text x="6" y="14" fill="var(--dim)" font-size="11">${label} high $${Math.max(...a).toFixed(2)}</text>
 <text x="6" y="${h-6}" fill="var(--dim)" font-size="11">low $${Math.min(...a).toFixed(2)}</text></svg>`;}

/* ---- DCF (5 levers only; rest from data) ---- */
function dcf(p){ // rev0,g,ebitM,capexPct,daPct,nwcPct + fixed tax,wacc,tg,years
 let rev=p.rev0,ev=0;
 for(let i=1;i<=p.years;i++){const prev=rev;rev*=1+p.g/100;
  const ebit=rev*p.ebitM/100,nopat=ebit*(1-p.tax/100),da=rev*p.daPct/100,
   capex=rev*p.capexPct/100,dnwc=(rev-prev)*p.nwcPct/100;
  ev+=(nopat+da-capex-dnwc)/Math.pow(1+p.wacc/100,i);}
 const lr=p.rev0*Math.pow(1+p.g/100,p.years),eb=lr*p.ebitM/100,np=eb*(1-p.tax/100),
  da=lr*p.daPct/100,cx=lr*p.capexPct/100,dn=lr*(p.g/100/(1+p.g/100))*p.nwcPct/100,
  fcffN=np+da-cx-dn,tv=fcffN*(1+p.tg/100)/((p.wacc-p.tg)/100);
 ev+=tv/Math.pow(1+p.wacc/100,p.years);return ev;}

const scrim=document.getElementById('scrim'),modal=document.getElementById('modal');
scrim.onclick=e=>{if(e.target===scrim)scrim.classList.remove('open');};

function mcell(k,v){return `<div class="mcell"><div class="k">${k}</div><div class="v">${v}</div></div>`;}
/* shared bar/card helpers used by both real and synthetic trend renderers */
const _fmtV=v=>{if(v==null||!isFinite(v))return '-';const a=Math.abs(v);
 if(a>=1e9)return (v<0?'-':'')+'$'+(Math.abs(v)/1e9).toFixed(1)+'B';
 if(a>=1e6)return (v<0?'-':'')+'$'+(Math.abs(v)/1e6).toFixed(0)+'M';
 if(a>=1e3)return (v<0?'-':'')+'$'+(Math.abs(v)/1e3).toFixed(0)+'K';
 return (v<0?'-':'')+'$'+Math.abs(v).toFixed(2);};
function _bars(vals,labels,tooltips){
 const clean=vals.filter(v=>v!=null&&isFinite(v));
 if(!clean.length)return '<div style="color:var(--dim);font-size:11px;padding:4px 0">No data</div>';
 const w=240,h=60,n=vals.length,gap=6,bw=Math.max(1,(w-(n-1)*gap)/n);
 const hi=Math.max(0,...clean),lo=Math.min(0,...clean),rng=(hi-lo)||1,top=4,plot=h-14-top;
 const zeroY=h-14-((0-lo)/rng)*plot;let o='';
 vals.forEach((v,i)=>{
  if(v==null||!isFinite(v))return;
  const vY=h-14-((v-lo)/rng)*plot,y=Math.min(vY,zeroY),bh=Math.max(2,Math.abs(zeroY-vY)),x=i*(bw+gap);
  const col=v>=0?'var(--up)':'var(--down)',op=(0.45+0.55*i/(n-1)).toFixed(2);
  const tip=tooltips?tooltips[i]:(v>=0?'+':'')+v.toFixed(1);
  o+=`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${col}" opacity="${op}"><title>${tip}</title></rect>`;
  o+=`<text x="${(x+bw/2).toFixed(1)}" y="${h-2}" font-size="8" text-anchor="middle">${labels[i]||''}</text>`;
 });
 return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto">${o}</svg>`;}

function trendsBlock(s){
 /* ---- Real data path (Yahoo statements embedded by Python) ---- */
 if(s.trends&&Object.keys(s.trends).length>0){
  const tr=s.trends;
  const growPct=(vals)=>{const vs=(vals||[]).filter(v=>v!=null&&isFinite(v));if(vs.length<2)return null;
   const c=vs[0],p=vs[1];if(!p||p===0)return null;return (c/Math.abs(p)-1)*100*(p<0?-1:1);};
  const cardReal=(t,mode)=>{
   const ser=mode==='quarter'?t.quarter:t.annual;
   const rawVals=(ser.values||[]).slice(0,5);const rawDates=(ser.dates||[]).slice(0,5);
   // Yahoo returns most-recent-first → reverse to chronological for display
   const vals=[...rawVals].reverse(),dates=[...rawDates].reverse();
   const g=growPct(rawVals); // growth: rawVals[0]=latest, rawVals[1]=prior
   const gl=g==null?'':`<span class="g ${g>=0?'pos':'neg'}">${(g>=0?'+':'')+g.toFixed(1)}% ${mode==='quarter'?'QoQ':'YoY'}</span>`;
   const latest=rawVals.find(v=>v!=null&&isFinite(v));
   const latestLbl=latest!=null?`<span style="color:var(--muted);font-size:10.5px;margin-right:4px">${_fmtV(latest)}</span>`:'';
   const tips=vals.map((v,i)=>_fmtV(v)+' ('+( dates[i]||'').slice(0,4)+')');
   const labShort=dates.map(d=>d.slice(mode==='quarter'?2:0,mode==='quarter'?7:4));
   return `<div class="devcard"><div class="devtop"><span class="l">${t.label}</span>
    <span style="display:flex;gap:4px;align-items:baseline">${latestLbl}${gl}</span></div>${_bars(vals,labShort,tips)}</div>`;};
  const order=['revenue','ebit','ni','eps','cfo','cfi','fcf','cash','recv','pay','assets','liab','ltdebt'];
  const avail=order.filter(k=>tr[k]);
  return `<div class="devhdr">Developments</div>
   <div class="devtoggle">
    <button class="active" data-dev="devYoY">Yearly · YoY</button>
    <button data-dev="devQoQ">Quarterly · QoQ</button></div>
   <div class="dev5 devset active" id="devYoY">${avail.map(k=>cardReal(tr[k],'annual')).join('')||'<p class="hint">No statement data.</p>'}</div>
   <div class="dev5 devset" id="devQoQ">${avail.map(k=>cardReal(tr[k],'quarter')).join('')||'<p class="hint">No statement data.</p>'}</div>`;}

 /* ---- Synthetic fallback (demo / names outside DETAIL_N set) ---- */
 let seed=0;for(const ch of (s.sym+'tr'))seed=(seed*31+ch.charCodeAt(0))%9973;
 const rng=()=>{seed=(seed*1103515245+12345)&0x7fffffff;return seed/0x7fffffff;};
 const sp=(c,d)=>c+(rng()-0.5)*2*d;
 const revY=isFinite(s.revg)&&s.revg!==0?s.revg:sp(10,15),epsY=isFinite(s.epsg)&&s.epsg!==0?s.epsg:sp(8,20);
 const defs=[
  ['Revenue',revY,1],['EBIT',sp(revY*0.9,8),1],['Net income',sp(revY*0.85,12),1],
  ['EPS',epsY,1],['Cash & cash equivalents',sp(6,18),1],['FCF / share',sp(revY*0.7,16),1],
  ['Long-term debt',sp(0,14),1],['Receivables',sp(revY*0.8,8),1],['Payables',sp(revY*0.75,8),1],
  ['Total assets',sp(7,7),1],['Total liabilities',sp(5,8),1],
  ['Cash from operating activities',sp(revY*0.8,10),1],
  ['Cash from investing activities',sp(8,25),-1]];
 const gen=(cagr,steps,sign)=>{const a=[100*sign];for(let i=1;i<5;i++){const g=(cagr/steps)*(0.5+rng());a.push(a[i-1]*(1+g/100));}return a;};
 const yLab=['21','22','23','24','25'],qLab=['2Q25','3Q25','4Q25','1Q26','2Q26'];
 const cardSyn=(label,cagr,sign,steps,lab,unit)=>{const a=gen(cagr,steps,sign);
  const g=a[3]!==0?(a[4]-a[3])/Math.abs(a[3])*100:0;
  const tips=a.map((v,i)=>'Index '+(v>=0?'+':'')+v.toFixed(1)+' (est.) · '+lab[i]);
  return `<div class="devcard"><div class="devtop"><span class="l">${label} <span style="color:var(--dim);font-size:9px">est.</span></span>
   <span class="g ${g>=0?'pos':'neg'}">${(g>=0?'+':'')+g.toFixed(1)}% ${unit}</span></div>${_bars(a,lab,tips)}</div>`;};
 const grid=(steps,lab,unit)=>defs.map(d=>cardSyn(d[0],d[1],d[2],steps,lab,unit)).join('');
 return `<div class="devhdr">Developments <span style="color:var(--dim);font-size:10px">(estimated - full data for top ${DATA.config&&DATA.config.DETAIL_N||40} names)</span></div>
  <div class="devtoggle">
   <button class="active" data-dev="devYoY">Yearly · YoY (5y)</button>
   <button data-dev="devQoQ">Quarterly · QoQ (5q)</button></div>
  <div class="dev5 devset active" id="devYoY">${grid(1,yLab,'YoY')}</div>
  <div class="dev5 devset" id="devQoQ">${grid(4,qLab,'QoQ')}</div>`;
}
/* ---- Thesis: built strictly from embedded data (score tests, statements,
   valuation, signals). A baked-in s.thesis object overrides it. No invented figures. ---- */
function trendYoY(t){ // latest YoY % from a real statement trend (values are most-recent-first)
 if(!t||!t.annual)return null;
 const v=(t.annual.values||[]).filter(x=>x!=null&&isFinite(x));
 if(v.length<2||!v[1])return null;
 return (v[0]/Math.abs(v[1])-1)*100*(v[1]<0?-1:1);
}
function thesisBlock(s){
 if(s.thesis) return thesisRender(s.thesis,s);          // baked-in override wins
 const S=[],R=[],W=[], tf={};
 (s.tests||[]).forEach(t=>tf[t.label]=t.ok);
 const fv=s.fv, price=s.price;
 /* strengths from PASSED score tests */
 if(tf['ROE > 15%'])             S.push({m:'g',t:`Strong returns - ROE ${pct(s.roe)}.`,s:'Financials'});
 if(tf['Net cash positive'])     S.push({m:'g',t:`Net cash on the balance sheet - $${fmt(s.netcash)}/share.`,s:'Balance sheet'});
 if(tf['Cash > 10% of price'])   S.push({m:'g',t:`Cash cushion - ${pct((s.cashport||0)*100)} of the price is net cash.`,s:'Balance sheet'});
 if(tf['PEG 0.5-1.0'])           S.push({m:'g',t:`Growth fairly priced - PEG ${fmt(s.peg,2)} against a ${fmt(s.pe,1)} P/E.`,s:'Valuation'});
 if(tf['Cheaper ex-cash P/E'])   S.push({m:'g',t:`Cheaper stripped of cash - ex-cash P/E ${fmt(s.newpe,1)} below the ${fmt(s.pe,1)} headline.`,s:'Valuation'});
 if(tf['Upside vs fair value'])  S.push({m:'g',t:`Model sees upside - fair value $${fmt(s.fv)} above the $${fmt(s.price)} price.`,s:'Valuation model'});
 if(tf['Debt covered by earnings'])S.push({m:'g',t:`Leverage in check - debt covered by earnings.`,s:'Balance sheet'});
 /* strengths from REAL statement trends (detail names only) */
 if(s.trends){
  const rg=trendYoY(s.trends.revenue), ng=trendYoY(s.trends.ni), eg=trendYoY(s.trends.ebit);
  if(rg!=null&&rg>=10){let x=`Top line growing - revenue ${pct(rg)} YoY`; if(eg!=null)x+=`, EBIT ${pct(eg)}`; S.push({m:'g',t:x+'.',s:'Financials'});}
  if(ng!=null&&ng>=15) S.push({m:'g',t:`Profit inflecting - net income ${pct(ng)} YoY.`,s:'Financials'});
 }
 if(isFinite(s.grossMargin)&&s.grossMargin>=50) S.push({m:'g',t:`High gross margin - ${pct(s.grossMargin)}.`,s:'Financials'});
 /* risks & weaknesses - substantive, from the name's own data (never a data-plumbing note) */
 const rich=fv&&price&&price>fv*1.15;
 if(tf['Upside vs fair value']===false&&fv) R.push({m:'r',t:`No margin of safety - the model's fair value $${fmt(s.fv)} sits below the $${fmt(s.price)} price.`,s:'Valuation model'});
 else if(rich)                   R.push({m:'r',t:`Stretched vs the model - price is ${fmt(price/fv,1)}x the $${fmt(fv)} fair value, so a lot of good news is already in.`,s:'Valuation model'});
 if(isFinite(s.pe)&&s.pe>=35)    R.push({m:'r',t:`Rich multiple - ${fmt(s.pe,1)}x earnings; the case leans on that growth actually holding up.`,s:'Valuation'});
 if(tf['ROE > 15%']===false&&isFinite(s.roe)) R.push({m:'r',t:`Returns light - ROE ${pct(s.roe)}, under the 15% bar; capital isn't compounding hard.`,s:'Financials'});
 if(tf['Net cash positive']===false) R.push({m:'a',t:`Net debt - not in a net-cash position, so it carries refinancing/balance-sheet risk into any downturn.`,s:'Balance sheet'});
 /* statement-trend weaknesses (detail names with real Yahoo statements) */
 if(s.trends){
  const rrg=trendYoY(s.trends.revenue), rng=trendYoY(s.trends.ni), reg=trendYoY(s.trends.ebit);
  if(rng!=null&&rng<0)             R.push({m:'r',t:`Earnings going backwards - net income ${pct(rng)} YoY despite the screen flag.`,s:'Financials'});
  if(rrg!=null&&rrg<0)             R.push({m:'r',t:`Top line shrinking - revenue ${pct(rrg)} YoY.`,s:'Financials'});
  else if(rrg!=null&&reg!=null&&reg<rrg-5) R.push({m:'a',t:`Margins compressing - EBIT growing ${pct(reg)} against revenue ${pct(rrg)}; costs are outrunning sales.`,s:'Financials'});
 }
 /* price-action & structural weaknesses that apply to essentially every screen name */
 if(isFinite(s.chg)&&s.chg<0)    R.push({m:'r',t:`Surfaced on heavy selling - a ${pct(s.chg)} drop on ${fmt(s.relvol,1)}x volume; the trend is against it until it bases (falling-knife risk).`,s:'Price action'});
 else if(isFinite(s.chg))        R.push({m:'a',t:`Chase risk - already up ${pct(s.chg)} on ${fmt(s.relvol,1)}x volume today; single-session pops often give part of the move back.`,s:'Price action'});
 if(isFinite(s.mcap)&&s.mcap>0&&s.mcap<2e9) R.push({m:'a',t:`Small-cap risk - ~${fmtCap(s.mcap)} market cap means thinner liquidity and wider swings than large caps.`,s:'Liquidity'});
 if(s.sector)                    R.push({m:'a',t:`Sector exposure - a single ${s.sector} name; a sector-wide drawdown would hit it whatever the screen score says.`,s:'Sector'});
 /* never leave a card empty */
 if(!S.length) S.push({m:'a',t:`Few standout positives in the data - it passes ${s.score}/7 of the screen tests.`,s:'Screen'});
 if(!R.length) R.push({m:'a',t:`Nothing in the screen fields flags a clear weakness yet (score ${s.score}/7) - that's the absence of a red flag, not a guarantee; re-check after the next report.`,s:'Screen'});
 /* what to watch (data-true, neutral) */
 if(s.trends&&trendYoY(s.trends.revenue)!=null) W.push({m:'b',t:`Whether the ${pct(trendYoY(s.trends.revenue))} revenue trend holds at the next report.`,s:'Financials'});
 if(fv&&price) W.push({m:'b',t:`The gap between price ($${fmt(price)}) and model fair value ($${fmt(fv)}).`,s:'Valuation'});
 W.push({m:'b',t:`Live headlines and sentiment - see the News tab.`,s:'News'});
 /* verdict + summary */
 const lean=S.length-R.length;
 const verdict = s.score>=5 ? (rich?'Quality screen, priced full':'Screens well across the board')
              : s.score>=3 ? 'Mixed signals - selective' : 'Weak on the screen tests';
 const stance = lean>1?'Stance: constructive - mind the price':lean<-1?'Stance: cautious - risks outweigh':'Stance: balanced';
 const mult=(fv&&price)?price/fv:null;
 const summary=`Built from the screen tests, statements and valuation fields. `
   +`${S.length} strength signal(s) vs ${R.length} risk signal(s)`
   +(mult?(mult>1?`; the stock trades ${fmt(mult,1)}x the model's fair value.`:`; the stock trades below the model's fair value.`):'.');
 return thesisRender({verdict,stance,summary,strengths:S,risks:R,watch:W,fv,price,
   sources:['Financials','TradingView'],asOf:(DATA.generated||'').slice(0,10)},s);
}
function thesisRender(t,s){
 const mkCls={g:'pos',r:'neg'}, mkSt={a:'color:var(--gold)',b:'color:var(--blue)'};
 const bullet=b=>`<span class="b"><span class="${mkCls[b.m]||''}" style="font-weight:700;${mkSt[b.m]||''}">▸</span> ${b.t}${b.s?` <span class="src">${b.s}</span>`:''}</span>`;
 const list=a=>a.map(bullet).join('');
 let gauge=''; const fv=t.fv, price=t.price;
 if(fv>0&&price>0){
  const fvPct=Math.max(0,Math.min(100,fv/price*100));
  if(price>fv){const prem=Math.max(0,100-fvPct),mult=(price/fv).toFixed(1),over=((price/fv-1)*100).toFixed(0);
   gauge=`<div class="gauge"><div class="row"><span>Model fair value $${fmt(fv)}</span><span>Price $${fmt(price)}</span></div>
    <div class="track"><div class="fv" style="width:${fvPct}%"></div><div class="prem" style="left:${fvPct}%;width:${prem}%"></div><div class="tick" style="left:${fvPct}%"></div></div>
    <div class="row"><span style="color:var(--up)">fair value zone</span><span style="color:var(--down)">+${over}% above FV · trades ${mult}x</span></div></div>`;
  } else {const up=((fv/price-1)*100).toFixed(0);
   gauge=`<div class="gauge"><div class="row"><span>Price $${fmt(price)}</span><span>Model fair value $${fmt(fv)}</span></div>
    <div class="track"><div class="fv" style="width:100%"></div><div class="tick" style="left:${(price/fv*100).toFixed(1)}%"></div></div>
    <div class="row"><span style="color:var(--up)">trades below model FV</span><span style="color:var(--up)">+${up}% to fair value</span></div></div>`;
  }
 }
 const watch=(t.watch&&t.watch.length)?`<div class="c wch"><h4>&#128065; What to watch</h4>${list(t.watch)}</div>`:'';
 const src=(t.sources||[]).map(x=>`<span class="chip2">${x}</span>`).join('');
 return `<div class="th">
  <div class="verdict"><span class="v">${t.verdict}</span><span class="m">Composite ${s.score} / 7</span>${s.mcap?`<span class="m">· ${fmtCap(s.mcap)}</span>`:''}</div>
  ${t.stance?`<div class="stance">${t.stance}</div>`:''}
  <p class="sum">${t.summary}</p>
  ${gauge}
  <div class="cols">
   <div class="c str"><h4>&#9650; Strengths</h4>${list(t.strengths)}</div>
   <div class="c rsk"><h4>&#9888; Risks &amp; weaknesses</h4>${list(t.risks)}</div>
  </div>
  ${watch}
  <div class="foot">${t.asOf?`<span>Screen as of ${t.asOf}</span>`:''}<span class="sp">Sources:</span>${src}</div>
 </div>`;
}
function peersBlock(s){
 const peers=[...DATA.up,...DATA.down].filter(p=>p.sector===s.sector && p.sym!==s.sym)
   .sort((a,b)=>(b.mcap||0)-(a.mcap||0)).slice(0,6);
 if(!peers.length) return `<p class="hint">No other ${s.sector||''} names in today\u2019s screen to compare against.</p>`;
 const row=(p,me)=>`<tr class="peerrow${me?' me':''}"><td>${p.sym}</td>
   <td>$${fmt(p.price)}</td><td>${fmt(p.pe,1)}</td><td>${pct(p.revg)}</td>
   <td>${pct(p.roe)}</td><td>${fmt(p.peg,2)}</td><td class="score">${p.score}</td></tr>`;
 return `<p class="hint">Same-sector peers from today\u2019s screen (${s.sector||''}).</p>
  <table><thead><tr><th>Ticker</th><th>Price</th><th>P/E</th><th>Rev g</th><th>ROE</th><th>PEG</th><th>Score</th></tr></thead>
  <tbody>${row(s,true)}${peers.map(p=>row(p,false)).join('')}</tbody></table>`;
}

function openDetail(sym){
 const s=findStock(sym);if(!s)return;
 // baseline from data (real where workbook has it; revenue is a labelled proxy for the demo)
 const sharesM=s.shares?s.shares/1e6:100;
 const netDebtM=((s.ltdebt||0)-(s.cash||0))/1e6;
 const revMn=s.rev?s.rev/1e6:(s.ni?Math.max(50,(s.ni/0.10)/1e6):500);
 const FIX={tax:21,wacc:9,tg:2.5,years:5,rev0:revMn,netDebt:netDebtM,shares:sharesM};
 window._FIX=FIX;
 modal.innerHTML=`<div class="mhead"><span class="sym" style="font-size:18px">${s.sym}</span>
 <span class="nm">${s.name||''}</span><span class="badge">${s.sector||''}</span>
 <a class="tvlink" href="https://www.tradingview.com/symbols/${s.sym}/" target="_blank" rel="noopener">TradingView &#8599;</a>
 <button class="x" id="mx">&times;</button></div>
 <div style="margin-top:14px">${chartSVG(s)}</div>
 <div class="subtabs">
  <button class="active" data-pane="thesisPane"><svg viewBox="0 0 24 24" width="13" height="13" style="vertical-align:-2px;margin-right:5px" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10c.7.7 1 1.4 1 2.3h6c0-.9.3-1.6 1-2.3A6 6 0 0 0 12 3z"/></svg>Thesis</button>
  <button data-pane="scorePane">Magellan Score</button>
  <button data-pane="dcfPane">DCF</button>
  <button data-pane="multPane">Multiples</button>
  <button data-pane="peerPane">Peers</button>
  <button data-pane="newsPane" data-sym="${s.sym}">News</button></div>

 <div class="pane active" id="thesisPane">${thesisBlock(s)}</div>

 <div class="pane" id="scorePane">
  <div class="bigscore"><span class="n">${s.score}</span><span class="of">Composite score / 7</span></div>
  <div class="mgrid">
   ${mcell('Price','$'+fmt(s.price))}
   ${mcell('1d change',pct(s.chg))}
   ${mcell('Rel volume',fmt(s.relvol,2)+'x')}
   ${mcell('P/E',fmt(s.pe,1))}
   ${mcell('New P/E',fmt(s.newpe,1))}
   ${mcell('P/E diff',fmt(s.pediff,1))}
   ${mcell('Rev growth',pct(s.revg))}
   ${mcell('EPS',fmt(s.eps))}
   ${mcell('EPS growth',pct(s.epsg))}
   ${mcell('ROE',pct(s.roe))}
   ${mcell('FCF / share','$'+fmt(s.fcfps))}
   ${mcell('PEG',fmt(s.peg,2))}
   ${mcell('Debt coverage',fmt(s.debtcov,2))}
   ${mcell('Fair value','$'+fmt(s.fv))}
   ${mcell('Up/Down pot.',pct(s.updown))}
   ${mcell('Net cash/share','$'+fmt(s.netcash))}
   ${mcell('Cash portion',pct(s.cashport))}
   ${mcell('Mkt cap',fmtCap(s.mcap))}
  </div>
  ${trendsBlock(s)}
  </div>

 <div class="pane" id="dcfPane">
  <p class="hint">Five levers, the rest comes from the data feed. Worst &amp; Optimistic flex all five around your base.</p>
  <div class="grid5">
   ${inp('g','Rev YoY growth %',Math.max(2,Math.round(s.revg||8)))}
   ${inp('ebitM','EBIT % of rev',15)}
   ${inp('capexPct','CapEx % of rev',5)}
   ${inp('daPct','D&amp;A % of rev',4)}
   ${inp('nwcPct','Working cap % of rev',2)}
  </div>
  <div class="baseline">
   <span>From data - <b>Revenue</b> $${fmt(FIX.rev0,0)}mn</span>
   <span><b>Net debt</b> $${fmt(FIX.netDebt,0)}mn</span>
   <span><b>Shares</b> ${fmt(FIX.shares,0)}mn</span>
   <span><b>WACC</b> ${FIX.wacc}%</span>
   <span><b>Tax</b> ${FIX.tax}%</span>
   <span><b>Terminal g</b> ${FIX.tg}%</span>
  </div>
  <div class="scen" id="dcfScen"></div></div>

 <div class="pane" id="multPane">
  <p class="hint">Target multiple &times; data metric &rarr; implied value per share.</p>
  <div class="grid5" style="grid-template-columns:repeat(2,1fr)">
   ${inp('mPe','Target P/E',Math.round(s.pe||15))}
   ${inp('mEvEbit','Target EV/EBIT',12)}
  </div>
  <div class="baseline"><span>From data - <b>EPS</b> $${fmt(s.eps)}</span>
   <span><b>EBIT/share</b> $${fmt((s.eps||0)*1.4)}</span></div>
  <div class="scen" id="multScen"></div></div>

 <div class="pane" id="peerPane">${peersBlock(s)}</div>
 <div class="pane" id="newsPane"><p class="hint" id="newsStatus">Loading news…</p><div id="newsFeed"></div><div id="redditFeed"></div></div>`;
 scrim.classList.add('open');
 document.getElementById('mx').onclick=()=>scrim.classList.remove('open');
 modal.querySelectorAll('.subtabs button').forEach(b=>b.onclick=()=>{
  modal.querySelectorAll('.subtabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  modal.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.getElementById(b.dataset.pane).classList.add('active');
  if(b.dataset.pane==='newsPane') loadNews(b.dataset.sym||s.sym);});
 modal.querySelectorAll('input.f').forEach(i=>i.oninput=()=>{recalcDCF(s);recalcMult(s);});
 modal.querySelectorAll('.devtoggle button').forEach(b=>b.onclick=()=>{
  modal.querySelectorAll('.devtoggle button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  modal.querySelectorAll('.devset').forEach(d=>d.classList.remove('active'));
  document.getElementById(b.dataset.dev).classList.add('active');});
 recalcDCF(s);recalcMult(s);
}
function inp(id,label,val){return `<div><label class="f">${label}</label>
 <input class="f" id="f_${id}" type="number" step="any" value="${val}"/></div>`;}
function gv(id){const e=document.getElementById('f_'+id);return e?parseFloat(e.value)||0:0;}

function recalcDCF(s){
 const F=window._FIX;
 const lev={g:gv('g'),ebitM:gv('ebitM'),capexPct:gv('capexPct'),daPct:gv('daPct'),nwcPct:gv('nwcPct')};
 const fix={rev0:F.rev0,tax:F.tax,wacc:F.wacc,tg:F.tg,years:F.years};
 const cases={
  worst:{...lev,...fix,g:lev.g-4,ebitM:lev.ebitM-3,capexPct:lev.capexPct+2,daPct:lev.daPct-1,nwcPct:lev.nwcPct+2},
  base:{...lev,...fix},
  opt:{...lev,...fix,g:lev.g+4,ebitM:lev.ebitM+3,capexPct:Math.max(0,lev.capexPct-2),daPct:lev.daPct+1,nwcPct:Math.max(0,lev.nwcPct-2)}};
 const out={};for(const k in cases){const ev=dcf(cases[k]);out[k]=F.shares>0?(ev-F.netDebt)/F.shares:null;}
 document.getElementById('dcfScen').innerHTML=scenHTML(out,s.price);
}
function recalcMult(s){
 const eps=s.eps||0,ebit=(s.eps||0)*1.4,pe=gv('mPe'),evx=gv('mEvEbit');
 const base=(eps*pe+ebit*evx)/2;
 document.getElementById('multScen').innerHTML=scenHTML({worst:base*0.8,base:base,opt:base*1.2},s.price);
}
function scenHTML(out,price){
 const box=(c,l,v)=>{const ud=(v!=null&&price)?(v-price)/price*100:null;
  return `<div class="box ${c}"><div class="lab">${l}</div>
  <div class="val">${v==null?'-':'$'+fmt(v)}</div>
  <div class="ud ${ud>=0?'pos':'neg'}">${ud==null?'':(ud>=0?'+':'')+ud.toFixed(0)+'% vs price'}</div></div>`;};
 return box('worst','Worst case',out.worst)+box('base','Base case',out.base)+box('opt','Optimistic',out.opt);}

/* ---- news + Reddit sentiment ---- */
const SENT_POS=['beat','surge','jump','rally','rise','gain','up','profit','record','strong','growth','buy','upgrade','bullish','exceed'];
const SENT_NEG=['miss','fall','drop','loss','down','decline','warning','cut','downgrade','bearish','weak','risk','concern','lawsuit','fraud'];
function sentimentScore(titles){
 let p=0,n=0;
 titles.forEach(t=>{const low=t.toLowerCase();SENT_POS.forEach(w=>{if(low.includes(w))p++;});SENT_NEG.forEach(w=>{if(low.includes(w))n++;});});
 return {p,n,total:titles.length};}
function sentBadge(sc){
 const net=sc.p-sc.n;const pct=sc.total?Math.round(((sc.p)/(sc.p+sc.n||1))*100):50;
 const col=net>2?'var(--up)':net<-2?'var(--down)':'var(--gold)';
 const label=net>2?'Positive':net<-2?'Negative':'Mixed';
 return `<span style="color:${col};font-weight:700">${label}</span> (${pct}% positive across ${sc.total} items)`;}
async function loadNews(sym){
 const status=document.getElementById('newsStatus');
 const newsFeed=document.getElementById('newsFeed');
 const redditFeed=document.getElementById('redditFeed');
 if(!status||!newsFeed||!redditFeed)return;
 status.textContent='Loading news…';
 // Yahoo Finance RSS via rss2json.com - CORS-safe, no API key needed
 try{
  const rss=encodeURIComponent('https://feeds.finance.yahoo.com/rss/2.0/headline?s='+sym+'&region=US&lang=en-US');
  const res=await fetch('https://api.rss2json.com/v1/api.json?rss_url='+rss);
  const d=await res.json();
  const items=(d.items||[]).slice(0,8);
  if(items.length){
   const sc=sentimentScore(items.map(n=>n.title||''));
   newsFeed.innerHTML=`<div class="devhdr" style="margin-top:0">Yahoo Finance News</div>
    <div style="font-size:11.5px;color:var(--muted);margin-bottom:10px">Sentiment: ${sentBadge(sc)}</div>`+
    items.map(n=>`<div style="margin-bottom:10px;padding:9px 12px;background:var(--panel);border:1px solid var(--line);border-radius:9px">
     <a href="${n.link||'#'}" target="_blank" rel="noopener" style="color:var(--ink);font-size:13px;font-weight:600;text-decoration:none;display:block;margin-bottom:3px">${n.title||''}</a>
     <div style="color:var(--dim);font-size:11px">${n.author||n.category||''} · ${n.pubDate?new Date(n.pubDate).toLocaleDateString():''}</div>
    </div>`).join('');
  } else { newsFeed.innerHTML='<p class="hint">No news found for '+sym+'.</p>'; }
  status.textContent='';
 }catch(e){ newsFeed.innerHTML=`<p class="hint">News feed unavailable (${e.message}).</p>`; status.textContent=''; }
 // Reddit: cross-origin JSON API works for GET requests without auth
 try{
  const rurl=`https://www.reddit.com/search.json?q=${encodeURIComponent(sym+' stock')}&sort=new&limit=10&type=link`;
  const res=await fetch(rurl,{headers:{'Accept':'application/json'}});
  const d=await res.json();
  const posts=(d.data?.children||[]).map(c=>c.data).filter(p=>p.title).slice(0,8);
  if(posts.length){
   const sc2=sentimentScore(posts.map(p=>p.title||''));
   redditFeed.innerHTML=`<div class="devhdr">Reddit</div>
    <div style="font-size:11.5px;color:var(--muted);margin-bottom:10px">Sentiment: ${sentBadge(sc2)}</div>`+
    posts.map(p=>`<div style="margin-bottom:8px;padding:9px 12px;background:var(--panel);border:1px solid var(--line);border-radius:9px">
     <a href="https://reddit.com${p.permalink}" target="_blank" rel="noopener" style="color:var(--ink);font-size:12.5px;font-weight:600;text-decoration:none;display:block;margin-bottom:3px">${p.title||''}</a>
     <div style="color:var(--dim);font-size:11px">r/${p.subreddit||''} · ▲${p.score||0} · ${new Date((p.created_utc||0)*1000).toLocaleDateString()}</div>
    </div>`).join('');
  } else { redditFeed.innerHTML=`<p class="hint">No Reddit posts found for ${sym}. <a href="https://www.reddit.com/search/?q=${encodeURIComponent(sym+' stock')}" target="_blank" style="color:var(--accent)">Search Reddit →</a></p>`; }
 }catch(e){ redditFeed.innerHTML=`<p class="hint">Reddit unavailable. <a href="https://www.reddit.com/search/?q=${encodeURIComponent(sym+' stock')}" target="_blank" style="color:var(--accent)">Open Reddit search →</a></p>`; }
}

/* ---- portfolio with LIVE price lookup ---- */
const LS='magellan_portfolio_v1';
function loadP(){try{return JSON.parse(localStorage.getItem(LS))||[]}catch(e){return []}}
function saveP(p){try{localStorage.setItem(LS,JSON.stringify(p))}catch(e){}}
let PORT=loadP();
async function livePrice(t){
 // Try Yahoo Finance chart API first (most reliable, no key needed)
 try{
  const yurl=`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(t)}?range=1d&interval=1d&corsDomain=finance.yahoo.com`;
  const res=await fetch(yurl,{headers:{'Accept':'application/json'}});
  const d=await res.json();
  const px=d?.chart?.result?.[0]?.meta?.regularMarketPrice;
  if(px&&isFinite(px))return px;
 }catch(e){}
 // Fallback: Stooq
 const url='https://stooq.com/q/l/?s='+encodeURIComponent(t.toLowerCase())+'.us&f=sd2t2ohlcv&h&e=csv';
 const r=await fetch(url);const txt=await r.text();
 const line=(txt.trim().split('\n')[1]||'').split(',');
 const close=parseFloat(line[6]);
 if(!isFinite(close)||/N\/D/i.test(txt))throw new Error('not found');
 return close;}
/* resolve price + Magellan score for any ticker:
   1) baked screen data  - instant, identical to the Screen tab
   2) the Cloudflare worker - live price + the same 7-test score (if configured)
   3) legacy in-browser price fetch - price only, no score                      */
async function lookupTicker(t){
 if(PXMAP[t]!=null){return{price:PXMAP[t],score:(SCOREMAP[t]!=null?SCOREMAP[t]:null),source:'screen'};}
 if(WORKER_URL){
  try{
   const r=await fetch(WORKER_URL+'/?t='+encodeURIComponent(t));
   const d=await r.json();
   if(d&&isFinite(d.price))return{price:d.price,score:(d.score!=null?d.score:null),source:'live'};
  }catch(e){}
 }
 const px=await livePrice(t);          // throws if unavailable
 return{price:px,score:null,source:'live'};
}
const stat=document.getElementById('pStatus');
document.getElementById('pAdd').onclick=async()=>{
 const t=document.getElementById('pTicker').value.trim().toUpperCase();
 const b=parseFloat(document.getElementById('pBuy').value);
 const sh=parseFloat(document.getElementById('pSh').value);
 if(!t||!(b>0)||!(sh>0)){stat.textContent='Enter ticker, buy price and shares.';return;}
 stat.textContent='Looking up '+t+'\u2026';
 let cur,sc=null,live=false;
 try{const r=await lookupTicker(t);cur=r.price;sc=r.score;live=true;
  stat.textContent=r.source==='screen'
    ? t+' found in today\u2019s screen \u2014 price + Magellan score loaded.'
    : (sc!=null ? t+' fetched live \u2014 price + Magellan score loaded.'
                : t+' price fetched live \u2014 score unavailable for this name.');}
 catch(e){cur=(PXMAP[t]!=null?PXMAP[t]:b);sc=(SCOREMAP[t]!=null?SCOREMAP[t]:null);
  stat.textContent='Could not fetch '+t+' live \u2014 using '+(PXMAP[t]!=null?'today\u2019s screen price':'your buy price')+'. Edit it in the Current column.';}
 PORT.push({t,b,sh,cur,sc,live});saveP(PORT);
 document.getElementById('pTicker').value='';document.getElementById('pBuy').value='';document.getElementById('pSh').value='';
 renderP();
};
function renderP(){
 const body=document.getElementById('pBody');
 if(!PORT.length){body.innerHTML='<div class="empty">No holdings yet. Add a ticker, your buy price and shares above - the live price is fetched automatically.</div>';return;}
 let cost=0,val=0;
 const rows=PORT.map((h,i)=>{const c=h.cur,hv=c*h.sh,hc=h.b*h.sh,pl=hv-hc,plp=hc?pl/hc*100:0;
  cost+=hc;val+=hv;
  return `<tr><td>${h.t} ${h.live?'<span class="badge">live</span>':'<span class="badge">manual</span>'}</td>
  <td>${h.sc!=null?('<b>'+h.sc+'</b><span style="color:var(--dim)">/7</span>'):'<span style="color:var(--dim)">&ndash;</span>'}</td>
  <td>$${fmt(h.b)}</td><td>${fmt(h.sh,0)}</td>
  <td><input class="f" style="width:90px;text-align:right;padding:4px 6px" type="number" step="any" value="${c}" data-i="${i}"/></td>
  <td>$${fmt(hv)}</td><td class="${pl>=0?'pos':'neg'}">${pl>=0?'+':''}$${fmt(pl)}</td>
  <td class="${pl>=0?'pos':'neg'}">${pct(plp)}</td>
  <td><button class="rm" data-rm="${i}">&times;</button></td></tr>`;}).join('');
 const tpl=val-cost,tplp=cost?tpl/cost*100:0;
 body.innerHTML=`<table><thead><tr><th>Ticker</th><th>Score</th><th>Buy</th><th>Shares</th><th>Current</th>
 <th>Value</th><th>P/L</th><th>Return</th><th></th></tr></thead><tbody>${rows}
 <tr class="totrow"><td>Portfolio</td><td></td><td></td><td></td><td></td><td>$${fmt(val)}</td>
 <td class="${tpl>=0?'pos':'neg'}">${tpl>=0?'+':''}$${fmt(tpl)}</td>
 <td class="${tpl>=0?'pos':'neg'}">${pct(tplp)}</td><td></td></tr></tbody></table>`;
 body.querySelectorAll('input[data-i]').forEach(inp=>inp.oninput=()=>{PORT[+inp.dataset.i].cur=parseFloat(inp.value)||0;saveP(PORT);renderP();});
 body.querySelectorAll('button[data-rm]').forEach(b=>b.onclick=()=>{PORT.splice(+b.dataset.rm,1);saveP(PORT);renderP();});
}
renderP();

/* ================= BACKTEST + HISTORY ================= */
let BT_SRC='screen';        // screen | portfolio | history
let BT_WINDOW='1y';         // 1y | 5y  (lookback window)
let BT_MANUAL=[];           // [{sym}] manually added tickers (fetched per window)
const HISTCACHE={};         // "sym|range" -> {closes,dates} live-fetch cache
const btSlider=document.getElementById('btSlider');
if(btSlider) btSlider.oninput=renderBacktest;
function normSeries(h){ if(!h||h.length<2)return null;const base=h[0];if(!base)return null;return h.map(x=>x/base*100);}

// live daily history via Yahoo chart API -> {closes,dates}, downsampled
async function fetchHist(sym,range){
 sym=sym.toUpperCase(); range=range||'1y';
 const key=sym+'|'+range;
 if(HISTCACHE[key])return HISTCACHE[key];
 // 1) embedded series baked by the Python build - no network, works on GitHub Pages
 const emb=(DATA.histmap||{})[sym];
 if(emb&&emb.c&&emb.c.length>1){
  let d=(emb.d||[]).slice(), c=emb.c.slice();
  if(range==='1y'){ // trailing ~1 year of the 5y series
   const cut=new Date(); cut.setFullYear(cut.getFullYear()-1);
   let s=d.findIndex(x=>new Date(x)>=cut); if(s<0)s=Math.max(0,c.length-64);
   d=d.slice(s); c=c.slice(s);
  }
  const target=range==='5y'?80:64; let idx=c.map((_,i)=>i);
  if(c.length>target){const step=c.length/target;idx=Array.from({length:target},(_,i)=>Math.min(c.length-1,Math.floor(i*step)));}
  const out={closes:idx.map(i=>c[i]),dates:idx.map(i=>d[i]||'')};
  HISTCACHE[key]=out; return out;
 }
 // 2) fallback: live Yahoo (works locally / non-Pages hosts; blocked by CORS on GitHub Pages)
 const url=`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?range=${range}&interval=1d&corsDomain=finance.yahoo.com`;
 const res=await fetch(url,{headers:{'Accept':'application/json'}});
 const d=await res.json();
 const r=d?.chart?.result?.[0]; if(!r)throw new Error('no data');
 const ts=r.timestamp||[], cl=(r.indicators?.quote?.[0]?.close)||[];
 let pts=ts.map((t,i)=>({t,c:cl[i]})).filter(p=>p.c!=null&&isFinite(p.c));
 const target=range==='5y'?80:64;
 if(pts.length>target){const step=pts.length/target;pts=Array.from({length:target},(_,i)=>pts[Math.min(pts.length-1,Math.floor(i*step))]);}
 const out={closes:pts.map(p=>+p.c.toFixed(4)),dates:pts.map(p=>new Date(p.t*1000).toISOString().slice(0,10))};
 HISTCACHE[key]=out; return out;
}
const fetchHist1y=sym=>fetchHist(sym,'1y');  // back-compat alias

document.querySelectorAll('#srcSeg button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('#srcSeg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 BT_SRC=b.dataset.src;
 document.getElementById('screenCtl').style.display = (BT_SRC==='screen'||BT_SRC==='history')?'flex':'none';
 renderBacktest();});
document.querySelectorAll('#winSeg button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('#winSeg button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 BT_WINDOW=b.dataset.win; renderBacktest();});
document.getElementById('btAdd').onclick=async()=>{
 const t=document.getElementById('btTicker').value.trim().toUpperCase();
 const st=document.getElementById('btAddStatus');
 if(!t){st.textContent='Type a ticker first.';return;}
 if(BT_MANUAL.find(m=>m.sym===t)){st.textContent=t+' already added.';return;}
 st.textContent='Fetching history for '+t+'…';
 try{await fetchHist(t,BT_WINDOW);BT_MANUAL.push({sym:t});
  document.getElementById('btTicker').value='';st.textContent=t+' added.';renderBacktest();}
 catch(e){st.textContent='Could not fetch '+t+' ('+e.message+').';}
};
document.getElementById('btReset').onclick=()=>{BT_MANUAL=[];document.getElementById('btAddStatus').textContent='';renderBacktest();};

// resolve the holdings (with closes/dates) for the chosen source; returns a promise
async function btHoldings(){
 let holds=[];
 if(BT_SRC==='screen'){
  const min=+btSlider.value;
  const names=[...DATA.up,...DATA.down].filter(s=>s.score>=min);
  if(BT_WINDOW==='1y'){
   holds=names.filter(s=>s.hist&&s.hist.length>1).map(s=>({sym:s.sym,closes:s.hist})); // embedded 1Y, fast
  } else { // 5Y: served from the baked-in 5y series (DATA.histmap), no live fetch
   for(const s of names){ try{const h=await fetchHist(s.sym,BT_WINDOW);holds.push({sym:s.sym,closes:h.closes,dates:h.dates});}catch(e){} }
  }
 } else if(BT_SRC==='portfolio'){
  for(const p of PORT){ try{const h=await fetchHist(p.t,BT_WINDOW);holds.push({sym:p.t,closes:h.closes,dates:h.dates});}catch(e){} }
 } else { // history: archived names with score>=slider, entering the first date they crossed it
  const min=btSlider?+btSlider.value:4;
  const seen={};(DATA.history||[]).forEach(d=>d.picks.forEach(p=>{if((p.score||0)>=min&&!seen[p.sym])seen[p.sym]={sym:p.sym,first:d.date};}));
  for(const o of Object.values(seen)){ try{const h=await fetchHist(o.sym,BT_WINDOW);holds.push({sym:o.sym,closes:h.closes,dates:h.dates,first:o.first});}catch(e){} }
 }
 for(const m of BT_MANUAL){ if(!holds.find(h=>h.sym===m.sym)){ try{const h=await fetchHist(m.sym,BT_WINDOW);holds.push({sym:m.sym,closes:h.closes,dates:h.dates,manual:true});}catch(e){} } }
 return holds.filter(h=>h.closes&&h.closes.length>1);
}

async function renderBacktest(){
 if(btSlider)document.getElementById('btScoreVal').textContent=+btSlider.value;
 const _min=btSlider?+btSlider.value:6;
 let _cnt;
 if(BT_SRC==='history'){const _s={};(DATA.history||[]).forEach(d=>d.picks.forEach(p=>{if((p.score||0)>=_min)_s[p.sym]=1;}));_cnt=Object.keys(_s).length;}
 else _cnt=[...DATA.up,...DATA.down].filter(s=>s.score>=_min).length;
 document.getElementById('btN').textContent=_cnt;
 const body=document.getElementById('btBody');
 const srcExtra=document.getElementById('srcExtra');
 srcExtra.textContent = BT_SRC==='portfolio'?'using your '+PORT.length+' holding(s)'
   : BT_SRC==='history'?'forward-tracked from '+((DATA.history||[]).length)+' saved day(s)' : '';
 if(BT_SRC!=='screen') body.innerHTML='<div class="empty">Loading live history…</div>';

 const holds=await btHoldings();
 // chips
 const chipsEl=document.getElementById('btChips');
 chipsEl.innerHTML='<span class="chip bench">SPY · benchmark</span>'+
  holds.map(h=>`<span class="chip">${h.sym}${h.manual?' <span style="color:var(--gold)">＋</span>':''}${h.first?' <span style="color:var(--dim);font-weight:400">since '+h.first.slice(5)+'</span>':''}${h.manual?` <button class="x" data-rm="${h.sym}">×</button>`:''}</span>`).join('');
 chipsEl.querySelectorAll('button[data-rm]').forEach(btn=>btn.onclick=()=>{BT_MANUAL=BT_MANUAL.filter(m=>m.sym!==btn.dataset.rm);renderBacktest();});

 if(!holds.length){
  body.innerHTML='<div class="empty">'+(BT_SRC==='portfolio'?'No holdings in your portfolio yet - add some in the Portfolio tab, or add a ticker manually above.':BT_SRC==='history'?'No history saved yet. Run an update after the close to start the archive.':'No score names with price history. Lower the score or add a ticker manually.')+'</div>';
  return;}

 // benchmark series (live for history mode so we have dates; embedded otherwise)
 let benchCloses,benchDates=null;
 if(BT_WINDOW==='5y'||BT_SRC==='history'){try{const b=await fetchHist('SPY',BT_WINDOW);benchCloses=b.closes;benchDates=b.dates;}catch(e){benchCloses=DATA.bench&&DATA.bench.hist;}}
 else benchCloses=DATA.bench&&DATA.bench.hist;
 const haveBench=benchCloses&&benchCloses.length>1;
 const L=Math.min(haveBench?benchCloses.length:Infinity,...holds.map(h=>h.closes.length));

 let port=[],spy=null,startIdx=0,label;
 if(BT_SRC==='history'){
  // forward-tracked: each name enters at its first-seen date; portfolio = avg of held, rebased at each entry
  const dates=benchDates||holds[0].dates;
  const entryIdx=h=>{const fi=(h.first||dates[0]);let e=dates.findIndex(d=>d>=fi);return e<0?0:e;};
  holds.forEach(h=>h._e=entryIdx(h));
  startIdx=Math.min(...holds.map(h=>h._e));
  for(let i=startIdx;i<L;i++){let sum=0,k=0;
   holds.forEach(h=>{if(i>=h._e){const base=h.closes[h._e];if(base){sum+=h.closes[i]/base*100;k++;}}});
   port.push(k?sum/k:100);}
  if(haveBench){const base=benchCloses[startIdx];spy=[];for(let i=startIdx;i<L;i++)spy.push(benchCloses[i]/base*100);}
  label='Magellan \u2265'+(btSlider?+btSlider.value:4)+' (forward-tracked)';
 } else {
  for(let i=0;i<L;i++){let sum=0,k=0;holds.forEach(h=>{const n=normSeries(h.closes.slice(0,L));if(n){sum+=n[i];k++;}});port.push(k?sum/k:100);}
  if(haveBench)spy=normSeries(benchCloses.slice(0,L));
  label=BT_SRC==='portfolio'?'My portfolio (equal wt.)':'Magellan ≥'+(+btSlider.value)+' (equal wt.)';
 }
 const PL=port.length;
 const portRet=port[PL-1]-100, spyRet=spy?spy[spy.length-1]-100:null, alpha=spy?portRet-spyRet:null;
 const w=900,h=340,padL=52,padR=16,padT=28,padB=34;
 const allV=[...port,...(spy||[])];let mn=Math.min(...allV),mx=Math.max(...allV);
 {const m=(mx-mn)*0.08||1;mn-=m;mx+=m;} const r=(mx-mn)||1;
 const X=i=>padL+i*(w-padL-padR)/(PL-1);
 const Y=v=>h-padB-((v-mn)/r)*(h-padT-padB);
 const xy=arr=>arr.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
 // Y axis - gridlines + return % labels (start = 0%)
 let yAxis='';{const steps=5;for(let i=0;i<=steps;i++){const v=mn+r*i/steps,yy=Y(v).toFixed(1),ret=v-100;
   yAxis+=`<line x1="${padL}" y1="${yy}" x2="${w-padR}" y2="${yy}" stroke="var(--line)" stroke-width="1"/>`
        +`<text x="${padL-6}" y="${(+yy+3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--dim)">${(ret>=0?'+':'')+ret.toFixed(0)+'%'}</text>`;}}
 // X axis - time ticks across the trailing window (real dates in history mode, else relative)
 const spanDays=BT_WINDOW==='5y'?1825:365, today=new Date();
 const xsrc=(BT_SRC==='history'&&(benchDates||(holds[0]&&holds[0].dates)))?(benchDates||holds[0].dates).slice(startIdx,startIdx+PL):null;
 const nTk=BT_WINDOW==='5y'?6:5;
 let xAxis='';for(let k=0;k<nTk;k++){const i=Math.round(k*(PL-1)/(nTk-1));let lab;
   if(xsrc&&xsrc[i]) lab=new Date(xsrc[i]).toLocaleDateString('en-US',{month:'short',year:'2-digit'});
   else lab=new Date(today.getTime()-(1-i/(PL-1))*spanDays*864e5).toLocaleDateString('en-US',{month:'short',year:'2-digit'});
   const xx=X(i).toFixed(1);
   xAxis+=`<line x1="${xx}" y1="${h-padB}" x2="${xx}" y2="${h-padB+4}" stroke="var(--dim)" stroke-width="1"/>`
        +`<text x="${xx}" y="${h-padB+16}" text-anchor="middle" font-size="9" fill="var(--dim)">${lab}</text>`;}
 const y100=Y(100).toFixed(1);
 const stat=(l,v)=>`<div class="mcell"><div class="k">${l}</div><div class="v ${v>=0?'pos':'neg'}">${v==null?'-':(v>=0?'+':'')+v.toFixed(1)+'%'}</div></div>`;
 const winLbl=BT_WINDOW==='5y'?'5-year':'1-year';
 const note = BT_SRC==='screen'?'Uses <i>today’s</i> top-rated names on past prices → carries selection &amp; survivorship bias.'
   : BT_SRC==='history'?'Each name enters on the date it first appeared in the screen → no survivorship bias. Prices are baked into the page at build time.'
   : 'Your actual holdings, equal-weighted, vs the market over the same window.';
 body.innerHTML=`
  <div class="mgrid" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px">
   ${stat(label+' · '+winLbl, portRet)}${stat('S&P 500 (SPY)', spyRet)}${stat('Outperformance', alpha)}
  </div>
  <svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px">
   ${yAxis}
   <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h-padB}" stroke="var(--dim)" stroke-width="1"/>
   <line x1="${padL}" y1="${h-padB}" x2="${w-padR}" y2="${h-padB}" stroke="var(--dim)" stroke-width="1"/>
   <line x1="${padL}" y1="${y100}" x2="${w-padR}" y2="${y100}" stroke="var(--gold)" stroke-dasharray="4 3" stroke-width="1" opacity="0.55"/>
   ${xAxis}
   ${spy?`<path d="${xy(spy)}" fill="none" stroke="var(--muted)" stroke-width="2"/>`:''}
   <path d="${xy(port)}" fill="none" stroke="var(--gold)" stroke-width="2.5"/>
   <text x="${w-padR}" y="${padT-14}" text-anchor="end" font-size="11" fill="var(--gold)">● ${label}</text>
   ${spy?`<text x="${w-padR}" y="${padT-1}" text-anchor="end" font-size="11" fill="var(--muted)">● S&P 500</text>`:''}
   <text x="${padL-44}" y="${padT-14}" font-size="9" fill="var(--dim)">return %</text>
  </svg>
  <div style="margin-top:10px;font-size:12px;color:var(--muted)">Holds <b>${holds.length}</b> name(s): <b style="color:var(--ink)">${holds.map(h=>h.sym).join(', ')}</b>, equal-weighted, rebased to 100 over the ${winLbl} window.</div>
  <p class="hint" style="margin-top:10px;line-height:1.6"><b>Read with care.</b> ${note} Past performance does not predict future results.</p>`;
}

function renderHistory(){
 const body=document.getElementById('histBody');
 const hist=DATA.history||[];
 if(!hist.length){body.innerHTML='<div class="empty">No history saved yet. Run an update after the close - today’s score-≥6 names will be archived to history.json.</div>';return;}
 const rows=[...hist].reverse().map(d=>{
  const isToday=d.date===(DATA.generated||'').slice(0,10);
  return `<tr><td>${d.date}${isToday?'<span class="daybadge">today</span>':''}</td>
   <td style="text-align:left">${(d.picks||[]).map(p=>`<span class="chip">${p.sym} <span style="color:var(--dim);font-weight:400">${p.score}${p.price!=null?'·$'+(+p.price).toFixed(2):''}</span></span>`).join('')||'<span style="color:var(--dim)">none</span>'}</td>
   <td>${(d.picks||[]).length}</td></tr>`;}).join('');
 body.innerHTML=`<table><thead><tr><th>Date</th><th style="text-align:left">Score ≥6 picks (score · close)</th><th>Count</th></tr></thead><tbody>${rows}</tbody></table>
  <p class="hint" style="margin-top:12px">The <b>“From history”</b> source in the Backtest tab reads this archive: each ticker enters the portfolio on the date it first appeared here, then is tracked forward against the S&P 500.</p>`;
}
</script></body></html>
"""


if __name__ == "__main__":
    main()
