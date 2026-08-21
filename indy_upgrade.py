#!/usr/bin/env python3
"""
indy_upgrade.py - one-shot patcher that adds the indy.finance-style panels to screener.py.

Adds to the Market tab:
  * "The read" one-line summary + a live ticker strip
  * Global market map  - 25 national indices, log-area treemap, region-weighted headers
  * Segment strips     - equity segments / rates & credit / real assets & FX / risk & crypto
  * Today's notable moves - biggest movers with 3M range and position-in-range
  * Benchmarks table, Screen movers table
  * Live wire          - multi-source RSS headlines with age stamps

Usage:
    python indy_upgrade.py               # patches ./screener.py (writes screener.py.bak first)
    python indy_upgrade.py path/to/screener.py

Idempotent: re-running on an already-patched file exits without changing anything.
"""

import os, shutil, sys

MARK = "INDY PANELS (market map, segments, wire)"

# ============================================================================ 1. PYTHON BLOCK
PY_BLOCK = r'''
# ============================ INDY PANELS (market map, segments, wire) ============================
# Everything below is fetched at build time and baked into dashboard.html, same as the rest of the
# market snapshot, so the page still opens offline / on GitHub Pages with no cross-origin calls.

# (country, tile label, yahoo symbol, region, approx. market cap USD bn, currency)
INDY_MAP = [
    ("Mainland China", "Shanghai",   "000001.SS",   "Asia-Pacific", 11500, "CNY"),
    ("Japan",          "Nikkei",     "^N225",       "Asia-Pacific",  6800, "JPY"),
    ("Hong Kong",      "Hang Seng",  "^HSI",        "Asia-Pacific",  5200, "HKD"),
    ("India",          "Nifty 50",   "^NSEI",       "Asia-Pacific",  5100, "INR"),
    ("Taiwan",         "TAIEX",      "^TWII",       "Asia-Pacific",  2600, "TWD"),
    ("South Korea",    "KOSPI",      "^KS11",       "Asia-Pacific",  2300, "KRW"),
    ("Australia",      "ASX 200",    "^AXJO",       "Asia-Pacific",  1900, "AUD"),
    ("Indonesia",      "IDX",        "^JKSE",       "Asia-Pacific",   750, "IDR"),
    ("Singapore",      "STI",        "^STI",        "Asia-Pacific",   650, "SGD"),
    ("United Kingdom", "FTSE 100",   "^FTSE",       "Europe",        3300, "GBP"),
    ("France",         "CAC 40",     "^FCHI",       "Europe",        3100, "EUR"),
    ("Germany",        "DAX",        "^GDAXI",      "Europe",        2600, "EUR"),
    ("Switzerland",    "SMI",        "^SSMI",       "Europe",        2100, "CHF"),
    ("Netherlands",    "AEX",        "^AEX",        "Europe",        1200, "EUR"),
    ("Sweden",         "OMXS30",     "^OMX",        "Europe",        1000, "SEK"),
    ("Spain",          "IBEX 35",    "^IBEX",       "Europe",         900, "EUR"),
    ("Italy",          "FTSE MIB",   "FTSEMIB.MI",  "Europe",         850, "EUR"),
    ("Denmark",        "OMXC25",     "^OMXC25",     "Europe",         700, "DKK"),
    ("United States",  "S&P 500",    "^GSPC",       "Americas",     55000, "USD"),
    ("Canada",         "TSX",        "^GSPTSE",     "Americas",      3200, "CAD"),
    ("Brazil",         "Bovespa",    "^BVSP",       "Americas",       900, "BRL"),
    ("Mexico",         "IPC",        "^MXX",        "Americas",       450, "MXN"),
    ("Israel",         "TA-125",     "^TA125.TA",   "Middle East & Africa", 350, "ILS"),
    ("South Africa",   "Top 40",     "^JN0U.JO",    "Middle East & Africa", 320, "USD"),
    ("Turkey",         "BIST 100",   "^XU100",      "Middle East & Africa", 250, "TRY"),
]
INDY_REGION_ORDER = ["Asia-Pacific", "Europe", "Americas", "Middle East & Africa"]

# (label, symbol, tooltip, is_tightening_when_up)
INDY_SEGMENTS = [
    ("Equity segments", [
        ("Nasdaq 100",   "^NDX",  "US mega-cap technology", False),
        ("Russell 2000", "^RUT",  "US small caps - the domestic economy", False),
        ("MSCI World",   "URTH",  "Developed-market equity", False),
        ("EM equity",    "EEM",   "MSCI Emerging Markets", False)]),
    ("Rates & credit", [
        ("US 10y",       "^TNX",  "10-year Treasury yield - a rise here is a tightening signal", True),
        ("Long bonds",   "TLT",   "20-year-plus Treasuries", False),
        ("IG credit",    "LQD",   "Investment-grade corporate bonds", False),
        ("High yield",   "HYG",   "High-yield corporate bonds - the risk canary", False)]),
    ("Real assets & FX", [
        ("Gold",         "GC=F",  "COMEX front month", False),
        ("Brent",        "BZ=F",  "Brent crude", False),
        ("Copper",       "HG=F",  "Copper - the industrial cycle", False),
        ("Dollar",       "DX-Y.NYB", "ICE dollar index - a rise here is a tightening signal", True),
        ("EUR/USD",      "EURUSD=X", "Euro against the dollar", False)]),
    ("Risk & crypto", [
        ("VIX",          "^VIX",  "S&P implied volatility - a rise here is a tightening signal", True),
        ("Bitcoin",      "BTC-USD", "BTC/USD", False),
        ("Ether",        "ETH-USD", "ETH/USD", False)]),
]

INDY_BENCH = [("S&P 500", "^GSPC", "US$"), ("NASDAQ", "^IXIC", "US$"),
              ("DAX", "^GDAXI", "\u20ac"), ("FTSE 100", "^FTSE", "\u00a3"),
              ("NIKKEI 225", "^N225", "JP\u00a5"), ("HANG SENG", "^HSI", "HK$")]

INDY_STRIP = [("S&P 500", "^GSPC", "US$"), ("VIX", "^VIX", ""), ("Gold", "GC=F", "US$"),
              ("WTI crude", "CL=F", "US$"), ("Bitcoin", "BTC-USD", "US$")]

# extra instruments that can win "today's notable moves"
INDY_SECTORS = [("Technology", "XLK"), ("Health Care", "XLV"), ("Financials", "XLF"),
                ("Energy", "XLE"), ("Industrials", "XLI"), ("Consumer disc.", "XLY"),
                ("Staples", "XLP"), ("Utilities", "XLU"), ("Materials", "XLB"),
                ("Real estate", "XLRE"), ("Silver", "SI=F"), ("WTI crude", "CL=F"),
                ("Natural gas", "NG=F"), ("Semis", "SOXX")]

INDY_WIRE_FEEDS = [
    ("MW",   "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("INV",  "https://www.investing.com/rss/news_25.rss"),
    ("SA",   "https://seekingalpha.com/market_currents.xml"),
]


def _indy_http(url, timeout=15):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Magellan screener)",
                                                   "Accept": "text/xml,application/xml,text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"      indy http failed {url[:52]}: {e}")
        return None


def _indy_closes(syms, period="3mo"):
    # one bulk yfinance pull -> {symbol: [closes ascending]}; per-symbol retry only where needed
    import yfinance as yf
    syms = list(dict.fromkeys([s for s in syms if s]))
    out = {}
    if not syms:
        return out
    data = None
    try:
        data = yf.download(syms, period=period, interval="1d", group_by="ticker",
                           auto_adjust=False, threads=True, progress=False)
    except Exception as e:
        print(f"      indy bulk download failed: {e}")
    for s in syms:
        c = []
        try:
            df = data[s] if (data is not None and len(syms) > 1) else data
            c = [float(x) for x in df["Close"].tolist() if x == x]
        except Exception:
            c = []
        if len(c) < 2:
            try:
                h = yf.Ticker(s).history(period=period, interval="1d")
                c = [float(x) for x in h["Close"].tolist() if x == x]
            except Exception:
                c = []
        if len(c) >= 2:
            out[s] = c
    missing = [s for s in syms if s not in out]
    if missing:
        print(f"      indy: no data for {len(missing)} symbol(s): {', '.join(missing[:8])}")
    return out


def _indy_age(secs):
    if secs is None or secs < 0:
        return ""
    m = int(secs // 60)
    if m < 60:
        return f"{max(m, 1)}m"
    h = m // 60
    if h < 48:
        return f"{h}h"
    return f"{h // 24}d"


def _indy_wire(k=14, per_feed=6):
    # multi-source RSS -> [{src, hd, url, age}], newest first
    import re, html as _html, email.utils as eut
    items = []
    for tag, url in INDY_WIRE_FEEDS:
        txt = _indy_http(url)
        if not txt:
            continue
        n = 0
        for m in re.finditer(r"<item[ >](.*?)</item>", txt, re.S):
            b = m.group(1)
            tm = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", b, re.S)
            lm = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", b, re.S)
            dm = re.search(r"<pubDate>(.*?)</pubDate>", b, re.S)
            if not (tm and lm):
                continue
            hd = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(1)).strip())
            lk = _html.unescape(lm.group(1).strip())
            if not hd or not lk.startswith("http"):
                continue
            ts = None
            if dm:
                try:
                    ts = eut.parsedate_to_datetime(dm.group(1).strip()).timestamp()
                except Exception:
                    ts = None
            items.append(dict(src=tag, hd=hd[:150], url=lk, ts=ts))
            n += 1
            if n >= per_feed:
                break
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    for it in items:
        it["age"] = _indy_age(now - it["ts"]) if it.get("ts") else ""
    items.sort(key=lambda x: -(x.get("ts") or 0))
    for it in items:
        it.pop("ts", None)
    return items[:k]


def _indy_pct(v, dp=2):
    return "-" if v is None else ("%+.*f%%" % (dp, v))


def build_indy():
    # the whole indy panel payload: map, segments, notable, bench, strip, read, wire
    import math
    syms = ([m[2] for m in INDY_MAP]
            + [t[1] for _g, ts in INDY_SEGMENTS for t in ts]
            + [b[1] for b in INDY_BENCH]
            + [s[1] for s in INDY_STRIP]
            + [s[1] for s in INDY_SECTORS])
    px = _indy_closes(syms, "3mo")

    def last(sym):
        c = px.get(sym)
        return c[-1] if c else None

    def chg(sym):
        c = px.get(sym)
        if not c or len(c) < 2 or not c[-2]:
            return None
        return (c[-1] / c[-2] - 1) * 100

    def r2(v):
        return None if v is None else round(v, 2)

    # ---- global market map (log-area tiles, cap-weighted region headers) ----
    regions = []
    for rname in INDY_REGION_ORDER:
        tiles = []
        for country, label, sym, reg, cap, ccy in INDY_MAP:
            if reg != rname:
                continue
            tiles.append(dict(country=country, label=label, sym=sym, ccy=ccy, cap=cap,
                              w=round(math.log10(cap) + 1, 3),
                              chg=r2(chg(sym)), value=r2(last(sym))))
        if not tiles:
            continue
        live = [t for t in tiles if t["chg"] is not None]
        capsum = sum(t["cap"] for t in live) or 1
        rchg = sum(t["cap"] * t["chg"] for t in live) / capsum if live else None
        regions.append(dict(name=rname, chg=r2(rchg),
                            up=sum(1 for t in live if t["chg"] > 0),
                            down=sum(1 for t in live if t["chg"] < 0),
                            w=round(sum(t["w"] for t in tiles), 3), tiles=tiles))

    # ---- segment strips ----
    segments = []
    for gname, ts in INDY_SEGMENTS:
        segments.append(dict(name=gname, tiles=[
            dict(label=l, sym=s, tip=tip, tighten=bool(tg), chg=r2(chg(s)), value=r2(last(s)))
            for l, s, tip, tg in ts]))

    # ---- today's notable moves (biggest absolute movers across everything tracked) ----
    pool = ([(l, s) for l, s in INDY_SECTORS]
            + [(t[0], t[1]) for _g, ts in INDY_SEGMENTS for t in ts]
            + [(m[1], m[2]) for m in INDY_MAP])
    seen, notable = set(), []
    for label, sym in pool:
        if sym in seen:
            continue
        seen.add(sym)
        ser = px.get(sym) or []
        c = chg(sym)
        if c is None or len(ser) < 10:
            continue
        lo, hi, cur = min(ser), max(ser), ser[-1]
        notable.append(dict(label=label, sym=sym, chg=r2(c), value=r2(cur),
                            lo=r2(lo), hi=r2(hi),
                            pos=int(round((cur - lo) / ((hi - lo) or 1) * 100)),
                            chg3m=r2((cur / ser[0] - 1) * 100) if ser[0] else None))
    notable.sort(key=lambda x: -abs(x["chg"]))
    notable = notable[:4]

    bench = [dict(label=l, sym=s, ccy=c, value=r2(last(s)), chg=r2(chg(s))) for l, s, c in INDY_BENCH]
    strip = [dict(label=l, sym=s, ccy=c, value=r2(last(s)), chg=r2(chg(s))) for l, s, c in INDY_STRIP]

    # ---- the read: one line, written from the numbers above ----
    sp, ru, vx, gd, bt = chg("^GSPC"), chg("^RUT"), chg("^VIX"), chg("GC=F"), chg("BTC-USD")
    bits = []
    if sp is not None:
        bits.append("the S&P is " + _indy_pct(sp))
    if ru is not None:
        bits.append("small caps " + _indy_pct(ru))
    if vx is not None:
        bits.append("the VIX " + _indy_pct(vx))
    if gd is not None:
        bits.append("gold " + _indy_pct(gd))
    if bt is not None:
        bits.append("bitcoin " + _indy_pct(bt))
    lead = ", ".join(bits) if bits else "the tracked market is quiet"
    reg = ", ".join(f"{r['name']} {_indy_pct(r['chg'])}" for r in regions if r["chg"] is not None)
    read = lead[0].upper() + lead[1:] + ("." if lead else "")
    if reg:
        read += " By region: " + reg + "."
    if notable:
        read += " Biggest single move: %s %s." % (notable[0]["label"], _indy_pct(notable[0]["chg"]))

    print("      indy wire...")
    wire = _indy_wire()

    return dict(read=read, strip=strip, map=regions, segments=segments,
                notable=notable, bench=bench, wire=wire)

'''

