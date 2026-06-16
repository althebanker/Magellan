#!/usr/bin/env python3
# ===========================================================================
# THIS IS screener.py  (Python).  If the line below this banner shows YAML
# like "on:" or "name: daily-screen", the wrong content was pasted here —
# this file must start with the Python triple-quote docstring.
# ===========================================================================
"""
MarketScreen — automated daily screening deck (independent of Excel/TradingView).

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
# (dashboard renderer is inlined at the bottom of this file)

FMP_KEY     = os.getenv("FMP_API_KEY", "")     # financialmodelingprep.com  (peers + comps)
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "") # finnhub.io  (peers)

# ----------------------------------------------------------------------------- CONFIG
CONFIG = dict(
    SOURCE         = "tradingview", # "tradingview" (one fast call, no throttling) or "yahoo" (local use)
    # ---- screen filters (mirror your TradingView "up/down on larger volume") ----
    PRICE_MIN      = 3.0,      # Price > $3
    CHG_ABS_MIN    = 2.0,      # |1-day change %| > 2
    AVG_VOL_MIN    = 250_000,  # 60-day average volume > 250k
    VOL_MIN        = 100_000,  # today's volume > 100k
    REL_VOL_MIN    = 1.3,      # relative volume > 1.3
    # ---- ranking / deck size ----
    SCORE_MIN      = 5,        # keep composite score >= this (your ">4")
    TOP_N          = 10,       # show at most this many per side
    SCORE_FALLBACK = 4,        # if a side has < TOP_N at SCORE_MIN, top up from this score
    ENRICH_MAX     = 45,       # only pull fundamentals for the N most-liquid movers per side
                               #   (we only show TOP_N; this keeps Yahoo from rate-limiting)
    # ---- universe ----
    UNIVERSE_FILE  = "",       # path to a CSV/txt of tickers (one per line/first col). "" = auto-fetch.
    MAX_UNIVERSE   = 0,        # 0 = no cap; else cap universe size (handy for quick test runs)
    # ---- output ----
    OUT_HTML       = "dashboard.html",
    # ---- network politeness ----
    DL_CHUNK       = 250,      # tickers per yf.download batch
    FUND_SLEEP     = 0.4,      # seconds to sleep between per-ticker fundamental pulls
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
    # clean: drop test issues, warrants/units/preferreds heuristically, bad chars
    out = []
    for s in syms:
        s = s.strip().upper()
        if not s or any(c in s for c in " $/^"):
            continue
        s = s.replace(".", "-")             # Yahoo uses BRK-B, not BRK.B
        if len(s) > 6:                      # most odd suffixes (warrants/units) are longer
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
            if parts[test_col].strip().upper() == "Y":   # skip test issues
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
    """Resilient row lookup in a yfinance statement DataFrame (index = line items)."""
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
            out.append(None if (f != f) else f)   # NaN -> None
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

    # EPS TTM YoY growth (approx from quarterly net income if EPS history absent)
    epsg = info.get("earningsGrowth")
    epsg = epsg*100 if epsg is not None else _ttm_yoy(_pick(inc_q, "Net Income"))

    # ---- derived metrics (exact Excel translation) ----
    netcash = ((cash - ltdebt)/shares) if (cash is not None and shares) else None
    swc     = (base["price"] - netcash) if netcash is not None else None
    newpe   = (swc/eps) if (swc is not None and eps) else None
    fv      = (eps*20) if eps else None
    updown  = ((fv - base["price"])/base["price"]) if fv else None
    peg     = (newpe/epsg) if (newpe is not None and epsg) else None
    debtcov = (1 if (ni is not None and ni < 0) else ((ltdebt - 2*ni)/1e6 if ni is not None else None))
    pediff  = (newpe - pe) if (newpe is not None and pe) else None
    cashport= (netcash/base["price"]) if netcash is not None else None

    # ---- PM valuation metrics (prefer Yahoo info fields; fall back to statement math) ----
    rev_ttm = info.get("totalRevenue") or (float(rev_a.iloc[0]) if rev_a is not None and len(rev_a) else None)
    om      = info.get("operatingMargins")
    ebit_ttm= (om*rev_ttm) if (om is not None and rev_ttm) else (float(ebit_a.iloc[0]) if ebit_a is not None and len(ebit_a) else None)
    ev      = info.get("enterpriseValue") or ((mcap + (info.get("totalDebt") or ltdebt) - (cash or 0)) if mcap else None)
    total_debt = info.get("totalDebt") or ltdebt or 0
    ebitda  = info.get("ebitda")
    fcf_ttm2= info.get("freeCashflow")
    dy      = info.get("dividendYield")
    div_yield = (dy if (dy and dy > 1) else (dy*100 if dy else None))   # normalize to %
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
    """Peer tickers: FMP -> Finnhub -> same-sector names from today's scan."""
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

