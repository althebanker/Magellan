#!/usr/bin/env python3
"""
terminal_upgrade.py - adds the live Terminal + Research tabs to screener.py.

Unlike the daily screen (baked at build time), these two tabs fetch on demand in
each visitor's browser through your Cloudflare Worker, so the page stays current
between builds and costs nothing to serve.

  Terminal  volatility terminal, top movers, commodities & energy futures,
            global benchmark indices, 3-month trend charts, SPDR sector
            breakdown, digital assets
  Research  symbol / company / theme search, then: valuation against the peer
            set, growth-profitability path, margin ladder, earnings quality,
            the underwater curve, seasonality grid

Usage:
    python terminal_upgrade.py                 # patches ./screener.py
    python terminal_upgrade.py path/to/screener.py

Safe to run before or after indy_upgrade.py. Idempotent.
"""

import os, shutil, sys

MARK = "MAGELLAN TERMINAL (client-side, via worker)"

# ============================================================================ CSS
CSS_BLOCK = r'''
/* ---- terminal + research ---- */
.mth{font-size:26px;font-weight:700;letter-spacing:.5px;margin:6px 0 2px}
.mtsub{color:var(--muted);font-size:13px;margin-bottom:16px}
.mtgrid{display:grid;gap:12px}
.mt3{grid-template-columns:2fr 1fr 1fr}
.mt2{grid-template-columns:1fr 1fr}
.mtcards{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
@media(max-width:900px){.mt3,.mt2{grid-template-columns:1fr}}
.mtc{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-top:12px}
.mtch{display:flex;align-items:baseline;gap:10px;color:var(--muted);font-size:10.5px;
letter-spacing:1.2px;text-transform:uppercase;margin-bottom:12px}
.mtch .r{margin-left:auto;color:var(--dim)}
.mtbig{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.mtnote{color:var(--dim);font-size:11.5px;line-height:1.6;margin:10px 2px 0}
.mtempty{color:var(--dim);font-size:12px;padding:14px;border:1px dashed var(--line);border-radius:10px;text-align:center}
.mtload{color:var(--dim);font-size:12px;padding:10px 0}
.mtt{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.mtt th{color:var(--dim);font-size:10px;letter-spacing:1px;text-transform:uppercase;
font-weight:600;text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
.mtt th:first-child{text-align:left}
.mtt td{padding:8px;font-size:13px;text-align:right;border-bottom:1px solid var(--line)}
.mtt td:first-child{text-align:left}
.mtt tr:last-child td{border-bottom:none}
.mtt tr:hover td{background:var(--panel2)}
.mtsym{color:var(--accent);cursor:pointer;font-weight:600}
.mtsym:hover{text-decoration:underline}
.mtmv{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.mtmv:last-child{border-bottom:none}
.mtbarrow{display:grid;grid-template-columns:150px 1fr 62px;gap:10px;align-items:center;
padding:4px 0;font-size:12.5px}
.mtbart{position:relative;height:15px;background:var(--panel2);border-radius:3px}
.mtbart>i{position:absolute;top:0;height:100%;border-radius:3px}
.mtbart>u{position:absolute;top:-2px;height:19px;width:1px;background:var(--dim);left:50%}
.mtsearch{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media(max-width:820px){.mtsearch{grid-template-columns:1fr}}
.mtin{width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:9px;
color:var(--ink);padding:11px 13px;font-size:14px}
.mtin:focus{outline:none;border-color:var(--accent)}
.mtchip{display:inline-block;background:var(--panel2);border:1px solid var(--line);color:var(--muted);
border-radius:7px;padding:5px 11px;margin:0 6px 6px 0;font-size:12px;cursor:pointer;font-weight:600}
.mtchip:hover{border-color:var(--accent);color:var(--ink)}
.mtres{border:1px solid var(--line);border-radius:10px;margin-top:10px;overflow:hidden}
.mtres div{display:flex;gap:10px;padding:9px 12px;font-size:13px;cursor:pointer;border-top:1px solid var(--line)}
.mtres div:first-child{border-top:none}
.mtres div:hover{background:var(--panel)}
.mtres .s{color:var(--accent);font-weight:700;min-width:82px}
.mtres .n{color:var(--ink);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mtres .e{color:var(--dim);font-size:11px}
.mtlbl{font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:var(--dim);margin:18px 0 8px}
.mthead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--line);
padding-bottom:12px;margin-bottom:4px}
.mthead .s{font-size:22px;font-weight:700}
.mthead .n{color:var(--muted);font-size:13px}
.mthead .p{margin-left:auto;font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.mtkv{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:12px}
.mtkv div{background:var(--panel);padding:9px 11px}
.mtkv .k{color:var(--dim);font-size:10px;letter-spacing:.5px;text-transform:uppercase}
.mtkv .v{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}
.mtseas{width:100%;border-collapse:separate;border-spacing:2px;font-variant-numeric:tabular-nums}
.mtseas th{color:var(--dim);font-size:9.5px;font-weight:600;padding:2px}
.mtseas td{font-size:9.5px;text-align:center;padding:4px 2px;border-radius:3px;color:var(--ink)}
.mtseas td.y{color:var(--dim);font-size:10px;text-align:right;padding-right:6px;background:none}
.mtseas td.tot{font-weight:700;background:none}
.mtsetup{border-left:3px solid var(--gold)}
.mtsetup code{background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:12px}
.mtlegend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:11px;margin-top:8px}
.mtlegend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
'''

# ============================================================================ HTML
HTML_TABS = ('<button data-tab="terminal">Terminal</button>\n'
             '<button data-tab="research">Research</button>')