# ============================================================================ 2. CSS BLOCK
CSS_BLOCK = r'''
/* ---- indy panels ---- */
.iread{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--gold);
border-radius:8px;padding:11px 16px;color:var(--ink);font-size:14px;line-height:1.6;margin:6px 0 10px}
.istrip{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 4px}
.istk{background:var(--panel);border:1px solid var(--line);border-radius:99px;padding:4px 12px;
font-size:12px;font-variant-numeric:tabular-nums;color:var(--muted)}
.istk b{color:var(--ink);font-weight:600}
.imap{position:relative;width:100%;padding-bottom:56%;background:var(--panel2);
border:1px solid var(--line);border-radius:12px;overflow:hidden}
.irg{position:absolute;pointer-events:none;padding:2px}
.irh{display:flex;gap:8px;align-items:baseline;font-size:11px;color:var(--muted);
padding:2px 4px;white-space:nowrap;overflow:hidden}
.irh .ct{color:var(--dim);font-size:10px}
.ity{position:absolute;border:1px solid var(--panel2);border-radius:4px;overflow:hidden;
padding:4px 6px;transition:filter .12s}
.ity:hover{filter:brightness(1.35)}
.ity .l{font-size:11px;font-weight:600;color:var(--ink);line-height:1.15;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ity .c{font-size:11px;font-variant-numeric:tabular-nums;line-height:1.3}
.ity .v{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}
.imapnote{color:var(--dim);font-size:11.5px;line-height:1.6;margin:10px 2px 0}
.inchips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.inchip{background:var(--panel2);border:1px solid var(--line);color:var(--muted);
border-radius:8px;padding:5px 11px;font-size:12px;cursor:pointer;font-variant-numeric:tabular-nums}
.inchip.on{border-color:var(--accent);color:var(--ink);background:var(--panel)}
.inbig{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;margin-bottom:10px}
.iwr{display:flex;gap:9px;align-items:baseline;padding:8px 0;border-top:1px solid var(--line);text-decoration:none}
.iwr:first-child{border-top:none}
.iwr .src{color:var(--accent);font-size:10px;font-weight:700;letter-spacing:.5px;
min-width:34px;flex:none}
.iwr .hd{color:var(--ink);font-size:12.5px;line-height:1.4;flex:1}
.iwr:hover .hd{color:var(--blue)}
.iwr .ag{color:var(--dim);font-size:10.5px;font-variant-numeric:tabular-nums;flex:none}
@media(max-width:700px){.imap{padding-bottom:104%}.ity{padding:3px 4px}.ity .l{font-size:10px}}
'''