# ----------------------------------------------------------------------------- shared scoring (used by both sources)
def compute_derived(d, cfg):
    """Given raw inputs (price, eps, pe, roe, epsg, cash, ltdebt, shares, ni, revg, fcfps),
    add the 12 derived metrics + the 7-test composite score. Mirrors your Excel exactly."""
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

# ----------------------------------------------------------------------------- TradingView screen (one fast request)
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
                      .order_by("change", ascending=ascending).limit(300).get_scanner_data()
    def fetch(fields):
        _, up   = run(fields, C("change") >  cfg["CHG_ABS_MIN"], False)
        _, down = run(fields, C("change") < -cfg["CHG_ABS_MIN"], True)
        return up, down
    try:
        up_df, down_df = fetch(core + extra)
    except Exception as e:
        print(f"  TradingView extended select failed ({e}); falling back to core fields.")
        print("  -> send Claude this message so the fundamental field names can be corrected.")
        up_df, down_df = fetch(core)
    print(f"  TradingView: {len(up_df)} up, {len(down_df)} down")
    print(f"  columns: {list(up_df.columns)}")
    def to_recs(df):
        out = []
        for _, row in df.iterrows():
            g = lambda k: (row[k] if k in df.columns and row[k]==row[k] else None)  # NaN->None
            sym = (g("name") or "").upper()
            if not sym: continue
            typ = (g("type") or "").lower()
            if typ in ("fund","etf","etn","structured","right","warrant","spc","bond"):
                continue                                  # ETFs/funds have no fundamentals — skip
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

# ----------------------------------------------------------------------------- Yahoo trends for the few finalists only
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
        revenue=trend(_pick(inc_a,"Total Revenue","Revenue"), _pick(inc_q,"Total Revenue","Revenue"), "Revenue","inc"),
        ebit   =trend(_pick(inc_a,"EBIT","Operating Income"), _pick(inc_q,"EBIT","Operating Income"), "EBIT","inc"),
        cfo    =trend(_pick(cf_a,"Operating Cash Flow","Total Cash From Operating Activities"),
                      _pick(cf_q,"Operating Cash Flow","Total Cash From Operating Activities"), "CFO","cf"),
        cfi    =trend(_pick(cf_a,"Investing Cash Flow","Total Cashflows From Investing Activities"),
                      _pick(cf_q,"Investing Cash Flow","Total Cashflows From Investing Activities"), "CFI","cf"),
        cff    =trend(_pick(cf_a,"Financing Cash Flow","Total Cash From Financing Activities"),
                      _pick(cf_q,"Financing Cash Flow","Total Cash From Financing Activities"), "CFF","cf"),
        fcf    =trend(_pick(cf_a,"Free Cash Flow"), _pick(cf_q,"Free Cash Flow"), "FCF","cf"),
        cash   =trend(_pick(bs_a,"Cash And Cash Equivalents","Cash"), _pick(bs_q,"Cash And Cash Equivalents","Cash"), "Cash & equiv.","bs"),
        ltdebt =trend(_pick(bs_a,"Long Term Debt"), _pick(bs_q,"Long Term Debt"), "Long-term debt","bs"),
    )