HTML_SECTIONS = r'''

<section class="view" id="terminal">
<div id="mtSetupT"></div>
<div class="mth">Global markets &amp; commodities terminal</div>
<div class="mtsub">Live equity indices, volatility, commodity futures, sector performance and crypto -
fetched in your browser when you open this tab.</div>
<div class="mtgrid mt3">
 <div class="mtc" style="margin-top:0"><div class="mtch">Volatility terminal<span class="r">CBOE VIX 3-month trend</span></div><div id="mtVix"><div class="mtload">Loading...</div></div></div>
 <div class="mtc" style="margin-top:0"><div class="mtch">Top movers - gainers<span class="r">screen</span></div><div id="mtGain"></div></div>
 <div class="mtc" style="margin-top:0"><div class="mtch">Top movers - decliners<span class="r">screen</span></div><div id="mtLose"></div></div>
</div>
<div class="mtc"><div class="mtch">Commodities &amp; energy futures</div><div id="mtComm"><div class="mtload">Loading...</div></div></div>
<div class="mtc"><div class="mtch">Global equity benchmark indices</div><div id="mtIdx"><div class="mtload">Loading...</div></div></div>
<div class="mtlbl">3-month benchmark trend charts</div>
<div class="mtgrid mtcards" id="mtTrend"><div class="mtload">Loading...</div></div>
<div class="mtc"><div class="mtch">US SPDR sector breakdown<span class="r">day change</span></div><div id="mtSect"><div class="mtload">Loading...</div></div></div>
<div class="mtc"><div class="mtch">Digital assets &amp; crypto</div><div id="mtCrypto"><div class="mtload">Loading...</div></div></div>
<p class="mtnote">Every figure on this tab is pulled live from free public Yahoo Finance data through your
worker, cached at the edge for a few minutes. Best-effort and unaudited - not investment advice.</p>
</section>

<section class="view" id="research">
<div id="mtSetupR"></div>
<div class="mtc mtsetup" style="margin-top:0">
 <div class="mtch">Research terminal</div>
 <div class="mtsearch">
  <div>
   <input class="mtin" id="mtQ" placeholder="Symbol, company or theme..." autocomplete="off"/>
   <div id="mtQres"></div>
  </div>
  <div>
   <div class="mtlbl" style="margin-top:0">Jump straight in</div>
   <div id="mtQuick"></div>
   <div class="mtlbl">Or a theme</div>
   <div id="mtThemes"></div>
  </div>
 </div>
</div>
<div id="mtSym"></div>
</section>'''

