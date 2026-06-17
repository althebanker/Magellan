#!/usr/bin/env python3
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
    DL_CHUNK       = 250,
    FUND_SLEEP     = 0.4,
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

def yahoo_history(sym, points=64):
    import yfinance as yf
    try:
        hist = yf.Ticker(sym).history(period="1y", interval="1d")
        closes = [float(x) for x in hist["Close"].tolist() if x == x]
        if len(closes) > points:
            step = len(closes) / points
            closes = [closes[min(len(closes)-1, int(i*step))] for i in range(points)]
        return [round(x, 4) for x in closes]
    except Exception as e:
        print(f"      history failed {sym}: {e}")
        return []

# ----------------------------------------------------------------------------- main
def main():
    cfg = dict(CONFIG)
    if "--quick" in sys.argv:
        cfg["MAX_UNIVERSE"] = 500
        print("[--quick] scanning a reduced universe for a fast test run")
    print("MarketScreen — daily run", dt.date.today())

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

    payload = dict(generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   config=cfg, up=up_final, down=down_final, demo=False)
    build_dashboard(payload, cfg["OUT_HTML"])
    print(f"Done -> {cfg['OUT_HTML']}  ({len(up_final)} up, {len(down_final)} down)")
    if "--no-open" not in sys.argv:
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

_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Market Screen by Magellan</title>
<style>
:root{--bg:#0b1220;--panel:#111b2e;--panel2:#0e1726;--line:#1f2c44;--ink:#e8eef9;
--muted:#8aa0c2;--dim:#5b6f92;--up:#3fe08a;--down:#ff5d6c;--gold:#e8c170;--accent:#5b8cff;--blue:#9fc0ff;}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 80% -10%,#152b4a 0,transparent 60%),var(--bg);
color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px;}
header{display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--line);padding-bottom:18px;}
.compass{width:42px;height:42px;flex:none}
.brand h1{margin:0;font-size:22px;letter-spacing:.5px;font-weight:700}
.brand .sub{color:var(--muted);font-size:12px;letter-spacing:2px;text-transform:uppercase}
.asof{margin-left:auto;color:var(--dim);font-size:12px;text-align:right}
nav.tabs{display:flex;gap:6px;margin:18px 0 8px}
nav.tabs button{background:transparent;border:1px solid var(--line);color:var(--muted);
padding:8px 16px;border-radius:999px;cursor:pointer;font-size:13px}
nav.tabs button.active{background:var(--panel);color:var(--ink);border-color:var(--accent)}
.view{display:none}.view.active{display:block}
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
</style></head><body>
<div class="wrap">
<header>
<svg class="compass" viewBox="0 0 100 100" fill="none">
<circle cx="50" cy="50" r="44" stroke="#2a3a5c" stroke-width="3"/>
<circle cx="50" cy="50" r="3" fill="#e8c170"/>
<polygon points="50,12 57,50 50,46 43,50" fill="#ff5d6c"/>
<polygon points="50,88 43,50 50,54 57,50" fill="#5b8cff"/></svg>
<div class="brand"><h1>Market Screen <span style="color:var(--gold)">by Magellan</span></h1>
<div class="sub">Daily US screen &middot; score &amp; valuation</div></div>
<div class="asof" id="asof"></div>
</header>

<nav class="tabs">
<button class="active" data-tab="screen">Screen</button>
<button data-tab="magellan">Magellan &mdash; Portfolio</button>
</nav>

<section class="view active" id="screen">
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
</div>

<div class="scrim" id="scrim"><div class="modal" id="modal"></div></div>

<script>
const DATA = /*__DATA__*/;
const PXMAP={};[...DATA.up,...DATA.down].forEach(s=>{if(s.sym)PXMAP[s.sym.toUpperCase()]=s.price;});
const fmt=(n,d=2)=>(n==null||isNaN(n))?'\u2013':Number(n).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=n=>(n==null||isNaN(n))?'\u2013':(n>=0?'+':'')+Number(n).toFixed(1)+'%';
const fmtCap=m=>{ if(m==null||isNaN(m)) return '\u2013';
  if(m>=1e9) return '$'+fmt(m/1e9,1)+'B';
  if(m>=1e7) return '$'+fmt(m/1e6,0)+'M';
  return '$'+fmt(m/1e6,1)+'M'; };
document.getElementById('asof').textContent=(DATA.demo?'Demo seed \u00b7 ':'Live \u00b7 ')+DATA.generated;