# ----------------------------------------------------------------------------- main
def main():
    cfg = dict(CONFIG)
    if "--quick" in sys.argv:          # fast first run: scan a small universe (~1-2 min)
        cfg["MAX_UNIVERSE"] = 500
        print("[--quick] scanning a reduced universe for a fast test run")
    print("MarketScreen — daily run", dt.date.today())

    if cfg["SOURCE"] == "tradingview":
        print("Screening via TradingView...")
        ups, downs = screen_tradingview(cfg)

        def rank_and_detail(cands):
            # show the TOP_N best-scoring real companies (slider on the page filters by score)
            keep = sorted(cands, key=lambda r: (-r["score"], -(r["flowmn"] or 0)))[: cfg["TOP_N"]]
            pool = [c["sym"] for c in cands]
            for r in keep:                                  # Yahoo only for the few finalists
                try: r["trends"] = yahoo_trends(r["sym"])
                except Exception as e: print(f"      trends failed {r['sym']}: {e}"); r["trends"] = {}
                try: r["peers"] = [peer_multiples(p) for p in get_peers(r["sym"], pool)]
                except Exception as e: print(f"      peers failed {r['sym']}: {e}")
                if cfg["FUND_SLEEP"]: time.sleep(cfg["FUND_SLEEP"])
            return keep

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
            cands = sorted(cands, key=lambda r: -r["flowmn"])     # most liquid first
            cands = cands[: cfg["ENRICH_MAX"]]                    # only score the most liquid N (avoids rate limits)
            pool = [c["sym"] for c in cands]
            for i, base in enumerate(cands, 1):
                print(f"    fundamentals {i}/{len(cands)}: {base['sym']}")
                try:
                    rec = fundamentals(base["sym"], base, cfg, sector_pool=pool, with_peers=False)
                    scored.append(rec)
                except Exception as e:
                    print(f"      ! {base['sym']}: {e}")
                if cfg["FUND_SLEEP"]: time.sleep(cfg["FUND_SLEEP"])
            keep = [r for r in scored if r["score"] >= cfg["SCORE_MIN"]]
            if len(keep) < cfg["TOP_N"]:
                extra = [r for r in scored if cfg["SCORE_FALLBACK"] <= r["score"] < cfg["SCORE_MIN"]]
                keep += sorted(extra, key=lambda r: (-r["score"], -r["flowmn"]))
            keep = sorted(keep, key=lambda r: (-r["score"], -r["flowmn"]))[: cfg["TOP_N"]]
            for r in keep:                                        # peers only for the final picks
                try:
                    r["peers"] = [peer_multiples(p) for p in get_peers(r["sym"], pool)]
                except Exception as e:
                    print(f"      peers failed for {r['sym']}: {e}")
            return keep

        print("Enriching UP side...");   up_final   = enrich_and_rank(ups)
        print("Enriching DOWN side..."); down_final = enrich_and_rank(downs)

    payload = dict(generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   config=cfg, up=up_final, down=down_final, demo=False)
    build_dashboard(payload, cfg["OUT_HTML"])
    print(f"Done -> {cfg['OUT_HTML']}  ({len(up_final)} up, {len(down_final)} down)")
    if "--no-open" not in sys.argv:    # scheduled runs can pass --no-open
        try:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(cfg["OUT_HTML"]))
        except Exception:
            pass

# ===================== inlined dashboard renderer =====================
def build_dashboard(payload, out_path="dashboard.html"):
    html = _TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, default=str))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarketScreen · daily deck</title>