# ============================================================================ 3. HTML BLOCKS
HTML_TOP = r'''
<div class="iread" id="indyRead"></div>
<div class="istrip" id="indyStrip"></div>'''

HTML_MAIN = r'''
<div class="mkcard"><div class="mkh">Global market map - today's session</div>
 <div class="imap" id="indyMap"></div>
 <p class="imapnote">25 national equity indices, coloured by today's move. <b>Tile area is logarithmic</b>
 in each market's approximate capitalisation, so bigger is still bigger, but every exchange stays readable.
 Region figures are capitalisation-weighted. Hover any tile for the level and the exact move.</p></div>
<div id="indySeg"></div>
<div class="mkcard"><div class="mkh">Today's notable moves</div><div id="indyNotable"></div>
 <p class="hint">Chosen from the day's moves across every index, segment and commodity tracked here - not a fixed list.</p></div>
<div class="mkrow">
 <div class="mkcard"><div class="mkh">Benchmarks</div><div id="indyBench"></div></div>
 <div class="mkcard"><div class="mkh">Screen movers</div><div id="indyMovers"></div></div>
</div>
<div class="mkcard"><div class="mkh">Live wire</div><div id="indyWire"></div></div>'''

# ============================================================================ 4. JS BLOCK
JS_BLOCK = r'''
/* ================= INDY panels: map, segments, notable, bench, wire ================= */
function iCol(c){return (c==null)?'var(--muted)':(c>0.02?'var(--up)':c<-0.02?'var(--down)':'var(--muted)');}
function iHeat(c){
 if(c==null)return 'rgba(127,140,170,.12)';
 const v=Math.max(-2.5,Math.min(2.5,c));
 if(v>0.05)return 'rgba(63,224,138,'+(0.10+0.50*(v/2.5)).toFixed(2)+')';
 if(v<-0.05)return 'rgba(255,93,108,'+(0.10+0.50*(-v/2.5)).toFixed(2)+')';
 return 'rgba(127,140,170,.12)';}
function iPct(v,d){return (v==null||!isFinite(v))?'-':((v>=0?'+':'')+Number(v).toFixed(d==null?2:d)+'%');}
function iNum(v){if(v==null||!isFinite(v))return '-';
 return Math.abs(v)>=1000?Number(v).toLocaleString('en-US',{maximumFractionDigits:0})
  :Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}
function iEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');}

function iWorst(row,side){
 const s=row.reduce((a,i)=>a+i.a,0); if(!s)return Infinity;
 const mx=Math.max(...row.map(i=>i.a)),mn=Math.min(...row.map(i=>i.a));
 if(!mn)return Infinity;
 return Math.max((side*side*mx)/(s*s),(s*s)/(side*side*mn));}
function iSquarify(items,x,y,w,h){
 const out=[];
 let list=items.filter(i=>i.w>0).map(i=>({ref:i.ref,w:i.w})).sort((a,b)=>b.w-a.w);
 if(!list.length||w<=0||h<=0)return out;
 const total=list.reduce((s,i)=>s+i.w,0)||1,k=(w*h)/total;
 list.forEach(i=>i.a=i.w*k);
 let rx=x,ry=y,rw=w,rh=h,i0=0;
 while(i0<list.length){
  const side=Math.max(1,Math.min(rw,rh));
  let row=[list[i0]],best=iWorst(row,side),j=i0+1;
  while(j<list.length){
   const cand=row.concat([list[j]]),r=iWorst(cand,side);
   if(r>best)break;
   row=cand;best=r;j++;}
  const rowArea=row.reduce((s,i)=>s+i.a,0);
  if(rw>=rh){
   const cw=Math.min(rw,rowArea/Math.max(rh,0.001));let cy=ry;
   row.forEach(it=>{const ch=it.a/Math.max(cw,0.001);out.push({ref:it.ref,x:rx,y:cy,w:cw,h:ch});cy+=ch;});
   rx+=cw;rw-=cw;
  }else{
   const ch=Math.min(rh,rowArea/Math.max(rw,0.001));let cx=rx;
   row.forEach(it=>{const cw2=it.a/Math.max(ch,0.001);out.push({ref:it.ref,x:cx,y:ry,w:cw2,h:ch});cx+=cw2;});
   ry+=ch;rh-=ch;}
  i0=j;}
 return out;}

let INOTE=[],INOTEI=0;
function renderIndyMap(regions){
 const el=document.getElementById('indyMap');if(!el)return;
 if(!regions||!regions.length){el.innerHTML='<div class="empty" style="margin:16px">Market map unavailable - it fills on the next Magellan update.</div>';return;}
 const W=1000,H=560,px=v=>(v/W*100).toFixed(3)+'%',py=v=>(v/H*100).toFixed(3)+'%';
 const rr=iSquarify(regions.map(r=>({ref:r,w:r.w})),0,0,W,H);
 let out='';
 rr.forEach(R=>{
  const r=R.ref;
  out+='<div class="irg" style="left:'+px(R.x)+';top:'+py(R.y)+';width:'+px(R.w)+';height:'+py(R.h)+'">'
    +'<div class="irh"><span style="color:var(--ink);font-weight:600">'+r.name+'</span>'
    +'<span style="color:'+iCol(r.chg)+'">'+iPct(r.chg)+'</span>'
    +'<span class="ct">'+r.up+'\u2191 '+r.down+'\u2193</span></div></div>';
  const inner=iSquarify(r.tiles.map(t=>({ref:t,w:t.w})),R.x+3,R.y+24,Math.max(1,R.w-6),Math.max(1,R.h-27));
  inner.forEach(T=>{
   const t=T.ref,mid=(T.w>50&&T.h>28),big=(T.w>84&&T.h>52);
   const tip=t.country+' \u00b7 '+t.label+' \u00b7 '+iPct(t.chg)+(t.value!=null?' \u00b7 '+iNum(t.value)+' '+t.ccy:'');
   out+='<div class="ity" title="'+iEsc(tip)+'" style="left:'+px(T.x)+';top:'+py(T.y)+';width:'+px(T.w)+';height:'+py(T.h)+';background:'+iHeat(t.chg)+'">'
     +(mid?'<div class="l">'+t.label+'</div>':'')
     +(mid?'<div class="c" style="color:'+iCol(t.chg)+'">'+iPct(t.chg,1)+'</div>':'')
     +(big&&t.value!=null?'<div class="v">'+iNum(t.value)+' '+t.ccy+'</div>':'')
     +'</div>';});
 });
 el.innerHTML=out;}

function renderIndyNotable(list){
 INOTE=list||[];
 const el=document.getElementById('indyNotable');if(!el)return;
 if(!INOTE.length){el.innerHTML='<p class="hint">No standout moves in the tracked set today.</p>';return;}
 if(INOTEI>=INOTE.length)INOTEI=0;
 const f=INOTE[INOTEI];
 el.innerHTML='<div class="inchips">'+INOTE.map((n,i)=>'<button class="inchip'+(i===INOTEI?' on':'')+'" data-i="'+i+'">'+n.label+' <span style="color:'+iCol(n.chg)+'">'+iPct(n.chg)+'</span></button>').join('')+'</div>'
  +'<div class="inbig">'+iNum(f.value)+' <span style="font-size:13px;font-weight:400;color:'+iCol(f.chg)+'">'+iPct(f.chg)+' today</span></div>'
  +'<div class="mkrowln"><span class="k">3M range</span><span class="v">'+iNum(f.lo)+' \u2013 '+iNum(f.hi)+'</span></div>'
  +'<div class="mkrowln"><span class="k">In range</span><span class="v">'+f.pos+'%</span></div>'
  +'<div class="mkrowln"><span class="k">3M change</span><span class="v" style="color:'+iCol(f.chg3m)+'">'+iPct(f.chg3m)+'</span></div>'
  +'<div class="mkends"><span>'+iNum(f.lo)+'</span><span>'+iNum(f.hi)+'</span></div>'
  +'<div class="mktrk"><i style="left:'+f.pos+'%;background:var(--ink)"></i></div>';
 el.querySelectorAll('button[data-i]').forEach(b=>b.onclick=()=>{INOTEI=+b.dataset.i;renderIndyNotable(INOTE);});}

function renderIndyMovers(){
 const el=document.getElementById('indyMovers');if(!el)return;
 const all=[...DATA.up,...DATA.down].filter(s=>isFinite(s.chg))
   .sort((a,b)=>Math.abs(b.chg)-Math.abs(a.chg)).slice(0,6);
 if(!all.length){el.innerHTML='<p class="hint">No screen names yet - run a Magellan update.</p>';return;}
 el.innerHTML='<table class="mkcat"><tbody>'+all.map(s=>'<tr><td>'+s.sym+'</td>'
   +'<td style="text-align:right">$'+fmt(s.price)+'</td>'
   +'<td style="text-align:right;color:'+iCol(s.chg)+'">'+iPct(s.chg)+'</td>'
   +'<td style="text-align:right;color:var(--gold)">'+s.score+'/7</td></tr>').join('')+'</tbody></table>'
   +'<p class="hint">Largest absolute moves in today\u2019s screen, with the Magellan score.</p>';}

function renderIndy(){
 const I=(DATA.market||{}).indy||{};
 const rd=document.getElementById('indyRead');
 if(rd){if(I.read){rd.textContent=I.read;rd.style.display='';}else{rd.style.display='none';}}
 const st=document.getElementById('indyStrip');
 if(st)st.innerHTML=(I.strip||[]).map(t=>'<span class="istk"><b>'+t.label+'</b> '
   +(t.ccy||'')+iNum(t.value)+' <span style="color:'+iCol(t.chg)+'">'+iPct(t.chg)+'</span></span>').join('');
 renderIndyMap(I.map||[]);
 const sg=document.getElementById('indySeg');
 if(sg)sg.innerHTML=(I.segments||[]).map(g=>'<div class="mkgrp">'+g.name+'</div><div class="mktiles">'
   +g.tiles.map(t=>'<div class="mktile" title="'+iEsc(t.tip||'')+'"><div class="tl">'+t.label
     +(t.tighten?' <span style="color:var(--gold)" title="a rise here is a tightening signal">\u2195</span>':'')+'</div>'
     +'<div class="tv" style="color:'+iCol(t.chg)+'">'+iPct(t.chg,1)+'</div>'
     +'<div class="tc" style="color:var(--dim)">'+iNum(t.value)+'</div></div>').join('')+'</div>').join('');
 renderIndyNotable(I.notable||[]);
 const bn=document.getElementById('indyBench');
 if(bn)bn.innerHTML=(I.bench||[]).length?('<table class="mkcat"><tbody>'+I.bench.map(b=>'<tr><td>'+b.label+'</td>'
   +'<td style="text-align:right">'+(b.ccy||'')+iNum(b.value)+'</td>'
   +'<td style="text-align:right;color:'+iCol(b.chg)+'">'+iPct(b.chg)+'</td></tr>').join('')+'</tbody></table>')
   :'<p class="hint">Benchmarks unavailable right now.</p>';
 const wr=document.getElementById('indyWire');
 if(wr)wr.innerHTML=(I.wire||[]).length?I.wire.map(n=>'<a class="iwr" href="'+n.url+'" target="_blank" rel="noopener">'
   +'<span class="src">'+n.src+'</span><span class="hd">'+n.hd+'</span><span class="ag">'+(n.age||'')+'</span></a>').join('')
   :'<p class="hint">Wire unavailable right now - it refreshes on the next update.</p>';
 renderIndyMovers();}
'''