# ============================================================================ JS
JS_BLOCK = r'''
/* ===================== MAGELLAN TERMINAL (client-side, via worker) ===================== */
const MT_LSW='magellan_worker_url';
function mtBase(){try{return ((localStorage.getItem(MT_LSW)||WORKER_URL||'').replace(/\/+$/,''));}catch(e){return (WORKER_URL||'');}}
const MTC={};
async function mtGet(path,params){
 const base=mtBase();
 if(!base)throw new Error('worker-not-set');
 const qs=new URLSearchParams(params||{}).toString();
 const key=path+'?'+qs;
 if(MTC[key])return MTC[key];
 const p=(async()=>{
  const r=await fetch(base+path+(qs?'?'+qs:''));
  const j=await r.json().catch(()=>null);
  if(!r.ok||!j||j.error)throw new Error((j&&j.error)||('HTTP '+r.status));
  return j;})();
 MTC[key]=p;
 try{return await p;}catch(e){delete MTC[key];throw e;}
}
function mtN(v,d){if(v==null||!isFinite(v))return '-';
 const dp=(d==null?(Math.abs(v)>=1000?0:2):d);
 return Number(v).toLocaleString('en-US',{minimumFractionDigits:dp,maximumFractionDigits:dp});}
function mtP(v,d){return (v==null||!isFinite(v))?'-':((v>=0?'+':'')+Number(v).toFixed(d==null?2:d)+'%');}
function mtCol(v){return (v==null||!isFinite(v))?'var(--muted)':(v>0.005?'var(--up)':v<-0.005?'var(--down)':'var(--muted)');}
function mtCap(v){if(v==null||!isFinite(v))return '-';
 if(Math.abs(v)>=1e12)return (v/1e12).toFixed(2)+'T';
 if(Math.abs(v)>=1e9)return (v/1e9).toFixed(1)+'B';
 if(Math.abs(v)>=1e6)return (v/1e6).toFixed(0)+'M';
 return mtN(v,0);}
function mtX(v){return (v==null||!isFinite(v))?'-':Number(v).toFixed(1)+'x';}
function mtCcy(c){return ({USD:'US$',EUR:'\u20ac',GBP:'\u00a3',JPY:'JP\u00a5',HKD:'HK$',CHF:'CHF ',
 CAD:'C$',AUD:'A$',CNY:'CN\u00a5',KRW:'\u20a9',INR:'\u20b9',TWD:'NT$',SEK:'kr ',DKK:'kr ',
 BRL:'R$',MXN:'MX$',ILS:'\u20aa',TRY:'\u20ba',SGD:'S$',IDR:'Rp'}[c]||'');}
function mtEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function mtMon(ts){return new Date(ts*1000).toLocaleDateString('en-US',{month:'short'});}

/* ---- charts ---- */
function mtLineChart(data,o){
 o=o||{};
 const c=(data&&data.c)||[],t=(data&&data.t)||[];
 if(c.length<2)return '<div class="mtempty">No price history available.</div>';
 const w=o.w||640,h=o.h||o.small?150:190,pl=6,pr=64,pt=12,pb=20;
 const mn=Math.min(...c),mx=Math.max(...c),r=(mx-mn)||1;
 const X=i=>pl+i*(w-pl-pr)/(c.length-1),Y=v=>h-pb-((v-mn)/r)*(h-pt-pb);
 const col=o.color||(c[c.length-1]>=c[0]?'var(--up)':'var(--down)');
 let g='';
 for(let k=0;k<=3;k++){const v=mn+r*k/3,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(w-pr)+'" y2="'+y+'" stroke="var(--line)" stroke-width="1"/>'
   +'<text x="'+(w-pr+6)+'" y="'+(+y+3).toFixed(1)+'" font-size="9" fill="var(--dim)">'+mtN(v,Math.abs(v)>=1000?0:2)+'</text>';}
 const path=c.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
 let xa='';const nT=4;
 for(let k=0;k<nT;k++){const i=Math.round(k*(c.length-1)/(nT-1));
  if(!t[i])continue;
  xa+='<text x="'+X(i).toFixed(1)+'" y="'+(h-4)+'" font-size="9" fill="var(--dim)" text-anchor="'+(k===0?'start':k===nT-1?'end':'middle')+'">'+mtMon(t[i])+'</text>';}
 const ly=Y(c[c.length-1]);
 const tag='<rect x="'+(w-pr+2)+'" y="'+(ly-8).toFixed(1)+'" width="56" height="16" rx="3" fill="'+col+'" opacity="0.85"/>'
   +'<text x="'+(w-pr+30)+'" y="'+(ly+3.5).toFixed(1)+'" font-size="9.5" fill="#08122b" text-anchor="middle" font-weight="700">'+mtN(c[c.length-1],2)+'</text>';
 return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:auto">'+g
  +'<path d="'+path+'" fill="none" stroke="'+col+'" stroke-width="1.8"/>'+xa+tag+'</svg>';}

function mtAreaChart(vals,o){
 o=o||{};
 if(!vals||vals.length<2)return '<div class="mtempty">No history available.</div>';
 const w=o.w||640,h=o.h||200,pl=34,pr=8,pt=10,pb=20;
 const mn=Math.min(...vals,0),mx=Math.max(...vals,0),r=(mx-mn)||1;
 const X=i=>pl+i*(w-pl-pr)/(vals.length-1),Y=v=>h-pb-((v-mn)/r)*(h-pt-pb);
 let g='';
 for(let k=0;k<=4;k++){const v=mn+r*k/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(w-pr)+'" y2="'+y+'" stroke="var(--line)" stroke-width="1"/>'
   +'<text x="'+(pl-5)+'" y="'+(+y+3).toFixed(1)+'" font-size="9" fill="var(--dim)" text-anchor="end">'+v.toFixed(0)+'%</text>';}
 const line=vals.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
 const area=line+' L '+X(vals.length-1).toFixed(1)+' '+Y(0).toFixed(1)+' L '+X(0).toFixed(1)+' '+Y(0).toFixed(1)+' Z';
 let xa='';
 (o.labels||[]).forEach(L=>{xa+='<text x="'+X(L.i).toFixed(1)+'" y="'+(h-4)+'" font-size="9" fill="var(--dim)" text-anchor="middle">'+L.t+'</text>';});
 return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:auto">'+g
  +'<path d="'+area+'" fill="rgba(255,93,108,.18)" stroke="none"/>'
  +'<path d="'+line+'" fill="none" stroke="var(--down)" stroke-width="1.3"/>'+xa+'</svg>';}

function mtMultiLine(sets,labels,o){
 o=o||{};
 const live=sets.filter(s=>s.v.some(x=>x!=null));
 if(!live.length)return '<div class="mtempty">No statement data.</div>';
 const all=[].concat(...live.map(s=>s.v.filter(x=>x!=null)));
 const w=o.w||620,h=o.h||210,pl=40,pr=10,pt=14,pb=22;
 let mn=Math.min(...all,0),mx=Math.max(...all);const r=(mx-mn)||1;
 const n=labels.length;
 const X=i=>pl+(n<2?(w-pl-pr)/2:i*(w-pl-pr)/(n-1)),Y=v=>h-pb-((v-mn)/r)*(h-pt-pb);
 let g='';
 for(let k=0;k<=4;k++){const v=mn+r*k/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(w-pr)+'" y2="'+y+'" stroke="var(--line)" stroke-width="1"/>'
   +'<text x="'+(pl-5)+'" y="'+(+y+3).toFixed(1)+'" font-size="9" fill="var(--dim)" text-anchor="end">'+v.toFixed(0)+'%</text>';}
 let paths='';
 live.forEach(s=>{
  let d='',started=false;
  s.v.forEach((v,i)=>{if(v==null)return;d+=(started?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)+' ';started=true;});
  paths+='<path d="'+d+'" fill="none" stroke="'+s.color+'" stroke-width="2"/>';
  s.v.forEach((v,i)=>{if(v!=null)paths+='<circle cx="'+X(i).toFixed(1)+'" cy="'+Y(v).toFixed(1)+'" r="2.6" fill="'+s.color+'"/>';});});
 let xa='';labels.forEach((L,i)=>{xa+='<text x="'+X(i).toFixed(1)+'" y="'+(h-5)+'" font-size="9" fill="var(--dim)" text-anchor="middle">'+L+'</text>';});
 const leg=live.map(s=>'<span><i style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
 return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:auto">'+g+paths+xa+'</svg>'
   +'<div class="mtlegend">'+leg+'</div>';}

function mtGroupBars(sets,labels){
 const all=[].concat(...sets.map(s=>s.v.filter(x=>x!=null)));
 if(!all.length)return '<div class="mtempty">No statement data.</div>';
 const w=620,h=210,pl=48,pr=10,pt=14,pb=22,n=labels.length,k=sets.length;
 const mx=Math.max(...all,0),mn=Math.min(...all,0),r=(mx-mn)||1;
 const gw=(w-pl-pr)/Math.max(n,1),bw=Math.min(24,(gw-8)/k);
 const Y=v=>h-pb-((v-mn)/r)*(h-pt-pb),z=Y(0);
 let g='',bars='';
 for(let i=0;i<=4;i++){const v=mn+r*i/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(w-pr)+'" y2="'+y+'" stroke="var(--line)" stroke-width="1"/>'
   +'<text x="'+(pl-5)+'" y="'+(+y+3).toFixed(1)+'" font-size="9" fill="var(--dim)" text-anchor="end">'+mtCap(v)+'</text>';}
 labels.forEach((L,i)=>{
  sets.forEach((s,j)=>{
   const v=s.v[i];if(v==null)return;
   const x=pl+i*gw+(gw-bw*k)/2+j*bw,y=Math.min(Y(v),z),bh=Math.max(2,Math.abs(z-Y(v)));
   bars+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+(bw-2).toFixed(1)+'" height="'+bh.toFixed(1)+'" rx="2" fill="'+s.color+'"><title>'+s.name+' '+L+': '+mtCap(v)+'</title></rect>';});
  bars+='<text x="'+(pl+i*gw+gw/2).toFixed(1)+'" y="'+(h-5)+'" font-size="9" fill="var(--dim)" text-anchor="middle">'+L+'</text>';});
 const leg=sets.map(s=>'<span><i style="background:'+s.color+'"></i>'+s.name+'</span>').join('');
 return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:auto">'+g+bars+'</svg><div class="mtlegend">'+leg+'</div>';}

/* ---- terminal tab ---- */
const MT_IDX=[['S&P 500','^GSPC'],['NASDAQ','^IXIC'],['DOW','^DJI'],['RUSSELL 2000','^RUT'],
 ['DAX','^GDAXI'],['FTSE 100','^FTSE'],['CAC 40','^FCHI'],['EURO STOXX 50','^STOXX50E'],
 ['IBEX 35','^IBEX'],['SMI','^SSMI'],['NIKKEI 225','^N225'],['HANG SENG','^HSI']];
const MT_TREND=['^GSPC','^GDAXI','^FTSE','^N225','^STOXX50E','^HSI'];
const MT_COMM=[['Gold','GC=F'],['Silver','SI=F'],['WTI Crude','CL=F'],['Brent Crude','BZ=F'],
 ['Natural Gas','NG=F'],['Copper','HG=F']];
const MT_SECT=[['Technology','XLK'],['Health Care','XLV'],['Financials','XLF'],
 ['Consumer Discretionary','XLY'],['Consumer Staples','XLP'],['Communication Services','XLC'],
 ['Energy','XLE'],['Industrials','XLI'],['Materials','XLB'],['Real Estate','XLRE'],['Utilities','XLU']];
const MT_CRYPTO=[['Bitcoin','BTC-USD'],['Ethereum','ETH-USD'],['Solana','SOL-USD'],
 ['XRP','XRP-USD'],['Cardano','ADA-USD'],['Dogecoin','DOGE-USD']];

function mtSetupCard(id){
 const el=document.getElementById(id);if(!el)return true;
 if(mtBase()){el.innerHTML='';return true;}
 el.innerHTML='<div class="mtc mtsetup" style="margin-top:0"><div class="mtch">One-time setup - connect your data worker</div>'
  +'<p style="font-size:13px;color:var(--muted);line-height:1.65;margin:0 0 12px">This tab reads live prices in your browser. '
  +'Browsers cannot call Yahoo Finance directly from a static page, so it goes through a free Cloudflare Worker. '
  +'Deploy <code>worker.js</code>, then paste the worker URL here (or set <code>WORKER_URL</code> in screener.py so every visitor gets it automatically).</p>'
  +'<div style="display:flex;gap:8px;flex-wrap:wrap"><input class="mtin" id="mtWin" style="flex:1;min-width:240px" placeholder="https://magellan-data.your-name.workers.dev"/>'
  +'<button class="btn" id="mtWsave">Save worker URL</button></div>'
  +'<div id="mtWmsg" class="mtnote"></div></div>';
 const btn=document.getElementById('mtWsave');
 btn.onclick=async()=>{
  const v=(document.getElementById('mtWin').value||'').trim().replace(/\/+$/,'');
  const msg=document.getElementById('mtWmsg');
  if(!/^https:\/\//.test(v)){msg.textContent='Enter the full https:// worker URL.';return;}
  msg.textContent='Testing '+v+' ...';
  try{
   const r=await fetch(v+'/chart?t=SPY&range=5d&interval=1d');
   const j=await r.json();
   if(!j||j.error||j.price==null)throw new Error((j&&j.error)||'no price returned');
   localStorage.setItem(MT_LSW,v);
   msg.textContent='Connected - SPY at '+mtN(j.price,2)+'. Loading...';
   setTimeout(()=>{mtRenderTerminal();mtRenderResearch();},400);
  }catch(e){msg.textContent='Could not reach the worker: '+e.message;}
 };
 return false;}

let MT_TERM_DONE=false;
async function mtRenderTerminal(){
 if(!mtSetupCard('mtSetupT'))return;
 mtMovers();
 if(MT_TERM_DONE)return;
 MT_TERM_DONE=true;
 const fail=(id,e)=>{const el=document.getElementById(id);
  if(el)el.innerHTML='<div class="mtempty">Unavailable right now ('+mtEsc(e.message||e)+').</div>';};

 mtGet('/chart',{t:'^VIX',range:'3mo',interval:'1d'}).then(v=>{
  const el=document.getElementById('mtVix');
  const lo=Math.min(...v.c),hi=Math.max(...v.c);
  el.innerHTML='<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">'
   +'<span class="mtbig">'+mtN(v.price,2)+'</span>'
   +'<span style="color:'+mtCol(v.chg)+';font-size:14px">'+mtP(v.chg)+' today</span>'
   +'<span style="margin-left:auto;color:var(--dim);font-size:11.5px">3M range '+mtN(lo,2)+' - '+mtN(hi,2)+'</span></div>'
   +'<div style="color:var(--dim);font-size:11.5px;margin:2px 0 8px">Option-implied volatility</div>'
   +mtLineChart(v,{color:'var(--down)'});
 }).catch(e=>fail('mtVix',e));

 mtGet('/quote',{t:MT_COMM.map(x=>x[1]).join(',')}).then(q=>{
  const m={};q.forEach(x=>m[x.sym]=x);
  document.getElementById('mtComm').innerHTML='<table class="mtt"><thead><tr><th>Commodity</th><th>Contract</th><th>Last price</th><th>Day change</th></tr></thead><tbody>'
   +MT_COMM.map(([l,s])=>{const d=m[s]||{};return '<tr><td>'+l+'</td><td style="text-align:right;color:var(--gold)">'+s+'</td>'
    +'<td>'+mtCcy(d.ccy)+mtN(d.price,2)+'</td><td style="color:'+mtCol(d.chg)+'">'+mtP(d.chg)+'</td></tr>';}).join('')
   +'</tbody></table>';
 }).catch(e=>fail('mtComm',e));

 mtGet('/quote',{t:MT_IDX.map(x=>x[1]).join(',')}).then(q=>{
  const m={};q.forEach(x=>m[x.sym]=x);
  document.getElementById('mtIdx').innerHTML='<table class="mtt"><thead><tr><th>Index</th><th>Symbol</th><th>Last close</th><th>Day change</th></tr></thead><tbody>'
   +MT_IDX.map(([l,s])=>{const d=m[s]||{};return '<tr><td>'+l+'</td>'
    +'<td style="text-align:right"><span class="mtsym" data-go="'+s+'">'+s+'</span></td>'
    +'<td>'+mtCcy(d.ccy)+mtN(d.price,2)+'</td><td style="color:'+mtCol(d.chg)+'">'+mtP(d.chg)+'</td></tr>';}).join('')
   +'</tbody></table>';
  mtWireSyms();
 }).catch(e=>fail('mtIdx',e));

 Promise.all(MT_TREND.map(s=>mtGet('/chart',{t:s,range:'3mo',interval:'1d'}).catch(()=>null))).then(rs=>{
  const el=document.getElementById('mtTrend');
  const ok=rs.filter(Boolean);
  el.innerHTML=ok.length?ok.map(v=>'<div class="mtc" style="margin-top:0"><div class="mtch">'+mtEsc(v.name||v.sym)
    +'<span class="r" style="color:'+mtCol(v.chg)+'">'+mtP(v.chg)+'</span></div>'
    +mtLineChart(v,{color:'var(--gold)',h:150})+'</div>').join('')
   :'<div class="mtempty">Trend charts unavailable right now.</div>';
 });

 mtGet('/quote',{t:MT_SECT.map(x=>x[1]).join(',')}).then(q=>{
  const m={};q.forEach(x=>m[x.sym]=x);
  const rows=MT_SECT.map(([l,s])=>({l,s,c:(m[s]||{}).chg})).filter(r=>r.c!=null).sort((a,b)=>b.c-a.c);
  if(!rows.length){document.getElementById('mtSect').innerHTML='<div class="mtempty">Sector data unavailable.</div>';return;}
  const mx=Math.max(...rows.map(r=>Math.abs(r.c)))||1;
  document.getElementById('mtSect').innerHTML=rows.map(r=>{
   const half=Math.abs(r.c)/mx*50;
   const pos=r.c>=0;
   return '<div class="mtbarrow"><span>'+r.l+' <span style="color:var(--dim);font-size:11px">'+r.s+'</span></span>'
    +'<span class="mtbart"><u></u><i style="'+(pos?'left:50%':'right:50%')+';width:'+half.toFixed(1)+'%;background:'+(pos?'var(--up)':'var(--down)')+';opacity:.75"></i></span>'
    +'<span style="text-align:right;color:'+mtCol(r.c)+'">'+mtP(r.c)+'</span></div>';}).join('');
 }).catch(e=>fail('mtSect',e));

 mtGet('/quote',{t:MT_CRYPTO.map(x=>x[1]).join(',')}).then(q=>{
  const m={};q.forEach(x=>m[x.sym]=x);
  document.getElementById('mtCrypto').innerHTML='<table class="mtt"><thead><tr><th>Asset</th><th>Symbol</th><th>Last price</th><th>24h change</th></tr></thead><tbody>'
   +MT_CRYPTO.map(([l,s])=>{const d=m[s]||{};return '<tr><td>'+l+'</td>'
    +'<td style="text-align:right"><span class="mtsym" data-go="'+s+'">'+s+'</span></td>'
    +'<td>US$'+mtN(d.price,2)+'</td><td style="color:'+mtCol(d.chg)+'">'+mtP(d.chg)+'</td></tr>';}).join('')
   +'</tbody></table>';
  mtWireSyms();
 }).catch(e=>fail('mtCrypto',e));
}

function mtMovers(){
 const all=[...DATA.up,...DATA.down].filter(s=>isFinite(s.chg));
 const g=all.filter(s=>s.chg>0).sort((a,b)=>b.chg-a.chg).slice(0,6);
 const l=all.filter(s=>s.chg<0).sort((a,b)=>a.chg-b.chg).slice(0,6);
 const row=s=>'<div class="mtmv"><span class="mtsym" data-go="'+s.sym+'">'+s.sym+'</span>'
   +'<span style="color:'+mtCol(s.chg)+'">'+mtP(s.chg)+'</span></div>';
 const G=document.getElementById('mtGain'),L=document.getElementById('mtLose');
 if(G)G.innerHTML=g.length?g.map(row).join(''):'<div class="mtempty">No gainers in today\u2019s screen.</div>';
 if(L)L.innerHTML=l.length?l.map(row).join(''):'<div class="mtempty">No decliners in today\u2019s screen.</div>';
 mtWireSyms();}

function mtWireSyms(){
 document.querySelectorAll('[data-go]').forEach(el=>{
  if(el._w)return;el._w=1;
  el.onclick=()=>mtOpenResearch(el.dataset.go);});}

/* ---- research tab ---- */
const MT_THEMES={
 bitcoin:['BTC-USD','BTC-EUR','IBIT','GBTC','MSTR','COIN','MARA','RIOT','CLSK','HUT'],
 ai:['NVDA','MSFT','GOOGL','AMD','AVGO','SMCI','PLTR','ORCL','META','TSM'],
 semiconductors:['TSM','NVDA','ASML.AS','AMD','INTC','AVGO','MU','LRCX','AMAT','QCOM'],
 gold:['GC=F','GLD','NEM','AEM','GOLD','WPM','FNV','KGC','AU','SGOL'],
 uranium:['CCJ','URA','NXE','DNN','UEC','LEU','URNM','PDN.AX','EU','UUUU'],
 rates:['^TNX','^FVX','^TYX','TLT','IEF','SHY','LQD','HYG','TIP','^IRX'],
 defence:['LMT','RTX','NOC','GD','BA','LHX','HII','RHM.DE','BA.L','SAAB-B.ST'],
 oil:['CL=F','BZ=F','XOM','CVX','SHEL','BP','TTE','COP','SLB','OXY']};

let MT_RES_INIT=false;
function mtRenderResearch(){
 if(!mtSetupCard('mtSetupR'))return;
 if(MT_RES_INIT)return;
 MT_RES_INIT=true;
 const quick=[...new Set([...DATA.up,...DATA.down].map(s=>s.sym))].slice(0,7);
 const qk=quick.length?quick:['AAPL','MSFT','NVDA','TSLA','ASML.AS','MAERSK-B.CO','6501.T'];
 document.getElementById('mtQuick').innerHTML=qk.map(s=>'<span class="mtchip" data-go="'+s+'">'+s+'</span>').join('');
 document.getElementById('mtThemes').innerHTML=Object.keys(MT_THEMES)
   .map(k=>'<span class="mtchip" data-theme="'+k+'">'+k.toUpperCase()+'</span>').join('');
 document.querySelectorAll('[data-theme]').forEach(el=>el.onclick=()=>mtShowTheme(el.dataset.theme));
 mtWireSyms();
 const inp=document.getElementById('mtQ');
 let tmr=null;
 inp.oninput=()=>{clearTimeout(tmr);const v=inp.value.trim();
  if(v.length<2){document.getElementById('mtQres').innerHTML='';return;}
  if(MT_THEMES[v.toLowerCase()]){mtShowTheme(v.toLowerCase());return;}
  tmr=setTimeout(async()=>{
   try{
    const rs=await mtGet('/search',{q:v});
    document.getElementById('mtQres').innerHTML=rs.length?'<div class="mtres">'
      +rs.slice(0,8).map(r=>'<div data-go="'+mtEsc(r.sym)+'"><span class="s">'+mtEsc(r.sym)+'</span>'
        +'<span class="n">'+mtEsc(r.name)+'</span><span class="e">'+mtEsc(r.exch)+'</span></div>').join('')+'</div>'
      :'<p class="mtnote">Nothing matched that. Try a ticker, or a theme like bitcoin or uranium.</p>';
    mtWireSyms();
   }catch(e){document.getElementById('mtQres').innerHTML='<p class="mtnote">Search unavailable ('+mtEsc(e.message)+').</p>';}
  },320);};
 inp.onkeydown=e=>{if(e.key==='Enter'&&inp.value.trim())mtOpenResearch(inp.value.trim().toUpperCase());};
}

async function mtShowTheme(name){
 const syms=MT_THEMES[name]||[];
 const box=document.getElementById('mtQres');
 box.innerHTML='<p class="mtnote">Loading '+name+'...</p>';
 try{
  const q=await mtGet('/quote',{t:syms.join(',')});
  box.innerHTML='<div class="mtres">'+q.filter(x=>!x.error).map(x=>'<div data-go="'+mtEsc(x.sym)+'">'
    +'<span class="s">'+mtEsc(x.sym)+'</span><span class="n">'+mtEsc(x.name||'')+'</span>'
    +'<span class="e" style="color:'+mtCol(x.chg)+'">'+mtP(x.chg)+'</span></div>').join('')+'</div>'
    +'<p class="mtnote">Themes resolve to the instruments they actually cover - spot, wrappers, proxies and the operators levered to them.</p>';
  mtWireSyms();
 }catch(e){box.innerHTML='<p class="mtnote">Theme unavailable ('+mtEsc(e.message)+').</p>';}}

function mtOpenResearch(sym){
 document.querySelectorAll('nav.tabs button').forEach(x=>x.classList.remove('active'));
 const tb=document.querySelector('nav.tabs button[data-tab="research"]');
 if(tb)tb.classList.add('active');
 document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
 const rv=document.getElementById('research');
 if(rv)rv.classList.add('active');
 mtRenderResearch();
 mtLoadSymbol(sym);
 window.scrollTo({top:0,behavior:'smooth'});}

function mtPanel(id,title,desc){
 return '<div class="mtc"><div class="mtch">'+title+'</div>'
   +(desc?'<p style="color:var(--muted);font-size:12.5px;margin:-6px 0 12px">'+desc+'</p>':'')
   +'<div id="'+id+'"><div class="mtload">Loading...</div></div></div>';}

async function mtLoadSymbol(sym){
 sym=String(sym||'').trim();if(!sym)return;
 const host=document.getElementById('mtSym');
 host.innerHTML='<div class="mtc"><div class="mtload">Loading '+mtEsc(sym)+'...</div></div>';
 let st={};
 try{st=await mtGet('/stats',{t:sym});}catch(e){
  host.innerHTML='<div class="mtc"><div class="mtempty">Could not load '+mtEsc(sym)+' ('+mtEsc(e.message)+').</div></div>';return;}
 host.innerHTML='<div class="mtc"><div class="mthead"><span class="s">'+mtEsc(st.sym||sym)+'</span>'
  +'<span class="n">'+mtEsc(st.name||'')+(st.sector?' \u00b7 '+mtEsc(st.sector):'')+'</span>'
  +'<span class="p">'+mtCcy(st.ccy)+mtN(st.price,2)+' <span style="font-size:14px;color:'+mtCol(st.chg)+'">'+mtP(st.chg)+'</span></span></div>'
  +'<div class="mtkv">'
   +[['Market cap',mtCap(st.mcap)],['P/E',mtX(st.pe)],['Fwd P/E',mtX(st.fpe)],['EV/EBITDA',mtX(st.evEbitda)],
     ['P/S',mtX(st.ps)],['ROE',mtP(st.roe,1)],['Gross margin',mtP(st.grossMargin,1)],['Net margin',mtP(st.netMargin,1)],
     ['Rev growth',mtP(st.revGrowth,1)],['Beta',st.beta==null?'-':mtN(st.beta,2)],
     ['52w high',mtN(st.hi52,2)],['52w low',mtN(st.lo52,2)]]
    .map(kv=>'<div><div class="k">'+kv[0]+'</div><div class="v">'+kv[1]+'</div></div>').join('')
  +'</div></div>'
  +'<div class="mtgrid mt2">'
   +mtPanel('mtPeer','Valuation against the peer set','Every multiple inside the range its real comparables trade at, peer median marked.')
   +mtPanel('mtPath','Growth-profitability path','Revenue growth across, operating margin up. Each fiscal year a point, connected in time order.')
   +mtPanel('mtMarg','Margin ladder','What survives each layer of cost, on one scale, year by year.')
   +mtPanel('mtEq','Earnings quality','Reported profit beside the cash that actually arrived.')
   +mtPanel('mtUw','The underwater curve','How far below its last high the price has been, every day of a decade.')
   +mtPanel('mtSeas','Seasonality grid','Calendar months by year, with the cross-year average underneath.')
  +'</div>'
  +'<p class="mtnote">Live figures, computed in your browser from free public data. Every panel works for any listed symbol.</p>';

 const fail=(id,e)=>{const el=document.getElementById(id);
  if(el)el.innerHTML='<div class="mtempty">Unavailable for this symbol ('+mtEsc(e.message||e)+').</div>';};

 mtPeerPanel(sym,st).catch(e=>fail('mtPeer',e));
 mtGet('/fund',{t:sym}).then(f=>{
  try{mtPathPanel(f);}catch(e){fail('mtPath',e);}
  try{mtMarginPanel(f);}catch(e){fail('mtMarg',e);}
  try{mtEqPanel(f);}catch(e){fail('mtEq',e);}
 }).catch(e=>{fail('mtPath',e);fail('mtMarg',e);fail('mtEq',e);});
 mtGet('/chart',{t:sym,range:'10y',interval:'1d'}).then(mtUwPanel).catch(e=>fail('mtUw',e));
 mtGet('/chart',{t:sym,range:'10y',interval:'1mo'}).then(mtSeasPanel).catch(e=>fail('mtSeas',e));
}

async function mtPeerPanel(sym,st){
 let peers=[];
 try{peers=await mtGet('/peers',{t:sym});}catch(e){peers=[];}
 if(!peers.length){
  const me=[...DATA.up,...DATA.down].find(s=>s.sym===sym);
  if(me&&me.sector)peers=[...DATA.up,...DATA.down].filter(s=>s.sector===me.sector&&s.sym!==sym).slice(0,5).map(s=>s.sym);}
 peers=peers.filter(p=>p&&p!==sym).slice(0,5);
 const el=document.getElementById('mtPeer');
 if(!peers.length){el.innerHTML='<div class="mtempty">No comparable set found for this symbol.</div>';return;}
 const ps=(await Promise.all(peers.map(p=>mtGet('/stats',{t:p}).catch(()=>null)))).filter(Boolean);
 const metrics=[['P/E','pe'],['Fwd P/E','fpe'],['EV/EBITDA','evEbitda'],['P/S','ps']];
 const rows=metrics.map(([label,key])=>{
  const mine=st[key];
  const vals=ps.map(p=>p[key]).filter(v=>v!=null&&isFinite(v)&&v>0&&v<300).sort((a,b)=>a-b);
  if(!vals.length||mine==null||!isFinite(mine))
   return '<div class="mtbarrow"><span>'+label+'</span><span class="mtbart"></span><span style="text-align:right;color:var(--dim)">'+mtX(mine)+'</span></div>';
  const lo=Math.min(vals[0],mine),hi=Math.max(vals[vals.length-1],mine),r=(hi-lo)||1;
  const med=vals[Math.floor(vals.length/2)];
  const pos=v=>((v-lo)/r*100).toFixed(1);
  const cheap=mine<med;
  return '<div class="mtbarrow"><span>'+label+'</span>'
   +'<span class="mtbart"><i style="left:0;width:100%;background:var(--panel2)"></i>'
    +'<u style="left:'+pos(med)+'%"></u>'
    +'<i style="left:calc('+pos(mine)+'% - 5px);width:10px;height:10px;top:2.5px;border-radius:50%;background:'
      +(cheap?'var(--up)':'var(--gold)')+'"></i></span>'
   +'<span style="text-align:right"><b>'+mtX(mine)+'</b> <span style="color:var(--dim)">/ '+mtX(med)+'</span></span></div>';});
 el.innerHTML=rows.join('')
  +'<div class="mtlegend"><span><i style="background:var(--gold);border-radius:50%"></i>'+mtEsc(sym)+'</span>'
  +'<span><i style="background:var(--dim)"></i>peer median</span>'
  +'<span>peers: '+ps.map(p=>mtEsc(p.sym)).join(', ')+'</span></div>';}

function mtFy(rows){return (rows||[]).map(r=>({y:String(r.d).slice(0,4),v:r.v}));}
function mtAlign(f,keys){
 const years=[...new Set([].concat(...keys.map(k=>mtFy(f.series[k]).map(r=>r.y))))].sort().slice(-6);
 const out={years};
 keys.forEach(k=>{const m={};mtFy(f.series[k]).forEach(r=>m[r.y]=r.v);
  out[k]=years.map(y=>m[y]==null?null:m[y]);});
 return out;}

function mtPathPanel(f){
 const a=mtAlign(f,['annualTotalRevenue','annualOperatingIncome']);
 const rev=a.annualTotalRevenue,oi=a.annualOperatingIncome;
 const pts=[];
 for(let i=1;i<a.years.length;i++){
  if(rev[i]==null||rev[i-1]==null||!rev[i-1]||oi[i]==null)continue;
  pts.push({y:a.years[i],g:(rev[i]/rev[i-1]-1)*100,m:oi[i]/rev[i]*100});}
 const el=document.getElementById('mtPath');
 if(pts.length<2){el.innerHTML='<div class="mtempty">Not enough annual history to plot the path.</div>';return;}
 const w=620,h=210,pl=42,pr=14,pt=18,pb=24;
 const gs=pts.map(p=>p.g),ms=pts.map(p=>p.m);
 let gmn=Math.min(...gs,0),gmx=Math.max(...gs,0),mmn=Math.min(...ms,0),mmx=Math.max(...ms,0);
 const gp=(gmx-gmn)*0.15||5,mp=(mmx-mmn)*0.15||5;
 gmn-=gp;gmx+=gp;mmn-=mp;mmx+=mp;
 const X=v=>pl+(v-gmn)/((gmx-gmn)||1)*(w-pl-pr),Y=v=>h-pb-(v-mmn)/((mmx-mmn)||1)*(h-pt-pb);
 let g='';
 for(let k=0;k<=3;k++){const v=mmn+(mmx-mmn)*k/3,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(w-pr)+'" y2="'+y+'" stroke="var(--line)"/>'
   +'<text x="'+(pl-5)+'" y="'+(+y+3).toFixed(1)+'" font-size="9" fill="var(--dim)" text-anchor="end">'+v.toFixed(0)+'%</text>';}
 for(let k=0;k<=3;k++){const v=gmn+(gmx-gmn)*k/3,x=X(v).toFixed(1);
  g+='<text x="'+x+'" y="'+(h-6)+'" font-size="9" fill="var(--dim)" text-anchor="middle">'+v.toFixed(0)+'%</text>';}
 const path=pts.map((p,i)=>(i?'L':'M')+X(p.g).toFixed(1)+' '+Y(p.m).toFixed(1)).join(' ');
 const dots=pts.map((p,i)=>'<circle cx="'+X(p.g).toFixed(1)+'" cy="'+Y(p.m).toFixed(1)+'" r="'+(i===pts.length-1?5:4)+'" fill="var(--accent)"/>'
  +'<text x="'+X(p.g).toFixed(1)+'" y="'+(Y(p.m)-9).toFixed(1)+'" font-size="9" fill="var(--muted)" text-anchor="middle">FY'+p.y.slice(2)+'</text>').join('');
 el.innerHTML='<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;height:auto">'+g
  +'<path d="'+path+'" fill="none" stroke="var(--dim)" stroke-width="1.5"/>'+dots+'</svg>'
  +'<div class="mtlegend"><span>x: revenue growth</span><span>y: operating margin</span></div>';}

function mtMarginPanel(f){
 const a=mtAlign(f,['annualTotalRevenue','annualGrossProfit','annualOperatingIncome','annualNetIncome']);
 const pct=(n)=>a.years.map((y,i)=>{const r=a.annualTotalRevenue[i],v=a[n][i];
  return (r&&v!=null)?v/r*100:null;});
 document.getElementById('mtMarg').innerHTML=mtMultiLine([
  {name:'Gross',v:pct('annualGrossProfit'),color:'var(--accent)'},
  {name:'Operating',v:pct('annualOperatingIncome'),color:'var(--gold)'},
  {name:'Net',v:pct('annualNetIncome'),color:'var(--up)'}],
  a.years.map(y=>'FY'+y.slice(2)));}

function mtEqPanel(f){
 const a=mtAlign(f,['annualNetIncome','annualOperatingCashFlow']);
 document.getElementById('mtEq').innerHTML=mtGroupBars([
  {name:'Net income',v:a.annualNetIncome,color:'var(--accent)'},
  {name:'Operating cash flow',v:a.annualOperatingCashFlow,color:'var(--gold)'}],
  a.years.map(y=>'FY'+y.slice(2)));}

function mtUwPanel(ch){
 const c=ch.c||[],t=ch.t||[];
 const el=document.getElementById('mtUw');
 if(c.length<30){el.innerHTML='<div class="mtempty">Not enough price history.</div>';return;}
 let peak=-Infinity;const dd=[];
 for(let i=0;i<c.length;i++){if(c[i]>peak)peak=c[i];dd.push((c[i]/peak-1)*100);}
 const N=520;let idx=dd.map((_,i)=>i);
 if(dd.length>N){const step=dd.length/N;idx=Array.from({length:N},(_,i)=>Math.min(dd.length-1,Math.floor(i*step)));}
 const vals=idx.map(i=>+dd[i].toFixed(2));
 const labels=[0,Math.floor(N/3),Math.floor(2*N/3),vals.length-1].map(i=>({i:Math.min(i,vals.length-1),
   t:new Date(t[idx[Math.min(i,idx.length-1)]]*1000).toLocaleDateString('en-US',{month:'short',year:'2-digit'})}));
 const worst=Math.min(...dd);
 el.innerHTML=mtAreaChart(vals,{labels})
  +'<div class="mtlegend"><span>worst drawdown '+worst.toFixed(1)+'%</span><span>now '+dd[dd.length-1].toFixed(1)+'%</span></div>';}

function mtSeasPanel(ch){
 const c=ch.c||[],t=ch.t||[];
 const el=document.getElementById('mtSeas');
 if(c.length<14){el.innerHTML='<div class="mtempty">Not enough monthly history.</div>';return;}
 const byYear={};
 for(let i=1;i<c.length;i++){
  if(!c[i-1])continue;
  const d=new Date(t[i]*1000),y=d.getUTCFullYear(),m=d.getUTCMonth();
  (byYear[y]=byYear[y]||{})[m]=(c[i]/c[i-1]-1)*100;}
 const years=Object.keys(byYear).sort().slice(-8);
 const M=['J','F','M','A','M','J','J','A','S','O','N','D'];
 const cell=v=>{
  if(v==null)return '<td style="background:var(--panel2);color:var(--dim)">\u00b7</td>';
  const a=Math.min(1,Math.abs(v)/12);
  const bg=v>=0?'rgba(63,224,138,'+(0.12+0.55*a).toFixed(2)+')':'rgba(255,93,108,'+(0.12+0.55*a).toFixed(2)+')';
  return '<td style="background:'+bg+'" title="'+v.toFixed(1)+'%">'+v.toFixed(0)+'</td>';};
 const avg=[];
 for(let m=0;m<12;m++){const vs=years.map(y=>byYear[y][m]).filter(v=>v!=null);
  avg.push(vs.length?vs.reduce((a,b)=>a+b,0)/vs.length:null);}
 const tot=y=>{const vs=Object.values(byYear[y]).filter(v=>v!=null);
  return vs.length?vs.reduce((a,b)=>a+b,0):null;};
 el.innerHTML='<table class="mtseas"><thead><tr><th></th>'+M.map(m=>'<th>'+m+'</th>').join('')+'<th>YR</th></tr></thead><tbody>'
  +years.map(y=>'<tr><td class="y">'+y+'</td>'+M.map((_,m)=>cell(byYear[y][m])).join('')
    +'<td class="tot" style="color:'+mtCol(tot(y))+'">'+(tot(y)==null?'-':tot(y).toFixed(0))+'</td></tr>').join('')
  +'<tr><td class="y">AVG</td>'+avg.map(cell).join('')+'<td class="tot"></td></tr>'
  +'</tbody></table><div class="mtlegend"><span>monthly % change, green positive</span><span>YR = sum of the year\u2019s months</span></div>';}
'''