<style>
  :root{
    --bg:#0e1116; --panel:#151a22; --panel2:#1b222c; --line:#262f3b;
    --ink:#e8edf4; --mut:#8b97a8; --dim:#5d6878;
    --up:#37d499; --upd:#103a2e; --down:#ff5d6c; --downd:#3a1419;
    --accent:#5b8cff; --pip:#2a3340; --warn:#f5b14c;
    --mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
    --sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);
       font-size:14px;-webkit-font-smoothing:antialiased}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums}
  a{color:var(--accent);text-decoration:none}

  header{display:flex;align-items:baseline;gap:16px;padding:14px 20px;
         border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
  header h1{font-size:15px;font-weight:650;letter-spacing:.3px;margin:0}
  header h1 b{color:var(--accent)}
  .meta{color:var(--dim);font-size:12px}
  .demo-flag{margin-left:auto;color:#0e1116;background:var(--warn);font-weight:650;
             font-size:11px;padding:3px 9px;border-radius:99px}
  .controls{display:flex;align-items:center;gap:18px;margin-left:auto}
  .controls.has-flag{margin-left:18px}
  .ctl{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mut)}
  .ctl input[type=range]{accent-color:var(--accent)}
  .ctl input[type=search]{background:var(--panel);border:1px solid var(--line);border-radius:7px;
       color:var(--ink);padding:6px 10px;font-family:var(--mono);font-size:12px;width:120px}
  kbd{font-family:var(--mono);font-size:11px;background:var(--panel2);border:1px solid var(--line);
      border-bottom-width:2px;border-radius:5px;padding:1px 6px;color:var(--mut)}

  .layout{display:grid;grid-template-columns:1fr 1fr 1.15fr;height:calc(100vh - 53px)}
  .col{overflow-y:auto;border-right:1px solid var(--line)}
  .col-head{position:sticky;top:0;background:var(--bg);padding:11px 18px 9px;
            border-bottom:1px solid var(--line);font-size:12px;letter-spacing:.6px;
            text-transform:uppercase;display:flex;align-items:center;gap:9px;z-index:2}
  .dot{width:8px;height:8px;border-radius:99px;display:inline-block}
  .col-head.up .dot{background:var(--up)} .col-head.down .dot{background:var(--down)}
  .col-head .count{margin-left:auto;color:var(--dim);font-weight:400;letter-spacing:0}

  .row{display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center;
       padding:11px 18px;border-bottom:1px solid var(--line);cursor:pointer}
  .row:hover{background:var(--panel)}
  .row.sel{background:var(--panel2);box-shadow:inset 3px 0 0 var(--accent)}
  .tkr{font-family:var(--mono);font-weight:650;font-size:14px}
  .row .name{color:var(--mut);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;
             white-space:nowrap;max-width:100%}
  .row .sub{color:var(--dim);font-size:10.5px;margin-top:1px}
  .chg{font-family:var(--mono);font-weight:650;font-size:13px;text-align:right}
  .chg.pos{color:var(--up)} .chg.neg{color:var(--down)}
  .row .px{color:var(--dim);font-size:10.5px;text-align:right;margin-top:1px}

  /* signature: 7-pip score meter */
  .pips{display:flex;gap:2px}
  .pips i{width:4px;height:14px;border-radius:1px;background:var(--pip);display:block}
  .pips i.on{background:var(--up)}
  .pips.dn i.on{background:var(--down)}
  .scorebadge{display:flex;flex-direction:column;align-items:flex-end;gap:4px}
  .scorebadge .sval{font-family:var(--mono);font-size:11px;color:var(--mut)}

  /* detail */
  .detail{overflow-y:auto;padding:0 0 60px}
  .empty{color:var(--dim);text-align:center;margin-top:90px;font-size:13px;line-height:1.7}
  .d-head{padding:18px 22px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;
          background:var(--bg);z-index:2}
  .d-head .top{display:flex;align-items:baseline;gap:12px}
  .d-head .tkr{font-size:22px}
  .d-head .px{font-family:var(--mono);font-size:18px;margin-left:auto}
  .d-head .chg{font-size:14px}
  .d-head .name{color:var(--mut);font-size:13px;margin-top:3px}
  .d-head .name span{color:var(--dim)}

  .sect{padding:14px 22px;border-bottom:1px solid var(--line)}
  .sect h3{font-size:11px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim);
           margin:0 0 11px;font-weight:600}

  .tests{display:grid;grid-template-columns:1fr 1fr;gap:7px 16px}
  .test{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mut)}
  .test .mk{width:15px;height:15px;border-radius:4px;display:grid;place-items:center;
            font-size:10px;flex-shrink:0;background:var(--panel2);color:var(--dim)}
  .test.ok{color:var(--ink)} .test.ok .mk{background:var(--upd);color:var(--up)}

  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
        border:1px solid var(--line);border-radius:9px;overflow:hidden}
  .cell{background:var(--panel);padding:9px 11px}
  .cell .k{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.4px}
  .cell .v{font-family:var(--mono);font-size:14px;margin-top:3px}
  .cell .v.pos{color:var(--up)} .cell .v.neg{color:var(--down)}

  table.comps{width:100%;border-collapse:collapse;font-size:11.5px}
  table.comps th{color:var(--dim);font-weight:500;text-align:right;padding:6px 8px;
       border-bottom:1px solid var(--line);text-transform:uppercase;font-size:9.5px;letter-spacing:.4px}
  table.comps th:first-child{text-align:left}
  table.comps td{padding:6px 8px;text-align:right;font-family:var(--mono);border-bottom:1px solid var(--panel2)}
  table.comps td:first-child{text-align:left;font-weight:600}
  table.comps tr.focal{background:var(--panel2)}
  table.comps tr.focal td:first-child{color:var(--accent)}
  table.comps .cheap{color:var(--up)} table.comps .rich{color:var(--down)}
  .chart{margin-bottom:14px}
  .chart .lbl{display:flex;align-items:baseline;gap:8px;margin-bottom:5px}
  .chart .lbl b{font-size:12px;font-weight:600}
  .chart .lbl .g{font-family:var(--mono);font-size:11px;margin-left:auto}
  .chart .lbl .g.pos{color:var(--up)} .chart .lbl .g.neg{color:var(--down)}
  .seg{display:inline-flex;gap:2px;background:var(--panel2);border-radius:6px;padding:2px;margin-bottom:12px}
  .seg button{background:none;border:0;color:var(--mut);font-family:var(--mono);font-size:11px;
              padding:3px 10px;border-radius:4px;cursor:pointer}
  .seg button.on{background:var(--panel);color:var(--ink)}
  svg text{font-family:var(--mono);fill:var(--dim);font-size:9px}
  .tvlink{display:inline-block;margin-top:4px;font-size:12px}
  @media (max-width:1100px){.layout{grid-template-columns:1fr}.col,.detail{height:auto}}