# ============================================================================ 5. ANCHORS
ANCHOR_PY = ('# ----------------------------------------------------------------------------- universe\n'
             'NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"')

ANCHOR_RETURN = ('    return dict(asof=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), groups=groups,\n'
                 '                rates=rates, fed=fed, ecb=ecb, risk=risk, arm=arm, setup=setup, news=news)')

RETURN_NEW = ('    print("      indy panels (map, segments, wire)...")\n'
              '    try:\n'
              '        indy = build_indy()\n'
              '    except Exception as e:\n'
              '        print(f"      indy build failed: {e}")\n'
              '        indy = None\n'
              '    return dict(asof=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), groups=groups,\n'
              '                rates=rates, fed=fed, ecb=ecb, risk=risk, arm=arm, setup=setup,\n'
              '                news=news, indy=indy)')

ANCHOR_CSS = '.disc .ok:hover{filter:brightness(1.08)}'

ANCHOR_HTML_TOP = '<div class="mksub" id="mkAsof"></div>'
ANCHOR_HTML_MAIN = '<div id="mkGroups"></div>'

ANCHOR_JS = ("  : '<p class=\"hint\">Macro headlines unavailable right now - they refresh on the next update.</p>';\n"
             "}")

JS_NEW = ("  : '<p class=\"hint\">Macro headlines unavailable right now - they refresh on the next update.</p>';\n"
          " try{renderIndy();}catch(e){console.warn('indy render failed',e);}\n"
          "}\n"
          + JS_BLOCK)