# ============================================================================ ANCHORS
ANCHOR_TABS = '<button data-tab="screen">Screen</button>'
ANCHOR_SECTIONS = '<div id="histBody"></div>\n</section>'
ANCHOR_CSS = '.disc .ok:hover{filter:brightness(1.08)}'
ANCHOR_TABJS = " if(b.dataset.tab==='history') renderHistory();});"
TABJS_NEW = (" if(b.dataset.tab==='history') renderHistory();\n"
             " if(b.dataset.tab==='terminal') mtRenderTerminal();\n"
             " if(b.dataset.tab==='research') mtRenderResearch();});")
ANCHOR_END = "</script></body></html>"


def patch(src):
    steps = [
        ("nav tabs",      ANCHOR_TABS,     ANCHOR_TABS + "\n" + HTML_TABS),
        ("sections",      ANCHOR_SECTIONS, ANCHOR_SECTIONS + HTML_SECTIONS),
        ("css",           ANCHOR_CSS,      ANCHOR_CSS + "\n" + CSS_BLOCK.strip()),
        ("tab switch",    ANCHOR_TABJS,    TABJS_NEW),
        ("js",            ANCHOR_END,      JS_BLOCK + "\n" + ANCHOR_END),
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
        raise SystemExit(f"! {path} not found - run this next to screener.py, or pass the path.")
    src = open(path, "r", encoding="utf-8").read()
    if MARK in src:
        print(f"{path} already has the Terminal tabs - nothing to do.")
        return
    print(f"Patching {path} ...")
    out = patch(src)
    shutil.copyfile(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(out)
    compile(out, path, "exec")
    print(f"Done. Backup at {path}.bak")
    print("Next: deploy worker.js, then set CONFIG['WORKER_URL'] in screener.py to the worker URL.")


if __name__ == "__main__":
    main()