</style></head>
<body>
<header>
  <h1>Market<b>Screen</b></h1>
  <span class="meta" id="meta"></span>
  <span class="demo-flag" id="demoflag" style="display:none">PREVIEW · seeded from your file</span>
  <div class="controls" id="controls">
    <div class="ctl">min score <input type="range" id="thresh" min="0" max="7" value="0">
      <span class="num" id="threshv">0</span></div>
    <div class="ctl"><input type="search" id="search" placeholder="filter…"></div>
    <div class="ctl"><kbd>j</kbd><kbd>k</kbd> move <kbd>↹</kbd> side</div>
  </div>
</header>

<div class="layout">
  <div class="col" id="colUp"><div class="col-head up"><span class="dot"></span>Up on larger volume<span class="count" id="cUp"></span></div><div id="listUp"></div></div>
  <div class="col" id="colDown"><div class="col-head down"><span class="dot"></span>Down on larger volume<span class="count" id="cDown"></span></div><div id="listDown"></div></div>
  <div class="detail" id="detail"><div class="empty">Select a company<br><span style="color:var(--dim)">click a row or press <kbd>j</kbd>/<kbd>k</kbd></span></div></div>
</div>

<script>
const DATA = /*__DATA__*/;
const $ = s => document.querySelector(s);
let thresh = 0, side = 'up', sel = 0, mode = {};

document.getElementById('meta').textContent =
  'generated ' + DATA.generated + ' · ' + (DATA.up.length+DATA.down.length) + ' names';
if(DATA.demo){ $('#demoflag').style.display='inline-block'; $('#controls').classList.add('has-flag'); }

// ---------- formatting ----------
const fmtNum = (v,d=2)=> (v==null||isNaN(v))?'–':Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
const fmtPct = (v,d=1)=> (v==null||isNaN(v))?'–':(v>0?'+':'')+Number(v).toFixed(d)+'%';
const fmtX   = (v,d=2)=> (v==null||isNaN(v))?'–':Number(v).toFixed(d)+'×';
const fmtBig = v => { if(v==null||isNaN(v))return '–'; const a=Math.abs(v);
  if(a>=1e9)return (v/1e9).toFixed(2)+'B'; if(a>=1e6)return (v/1e6).toFixed(1)+'M';
  if(a>=1e3)return (v/1e3).toFixed(0)+'K'; return v.toFixed(0); };

function pips(score,dn){ let h='<div class="pips'+(dn?' dn':'')+'">';
  for(let i=0;i<7;i++) h+='<i class="'+(i<score?'on':'')+'"></i>'; return h+'</div>'; }

// ---------- lists ----------
function visible(arr){ const q=$('#search').value.trim().toUpperCase();
  return arr.filter(r=> r.score>=thresh && (!q || r.sym.includes(q) || (r.name||'').toUpperCase().includes(q))); }