def patch(src):
    steps = [
        ("python block",  ANCHOR_PY,        PY_BLOCK + "\n" + ANCHOR_PY),
        ("snapshot return", ANCHOR_RETURN,  RETURN_NEW),
        ("css",           ANCHOR_CSS,       ANCHOR_CSS + "\n" + CSS_BLOCK.strip()),
        ("html header",   ANCHOR_HTML_TOP,  ANCHOR_HTML_TOP + HTML_TOP),
        ("html panels",   ANCHOR_HTML_MAIN, ANCHOR_HTML_MAIN + HTML_MAIN),
        ("js",            ANCHOR_JS,        JS_NEW),
    ]
    for name, anchor, repl in steps:
        n = src.count(anchor)
        if n != 1:
            raise SystemExit(f"! anchor for '{name}' found {n} times (expected 1). "
                             f"Your screener.py differs from the version this patch targets.")
        src = src.replace(anchor, repl, 1)
        print(f"  + {name}")
    return src


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "screener.py"
    if not os.path.exists(path):
        raise SystemExit(f"! {path} not found - run this next to your screener.py, or pass the path.")
    src = open(path, "r", encoding="utf-8").read()
    if MARK in src:
        print(f"{path} already has the indy panels - nothing to do.")
        return
    print(f"Patching {path} ...")
    out = patch(src)
    shutil.copyfile(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(out)
    compile(out, path, "exec")   # syntax check the result
    print(f"Done. Backup at {path}.bak - now run: python {path}")


if __name__ == "__main__":
    main()