document.querySelectorAll('nav.tabs button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('nav.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
 document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
 document.getElementById(b.dataset.tab).classList.add('active');});

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
const _fmtV=v=>{if(v==null||!isFinite(v))return '–';const a=Math.abs(v);
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
 return `<div class="devhdr">Developments <span style="color:var(--dim);font-size:10px">(estimated — full data for top ${DATA.config&&DATA.config.DETAIL_N||40} names)</span></div>
  <div class="devtoggle">
   <button class="active" data-dev="devYoY">Yearly · YoY (5y)</button>
   <button data-dev="devQoQ">Quarterly · QoQ (5q)</button></div>
  <div class="dev5 devset active" id="devYoY">${grid(1,yLab,'YoY')}</div>
  <div class="dev5 devset" id="devQoQ">${grid(4,qLab,'QoQ')}</div>`;
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
  <button class="active" data-pane="scorePane">Magellan Score</button>
  <button data-pane="dcfPane">DCF</button>
  <button data-pane="multPane">Multiples</button>
  <button data-pane="peerPane">Peers</button>
  <button data-pane="newsPane" data-sym="${s.sym}">News</button></div>

 <div class="pane active" id="scorePane">
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
   <span>From data &mdash; <b>Revenue</b> $${fmt(FIX.rev0,0)}mn</span>
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
  <div class="baseline"><span>From data &mdash; <b>EPS</b> $${fmt(s.eps)}</span>
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
  <div class="val">${v==null?'\u2013':'$'+fmt(v)}</div>
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
 // Yahoo Finance RSS via rss2json.com — CORS-safe, no API key needed
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
const stat=document.getElementById('pStatus');
document.getElementById('pAdd').onclick=async()=>{
 const t=document.getElementById('pTicker').value.trim().toUpperCase();
 const b=parseFloat(document.getElementById('pBuy').value);
 const sh=parseFloat(document.getElementById('pSh').value);
 if(!t||!(b>0)||!(sh>0)){stat.textContent='Enter ticker, buy price and shares.';return;}
 stat.textContent='Fetching live price for '+t+'\u2026';
 let cur,live=false;
 try{cur=await livePrice(t);live=true;stat.textContent='';}
 catch(e){cur=(PXMAP[t]!=null?PXMAP[t]:b);
  stat.textContent='Could not fetch '+t+' live \u2014 using '+(PXMAP[t]!=null?'today\u2019s screen price':'your buy price')+'. Edit it in the Current column.';}
 PORT.push({t,b,sh,cur,live});saveP(PORT);
 document.getElementById('pTicker').value='';document.getElementById('pBuy').value='';document.getElementById('pSh').value='';
 renderP();
};
function renderP(){
 const body=document.getElementById('pBody');
 if(!PORT.length){body.innerHTML='<div class="empty">No holdings yet. Add a ticker, your buy price and shares above \u2014 the live price is fetched automatically.</div>';return;}
 let cost=0,val=0;
 const rows=PORT.map((h,i)=>{const c=h.cur,hv=c*h.sh,hc=h.b*h.sh,pl=hv-hc,plp=hc?pl/hc*100:0;
  cost+=hc;val+=hv;
  return `<tr><td>${h.t} ${h.live?'<span class="badge">live</span>':'<span class="badge">manual</span>'}</td>
  <td>$${fmt(h.b)}</td><td>${fmt(h.sh,0)}</td>
  <td><input class="f" style="width:90px;text-align:right;padding:4px 6px" type="number" step="any" value="${c}" data-i="${i}"/></td>
  <td>$${fmt(hv)}</td><td class="${pl>=0?'pos':'neg'}">${pl>=0?'+':''}$${fmt(pl)}</td>
  <td class="${pl>=0?'pos':'neg'}">${pct(plp)}</td>
  <td><button class="rm" data-rm="${i}">&times;</button></td></tr>`;}).join('');
 const tpl=val-cost,tplp=cost?tpl/cost*100:0;
 body.innerHTML=`<table><thead><tr><th>Ticker</th><th>Buy</th><th>Shares</th><th>Current</th>
 <th>Value</th><th>P/L</th><th>Return</th><th></th></tr></thead><tbody>${rows}
 <tr class="totrow"><td>Portfolio</td><td></td><td></td><td></td><td>$${fmt(val)}</td>
 <td class="${tpl>=0?'pos':'neg'}">${tpl>=0?'+':''}$${fmt(tpl)}</td>
 <td class="${tpl>=0?'pos':'neg'}">${pct(tplp)}</td><td></td></tr></tbody></table>`;
 body.querySelectorAll('input[data-i]').forEach(inp=>inp.oninput=()=>{PORT[+inp.dataset.i].cur=parseFloat(inp.value)||0;saveP(PORT);renderP();});
 body.querySelectorAll('button[data-rm]').forEach(b=>b.onclick=()=>{PORT.splice(+b.dataset.rm,1);saveP(PORT);renderP();});
}
renderP();
</script></body></html>
"""


if __name__ == "__main__":
    main()