function renderList(arr,elId,dn){
  const v=visible(arr), el=$('#'+elId); el.innerHTML='';
  v.forEach((r,i)=>{
    const d=document.createElement('div'); d.className='row'; d.dataset.side=dn?'down':'up'; d.dataset.i=i;
    const cls=r.chg>=0?'pos':'neg';
    d.innerHTML=`<div class="scorebadge">${pips(r.score,dn)}<span class="sval">${r.score}/7</span></div>
      <div><div class="tkr">${r.sym}</div><div class="name">${r.name||''}</div>
           <div class="sub">${r.sector||''} · $${fmtNum(r.flowmn,0)}M flow · ${fmtX(r.relvol,1)} rvol</div></div>
      <div><div class="chg ${cls}">${fmtPct(r.chg)}</div><div class="px num">$${fmtNum(r.price)}</div></div>`;
    d.onclick=()=>{ side=dn?'down':'up'; sel=i; sync(); };
    el.appendChild(d);
  });
  return v;
}
let vUp=[], vDown=[];
function refresh(){
  vUp=renderList(DATA.up,'listUp',false); vDown=renderList(DATA.down,'listDown',true);
  $('#cUp').textContent=vUp.length; $('#cDown').textContent=vDown.length;
  const cur=(side==='up'?vUp:vDown); if(sel>=cur.length) sel=Math.max(0,cur.length-1);
  sync();
}
function sync(){
  document.querySelectorAll('.row').forEach(r=>r.classList.remove('sel'));
  const cur=(side==='up'?vUp:vDown); if(!cur.length){ $('#detail').innerHTML='<div class="empty">No names at this score.</div>'; return; }
  const elId=side==='up'?'listUp':'listDown';
  const node=$('#'+elId).children[sel]; if(node){ node.classList.add('sel'); node.scrollIntoView({block:'nearest'}); }
  detail(cur[sel]);
}

// ---------- charts ----------
function bars(series,kind){
  const dates=(series.dates||[]).slice(0,kind==='annual'?5:8).reverse();
  const vals =(series.values||[]).slice(0,kind==='annual'?5:8).reverse();
  if(!vals.length||vals.every(v=>v==null)) return '<svg viewBox="0 0 320 70"><text x="0" y="38">no data</text></svg>';
  const W=320,H=70,pad=14,n=vals.length,bw=(W-pad)/n*0.62,gap=(W-pad)/n;
  const mx=Math.max(0,...vals.filter(v=>v!=null)), mn=Math.min(0,...vals.filter(v=>v!=null));
  const span=(mx-mn)||1, z=H-pad-(0-mn)/span*(H-2*pad);
  let s=`<svg viewBox="0 0 ${W} ${H}"><line x1="0" y1="${z.toFixed(1)}" x2="${W}" y2="${z.toFixed(1)}" stroke="var(--line)"/>`;
  vals.forEach((v,i)=>{ if(v==null)return;
    const x=pad+i*gap+(gap-bw)/2, h=Math.abs(v)/span*(H-2*pad);
    const y=v>=0? z-h : z; const col=v>=0?'var(--up)':'var(--down)';
    s+=`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,h).toFixed(1)}" rx="1.5" fill="${col}" opacity="0.85"/>`;
    s+=`<text x="${(x+bw/2).toFixed(1)}" y="${H-2}" text-anchor="middle">${(dates[i]||'').slice(kind==='annual'?0:5,kind==='annual'?4:10)}</text>`;
  });
  return s+'</svg>';
}
function growth(series,kind){
  const vals=(series.values||[]); if(vals.length<2||vals[0]==null) return null;
  const prevIdx=kind==='annual'?1:1; const a=vals[0], b=vals[prevIdx];
  if(b==null||b===0) return null; return (a/Math.abs(b)-1)*100*(b<0?-1:1);
}
function chartBlock(t,key){
  const series=mode[key]==='quarter'?t.quarter:t.annual;
  const g=growth(series,mode[key]||'annual');
  const gl=g==null?'':`<span class="g ${g>=0?'pos':'neg'}">${fmtPct(g)} ${mode[key]==='quarter'?'QoQ':'YoY'}</span>`;
  return `<div class="chart"><div class="lbl"><b>${t.label}</b>${gl}</div>${bars(series,mode[key]||'annual')}</div>`;
}

function peersTable(r){
  const peers=r.peers||[];
  const focal=Object.assign({sym:r.sym, evEbitda:r.evEbitda, evEbit:r.evEbit, pe:r.pe,
                             evSales:r.evSales, revg:r.revg, ebitMargin:r.ebitMargin}, {focal:true});
  const rows=[focal,...peers];
  const med=k=>{const a=rows.map(x=>x[k]).filter(v=>v!=null&&!isNaN(v)).sort((p,q)=>p-q);
                return a.length?a[Math.floor(a.length/2)]:null;};
  const mE=med('evEbitda');
  const c=(v,m,d=1,suf='×')=>{ if(v==null||isNaN(v))return '<td>–</td>';
    const cl=(m!=null&&!isNaN(m))?(v<m?'cheap':v>m?'rich':''):''; return `<td class="${cl}">${Number(v).toFixed(d)}${suf}</td>`; };
  let h=`<table class="comps"><thead><tr><th>Peer</th><th>EV/EBITDA</th><th>EV/EBIT</th><th>P/E</th><th>EV/Sales</th><th>Rev g</th><th>EBIT m</th></tr></thead><tbody>`;
  rows.forEach(x=>{ h+=`<tr class="${x.focal?'focal':''}"><td>${x.sym}</td>`+
      c(x.evEbitda,mE)+c(x.evEbit,med('evEbit'))+c(x.pe,med('pe'))+c(x.evSales,med('evSales'),1)+
      `<td>${x.revg==null?'–':fmtPct(x.revg)}</td><td>${x.ebitMargin==null?'–':fmtPct(x.ebitMargin)}</td></tr>`; });
  h+=`</tbody></table>`;
  if(!peers.length) h+=`<div style="color:var(--dim);font-size:11px;margin-top:7px">No peer data — add an FMP/Finnhub key (see README) to populate comps.</div>`;
  else h+=`<div style="color:var(--dim);font-size:10.5px;margin-top:7px">Green = cheaper than peer median, red = richer.</div>`;
  return h;
}

// ---------- detail ----------
function detail(r){
  const cls=r.chg>=0?'pos':'neg';
  const T=r.tests||[];
  const testHtml=T.map(t=>`<div class="test ${t.ok?'ok':''}"><span class="mk">${t.ok?'✓':'·'}</span>${t.label}</div>`).join('');
  const tr=r.trends||{};
  const order=['revenue','ebit','cfo','cfi','cff','fcf','cash','ltdebt'];
  const charts=order.filter(k=>tr[k]).map(k=>{
    if(!(k in mode)) mode[k]='annual';
    return `<div data-k="${k}">
      <div class="seg"><button class="${mode[k]==='annual'?'on':''}" onclick="setMode('${k}','annual')">Annual / YoY</button>
      <button class="${mode[k]==='quarter'?'on':''}" onclick="setMode('${k}','quarter')">Quarterly / QoQ</button></div>
      ${chartBlock(tr[k],k)}</div>`;
  }).join('');
  const cell=(k,v,c='')=>`<div class="cell"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`;
  $('#detail').innerHTML=`
    <div class="d-head"><div class="top"><span class="tkr">${r.sym}</span>
      <span class="chg ${cls}">${fmtPct(r.chg)}</span>
      <span class="px">$${fmtNum(r.price)}</span></div>
      <div class="name">${r.name||''} <span>· ${r.sector||''}</span></div>
      <a class="tvlink" href="https://www.tradingview.com/symbols/${r.sym}/" target="_blank">open on TradingView →</a>
    </div>
    <div class="sect"><h3>Why it scored — ${r.score}/7</h3><div class="tests">${testHtml}</div></div>
    <div class="sect"><h3>Snapshot</h3><div class="grid">
      ${cell('Mkt cap', fmtBig(r.mcap||r.mcapmn*1e6))}
      ${cell('Enterprise value', fmtBig(r.ev))}
      ${cell('$ flow', '$'+fmtNum(r.flowmn,0)+'M')}
      ${cell('Rel vol', fmtX(r.relvol,1))}
      ${cell('52w range', r.range52==null?'–':Math.round(r.range52*100)+'%')}
      ${cell('Beta', fmtNum(r.beta))}
    </div></div>
    <div class="sect"><h3>Valuation multiples</h3><div class="grid">
      ${cell('EV/EBITDA', fmtX(r.evEbitda,1))}
      ${cell('EV/EBIT', fmtX(r.evEbit,1))}
      ${cell('EV/Sales', fmtX(r.evSales,1))}
      ${cell('P/E', fmtX(r.pe))}
      ${cell('P/FCF', fmtX(r.pfcf,1))}
      ${cell('Net debt/EBITDA', fmtX(r.netDebtEbitda,1), r.netDebtEbitda<0?'pos':'')}
    </div></div>
    <div class="sect"><h3>Quality & margins</h3><div class="grid">
      ${cell('Gross margin', fmtPct(r.grossMargin,1))}
      ${cell('EBIT margin', fmtPct(r.ebitMargin,1))}
      ${cell('Net margin', fmtPct(r.netMargin,1))}
      ${cell('ROIC', fmtPct(r.roic,1), r.roic>10?'pos':'')}
      ${cell('ROE', fmtPct(r.roe,1), r.roe>15?'pos':'')}
      ${cell('FCF yield', fmtPct(r.fcfYield,1), r.fcfYield>5?'pos':'')}
    </div></div>
    <div class="sect"><h3>Peers — comps</h3>${peersTable(r)}</div>
    <div class="sect"><h3>Your model</h3><div class="grid">
      ${cell('Fair value', '$'+fmtNum(r.fv))}
      ${cell('Up/down', fmtPct(r.updown*100), r.updown>0?'pos':'neg')}
      ${cell('PEG', fmtX(r.peg), (r.peg>=.5&&r.peg<=1)?'pos':'')}
      ${cell('P/E diff', fmtNum(r.pediff), r.pediff<0?'pos':'neg')}
      ${cell('New P/E', fmtX(r.newpe), r.pediff<0?'pos':'')}
      ${cell('Share ex-cash', '$'+fmtNum(r.swc))}
    </div></div>
    <div class="sect"><h3>Balance & cash</h3><div class="grid">
      ${cell('Cash', fmtBig(r.cash))}
      ${cell('LT debt', fmtBig(r.ltdebt))}
      ${cell('Net cash/sh', '$'+fmtNum(r.netcash), r.netcash>0?'pos':'neg')}
      ${cell('Div yield', fmtPct(r.divYield,2))}
      ${cell('Cash/share', '$'+fmtNum(r.cashps))}
      ${cell('CapEx/share', '$'+fmtNum(r.capexps))}
    </div></div>
    <div class="sect"><h3>Financial trends</h3>${charts||'<div style="color:var(--dim);font-size:12px">No statement data.</div>'}</div>`;
}
window.setMode=(k,m)=>{ mode[k]=m; const cur=(side==='up'?vUp:vDown); detail(cur[sel]); };

// ---------- controls ----------
$('#thresh').oninput=e=>{ thresh=+e.target.value; $('#threshv').textContent=thresh; refresh(); };
$('#search').oninput=refresh;
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT')return;
  const cur=(side==='up'?vUp:vDown);
  if(e.key==='j'||e.key==='ArrowDown'){ sel=Math.min(sel+1,cur.length-1); sync(); e.preventDefault(); }
  if(e.key==='k'||e.key==='ArrowUp'){ sel=Math.max(sel-1,0); sync(); e.preventDefault(); }
  if(e.key==='Tab'){ side=side==='up'?'down':'up'; sel=0; sync(); e.preventDefault(); }
});
// ---------- auto-refresh: reload an open tab when a new build lands ----------
async function checkFresh(){
  try{
    const txt=await (await fetch(location.href,{cache:'no-store'})).text();
    const m=txt.match(/const DATA = (\{.*?\});/s);
    if(m){ const g=JSON.parse(m[1]).generated; if(g && g!==DATA.generated) location.reload(); }
  }catch(e){}
}
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) checkFresh(); });
setInterval(checkFresh, 30*60*1000);
refresh();
</script>
</body></html>
"""


if __name__ == "__main__":
    main()
