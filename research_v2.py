#!/usr/bin/env python3
"""
research_v2.py - upgrades the Research tab into a full symbol terminal, adds a
News tab, and removes the Magellan - Portfolio tab button.

Requires terminal_upgrade.py to have been applied first (it reuses mtGet, the
worker plumbing and the formatting helpers, and overrides the two research
functions by redefining them).

Research sections, all computed in the browser from worker data:
  CHART        price navigator: 1M/3M/6M/YTD/1Y/5Y/10Y/MAX, price or rebased,
               compare against an index or a peer, crosshair readout,
               window return / annualised / high / low / max drawdown
  VS PEERS     every line rebased to 100, plus a window-return table
  VALUATION    each multiple on the peer range with the median ticked
  VALUE MAP    P/E against revenue growth, bubble area by market cap
  QUARTERS     the reporting record, one computed line per quarter + table
  SCALE        revenue and net income by fiscal year, recent quarters beside it
  MARGINS      gross / EBITDA / operating / net on one scale
  TRAJECTORY   growth against margin with the Rule of 40 diagonal
  CASH QUALITY net income against operating and free cash flow
  PER SHARE    revenue, revenue/share, FCF/share against the share count
  REINVESTMENT capex, R&D and SG&A as a share of revenue
  BALANCE      cash against debt, and net debt in years of EBITDA
  STREET       price targets, rating mix, EPS surprise history
  RISK         return distribution, realised vol, underwater curve + episodes
  SEASONALITY  calendar-month returns by year with the cross-year average
  ABOUT        what the company actually does

News sections: LIVE WIRE (merged, links straight out), REPORTS (with summaries),
TOPICS (keyword filter), DAILY WRAP (computed from the baked market snapshot).

Usage:
    python research_v2.py [path/to/screener.py]
"""

import os, shutil, sys

MARK = "MAGELLAN RESEARCH TERMINAL v2"

# ============================================================================ CSS
CSS_BLOCK = r'''
/* ---- research terminal v2 + news ---- */
.rvnav{position:sticky;top:0;z-index:6;background:var(--bg);border-bottom:1px solid var(--line);
display:flex;gap:2px;overflow-x:auto;padding:8px 0;margin-bottom:4px;-webkit-overflow-scrolling:touch}
.rvnav button{background:none;border:0;color:var(--dim);font-size:10.5px;letter-spacing:1.1px;
text-transform:uppercase;padding:5px 10px;white-space:nowrap;cursor:pointer;border-radius:6px}
.rvnav button:hover{color:var(--ink);background:var(--panel)}
.rvnav button.on{color:var(--gold);background:var(--panel)}
.rvcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 17px;margin-top:12px;scroll-margin-top:56px}
.rvhd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:10.5px;
letter-spacing:1.2px;text-transform:uppercase;margin-bottom:9px}
.rvhd .r{margin-left:auto;color:var(--gold);letter-spacing:.5px}
.rvdesc{color:var(--muted);font-size:12.5px;line-height:1.6;margin:0 0 13px}
.rvfoot{color:var(--dim);font-size:11px;line-height:1.6;margin:10px 0 0}
.rvwrap{position:relative}
.rvwrap svg{display:block;width:100%;height:auto;touch-action:pan-y}
.rvtip{position:absolute;pointer-events:none;background:var(--panel2);border:1px solid var(--line);
border-radius:7px;padding:7px 10px;font-size:11px;font-variant-numeric:tabular-nums;
opacity:0;transition:opacity .1s;z-index:4;min-width:120px;box-shadow:0 6px 20px rgba(0,0,0,.45)}
.rvtip.on{opacity:1}
.rvtip .d{color:var(--ink);font-weight:700;margin-bottom:4px}
.rvtip .r{display:flex;justify-content:space-between;gap:14px;color:var(--muted)}
.rvtip .r b{color:var(--ink);font-weight:600}
.rvseg{display:inline-flex;gap:2px;background:var(--panel2);border:1px solid var(--line);
border-radius:8px;padding:3px;flex-wrap:wrap}
.rvseg button{background:none;border:0;color:var(--muted);font-size:11px;padding:4px 10px;
border-radius:5px;cursor:pointer;font-variant-numeric:tabular-nums}
.rvseg button.on{background:var(--gold);color:#08122b;font-weight:700}
.rvbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.rvsel{background:var(--panel2);border:1px solid var(--line);color:var(--ink);border-radius:7px;
padding:5px 9px;font-size:12px}
.rvstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:1px;
background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:12px}
.rvstats div{background:var(--panel2);padding:9px 11px}
.rvstats .k{color:var(--dim);font-size:9.5px;letter-spacing:.6px;text-transform:uppercase}
.rvstats .v{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:2px}
.rvleg{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:9px}
.rvleg i{display:inline-block;width:11px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
.rvt{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-top:10px}
.rvt th{color:var(--dim);font-size:9.5px;letter-spacing:1px;text-transform:uppercase;font-weight:600;
text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
.rvt th:first-child{text-align:left}
.rvt td{padding:7px 8px;font-size:12.5px;text-align:right;border-bottom:1px solid var(--panel2)}
.rvt td:first-child{text-align:left}
.rvt tr.me td{background:var(--panel2)}
.rvt tr.me td:first-child{color:var(--gold);font-weight:700}
.rvq{border-left:2px solid var(--line);padding:8px 0 8px 13px;margin-bottom:10px}
.rvq .h{display:flex;gap:10px;align-items:baseline}
.rvq .h b{font-size:12.5px}
.rvq .h span{margin-left:auto;font-variant-numeric:tabular-nums;font-size:12px}
.rvq p{margin:3px 0 0;color:var(--muted);font-size:12.5px;line-height:1.55}
.rvtrack{display:grid;grid-template-columns:118px 1fr 120px;gap:11px;align-items:center;padding:5px 0;font-size:12.5px}
.rvtrack .tk{position:relative;height:16px}
.rvtrack .tk .ln{position:absolute;top:7px;left:0;right:0;height:3px;background:var(--panel2);border-radius:2px}
.rvtrack .tk .md{position:absolute;top:2px;width:2px;height:13px;background:var(--dim)}
.rvtrack .tk .dot{position:absolute;top:3px;width:11px;height:11px;border-radius:50%;margin-left:-5px}
.rvtrack .vv{text-align:right}
.rvtrack .vv b{font-size:13px}
.rvtrack .vv span{color:var(--dim)}
.rvabout{color:var(--muted);font-size:13px;line-height:1.75}
.rvchips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.rvchip{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:3px 9px;
font-size:11px;color:var(--muted)}
.rvseas{width:100%;border-collapse:separate;border-spacing:2px;font-variant-numeric:tabular-nums}
.rvseas th{color:var(--dim);font-size:9.5px;font-weight:600;padding:2px}
.rvseas td{font-size:10px;text-align:center;padding:5px 2px;border-radius:3px}
.rvseas td.y{color:var(--dim);text-align:right;padding-right:7px}
.rvseas td.tot{font-weight:700}
/* news */
.nwtabs{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:6px}
.nwtabs button{background:none;border:0;color:var(--dim);font-size:11px;letter-spacing:1.2px;
text-transform:uppercase;padding:6px 12px;cursor:pointer;border-radius:6px}
.nwtabs button.on{color:var(--gold);background:var(--panel)}
.nwrow{display:flex;gap:12px;align-items:baseline;padding:11px 0;border-bottom:1px solid var(--line);text-decoration:none}
.nwrow:hover{background:var(--panel)}
.nwrow .ag{color:var(--dim);font-size:10.5px;min-width:34px;font-variant-numeric:tabular-nums}
.nwrow .src{color:var(--accent);font-size:10px;font-weight:700;min-width:38px;letter-spacing:.5px}
.nwrow .bd{flex:1}
.nwrow .hd{color:var(--ink);font-size:13.5px;line-height:1.45}
.nwrow:hover .hd{color:var(--blue)}
.nwrow .sm{color:var(--dim);font-size:12px;line-height:1.5;margin-top:3px}
.nwfilt{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px}
.nwfilt button{background:var(--panel2);border:1px solid var(--line);color:var(--muted);
border-radius:7px;padding:4px 11px;font-size:11.5px;cursor:pointer}
.nwfilt button.on{border-color:var(--accent);color:var(--ink)}
'''

# ============================================================================ HTML
HTML_NEWS_TAB = '<button data-tab="news">News</button>'
HTML_NEWS_SECTION = r'''

<section class="view" id="news">
<div id="mtSetupN"></div>
<div class="mth">INDY-style newsroom</div>
<div class="mtsub">Public wires, merged and de-duplicated in your browser. Every headline links to the
original publisher - nothing here is rewritten.</div>
<div class="nwtabs" id="nwTabs"></div>
<div id="nwFilt"></div>
<div id="nwBody"><div class="mtload">Loading the wire...</div></div>
</section>'''

# ============================================================================ JS
JS_BLOCK = r'''
/* ===================== MAGELLAN RESEARCH TERMINAL v2 ===================== */
const RV_SECTIONS=[['chart','Chart'],['peers','Vs peers'],['valuation','Valuation'],
 ['valuemap','Value map'],['quarters','Quarters'],['scale','Scale'],['margins','Margins'],
 ['traj','Trajectory'],['cash','Cash quality'],['pershare','Per share'],
 ['reinv','Reinvestment'],['balance','Balance sheet'],['street','Street'],['risk','Risk'],
 ['seas','Seasonality'],['about','About']];
const RV_RANGES=[['1M','1mo','1d'],['3M','3mo','1d'],['6M','6mo','1d'],['YTD','ytd','1d'],
 ['1Y','1y','1d'],['5Y','5y','1wk'],['10Y','10y','1mo'],['MAX','max','1mo']];
const RV_BENCH=[['none','No comparison'],['^GSPC','S&P 500'],['^IXIC','Nasdaq Composite'],
 ['^STOXX50E','Euro Stoxx 50'],['^N225','Nikkei 225']];
const RV_COLORS=['var(--gold)','var(--accent)','var(--up)','var(--down)','#c48cff','#4ec9d4'];
let RV={sym:null,st:null,range:'1Y',mode:'rebased',cmp:'^GSPC',peers:[],pstats:[],fundA:null,fundQ:null};

/* ---- interactive line plot with crosshair ---- */
function rvPlot(el,series,o){
 o=o||{};
 const live=series.filter(s=>s.pts&&s.pts.length>1);
 if(!live.length){el.innerHTML='<div class="mtempty">No data for this window.</div>';return;}
 const W=o.w||900,H=o.h||300,pl=o.pl||52,pr=14,pt=14,pb=26;
 const n=Math.max(...live.map(s=>s.pts.length));
 const all=[].concat(...live.map(s=>s.pts.map(p=>p.v))).filter(v=>v!=null&&isFinite(v));
 if(!all.length){el.innerHTML='<div class="mtempty">No data for this window.</div>';return;}
 let mn=Math.min(...all),mx=Math.max(...all);
 if(o.zero)mn=Math.min(mn,0);
 const padv=(mx-mn)*0.08||1;mn-=padv;mx+=padv;
 const r=(mx-mn)||1;
 const X=i=>pl+(n<2?0:i*(W-pl-pr)/(n-1)),Y=v=>H-pb-((v-mn)/r)*(H-pt-pb);
 let g='';
 for(let k=0;k<=4;k++){const v=mn+r*k/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="var(--line)" stroke-width="1"/>'
   +'<text x="'+(pl-6)+'" y="'+(+y+3.5).toFixed(1)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">'
   +(o.fmtY?o.fmtY(v):mtN(v,Math.abs(v)>=1000?0:1))+'</text>';}
 if(o.baseline!=null&&o.baseline>=mn&&o.baseline<=mx)
  g+='<line x1="'+pl+'" y1="'+Y(o.baseline).toFixed(1)+'" x2="'+(W-pr)+'" y2="'+Y(o.baseline).toFixed(1)
    +'" stroke="var(--gold)" stroke-dasharray="4 3" stroke-width="1" opacity=".5"/>';
 let paths='';
 live.forEach(s=>{
  let d='',open=false;
  s.pts.forEach((p,i)=>{if(p.v==null||!isFinite(p.v)){open=false;return;}
   d+=(open?'L':'M')+X(i).toFixed(1)+' '+Y(p.v).toFixed(1)+' ';open=true;});
  if(s.fill)paths+='<path d="'+d+'L '+X(s.pts.length-1).toFixed(1)+' '+Y(Math.max(mn,0)).toFixed(1)
    +' L '+X(0).toFixed(1)+' '+Y(Math.max(mn,0)).toFixed(1)+' Z" fill="'+s.fill+'" stroke="none"/>';
  paths+='<path d="'+d+'" fill="none" stroke="'+s.color+'" stroke-width="'+(s.w||1.7)+'"/>';});
 const ref=live[0].pts;let xa='',nT=Math.min(7,Math.max(3,Math.floor(W/140)));
 for(let k=0;k<nT;k++){const i=Math.round(k*(n-1)/(nT-1));const p=ref[Math.min(i,ref.length-1)];
  if(!p)continue;
  xa+='<text x="'+X(i).toFixed(1)+'" y="'+(H-7)+'" font-size="9.5" fill="var(--dim)" text-anchor="'
   +(k===0?'start':k===nT-1?'end':'middle')+'">'+rvDate(p.t)+'</text>';}
 el.innerHTML='<div class="rvwrap"><svg viewBox="0 0 '+W+' '+H+'">'+g+paths+xa
  +'<line class="rvx" x1="0" y1="'+pt+'" x2="0" y2="'+(H-pb)+'" stroke="var(--dim)" stroke-dasharray="3 3" opacity="0"/>'
  +'<rect x="'+pl+'" y="'+pt+'" width="'+(W-pl-pr)+'" height="'+(H-pt-pb)+'" fill="transparent" class="rvhit"/>'
  +'</svg><div class="rvtip"></div></div>'
  +(o.legend===false?'':'<div class="rvleg">'+live.map(s=>'<span><i style="background:'+s.color+'"></i>'+mtEsc(s.name)+'</span>').join('')+'</div>');
 const svg=el.querySelector('svg'),tip=el.querySelector('.rvtip'),xl=el.querySelector('.rvx');
 const move=ev=>{
  const rect=svg.getBoundingClientRect();
  const cx=((ev.touches?ev.touches[0].clientX:ev.clientX)-rect.left)/rect.width*W;
  if(cx<pl||cx>W-pr){tip.classList.remove('on');xl.setAttribute('opacity','0');return;}
  let i=Math.round((cx-pl)/((W-pl-pr)||1)*(n-1));i=Math.max(0,Math.min(n-1,i));
  xl.setAttribute('x1',X(i));xl.setAttribute('x2',X(i));xl.setAttribute('opacity','1');
  const p0=ref[Math.min(i,ref.length-1)];
  tip.innerHTML='<div class="d">'+(p0?rvDateFull(p0.t):'')+'</div>'
   +live.map(s=>{const p=s.pts[Math.min(i,s.pts.length-1)];
     return '<div class="r"><span style="color:'+s.color+'">'+mtEsc(s.name)+'</span><b>'
      +(p&&p.v!=null?(o.fmtT?o.fmtT(p.v):mtN(p.v,2)):'-')+'</b></div>';}).join('');
  tip.classList.add('on');
  const px=X(i)/W*rect.width;
  tip.style.left=Math.min(rect.width-tip.offsetWidth-6,Math.max(4,px+12))+'px';
  tip.style.top='8px';};
 svg.addEventListener('mousemove',move);
 svg.addEventListener('touchmove',move,{passive:true});
 svg.addEventListener('mouseleave',()=>{tip.classList.remove('on');xl.setAttribute('opacity','0');});}

function rvDate(ts){const d=new Date(ts*1000);
 return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short'});}
function rvDateFull(ts){const d=new Date(ts*1000);
 return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});}
function rvBars(el,groups,labels,o){
 o=o||{};
 const live=groups.filter(g=>g.v.some(x=>x!=null));
 const all=[].concat(...live.map(g=>g.v.filter(x=>x!=null)));
 if(!all.length){el.innerHTML='<div class="mtempty">No reported figures.</div>';return;}
 const W=900,H=o.h||250,pl=58,pr=12,pt=14,pb=26,n=labels.length,k=live.length;
 const mx=Math.max(...all,0),mn=Math.min(...all,0),r=(mx-mn)||1;
 const gw=(W-pl-pr)/Math.max(n,1),bw=Math.min(38,(gw-10)/k),Y=v=>H-pb-((v-mn)/r)*(H-pt-pb),z=Y(0);
 let g='',bars='';
 for(let i=0;i<=4;i++){const v=mn+r*i/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="var(--line)"/>'
   +'<text x="'+(pl-6)+'" y="'+(+y+3.5).toFixed(1)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">'
   +(o.fmtY?o.fmtY(v):mtCap(v))+'</text>';}
 labels.forEach((L,i)=>{
  live.forEach((s,j)=>{const v=s.v[i];if(v==null)return;
   const x=pl+i*gw+(gw-bw*k)/2+j*bw,y=Math.min(Y(v),z),bh=Math.max(2,Math.abs(z-Y(v)));
   bars+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+(bw-3).toFixed(1)+'" height="'+bh.toFixed(1)
    +'" rx="2" fill="'+s.color+'"><title>'+mtEsc(s.name)+' '+L+': '+(o.fmtY?o.fmtY(v):mtCap(v))+'</title></rect>';});
  bars+='<text x="'+(pl+i*gw+gw/2).toFixed(1)+'" y="'+(H-7)+'" font-size="9.5" fill="var(--dim)" text-anchor="middle">'+L+'</text>';});
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'+g+bars+'</svg>'
  +'<div class="rvleg">'+live.map(s=>'<span><i style="background:'+s.color+'"></i>'+s.name+'</span>').join('')+'</div>';}

/* ---- fundamentals helpers ---- */
function rvSer(f,pre,key){return (f&&f.series&&f.series[pre+key])||[];}
function rvAlign(f,pre,keys,limit){
 const ds=[...new Set([].concat(...keys.map(k=>rvSer(f,pre,k).map(r=>r.d))))].sort().slice(-(limit||5));
 const out={dates:ds};
 keys.forEach(k=>{const m={};rvSer(f,pre,k).forEach(r=>m[r.d]=r.v);
  out[k]=ds.map(d=>m[d]==null?null:m[d]);});
 return out;}
function rvFY(d){return 'FY'+String(d).slice(2,4);}
function rvQL(d){const y=String(d).slice(0,4),m=+String(d).slice(5,7);
 return 'Q'+Math.ceil(m/3)+' '+y;}
function rvPctOf(num,den){return num.map((v,i)=>(v!=null&&den[i])?v/den[i]*100:null);}
function rvIdx(a){const b=a.find(v=>v!=null&&v!==0);
 return b?a.map(v=>v==null?null:v/b*100):a.map(()=>null);}

/* ---- section shell ---- */
function rvCard(id,title,right,desc,body){
 return '<div class="rvcard" id="rv-'+id+'"><div class="rvhd">'+title
  +(right?'<span class="r">'+right+'</span>':'')+'</div>'
  +(desc?'<p class="rvdesc">'+desc+'</p>':'')
  +'<div id="rvb-'+id+'">'+(body||'<div class="mtload">Loading...</div>')+'</div></div>';}
function rvFail(id,e){const el=document.getElementById('rvb-'+id);
 if(el)el.innerHTML='<div class="mtempty">Not available for this symbol'
  +(e&&e.message?' ('+mtEsc(e.message)+')':'')+'.</div>';}
function rvNote(id,txt){const el=document.getElementById('rv-'+id);
 if(el)el.insertAdjacentHTML('beforeend','<p class="rvfoot">'+txt+'</p>');}

/* ---- override: research tab ---- */
function mtRenderResearch(){
 if(!mtSetupCard('mtSetupR'))return;
 if(MT_RES_INIT)return;
 MT_RES_INIT=true;
 const quick=[...new Set([...DATA.up,...DATA.down].map(s=>s.sym))].slice(0,7);
 const qk=quick.length?quick:['NVDA','AAPL','MSFT','TSLA','ASML.AS','MAERSK-B.CO','6501.T'];
 document.getElementById('mtQuick').innerHTML=qk.map(s=>'<span class="mtchip" data-go="'+s+'">'+s+'</span>').join('');
 document.getElementById('mtThemes').innerHTML=Object.keys(MT_THEMES)
  .map(k=>'<span class="mtchip" data-theme="'+k+'">'+k.toUpperCase()+'</span>').join('');
 document.querySelectorAll('[data-theme]').forEach(el=>el.onclick=()=>mtShowTheme(el.dataset.theme));
 mtWireSyms();
 const inp=document.getElementById('mtQ');let tmr=null;
 inp.oninput=()=>{clearTimeout(tmr);const v=inp.value.trim();
  if(v.length<2){document.getElementById('mtQres').innerHTML='';return;}
  if(MT_THEMES[v.toLowerCase()]){mtShowTheme(v.toLowerCase());return;}
  tmr=setTimeout(async()=>{
   try{const rs=await mtGet('/search',{q:v});
    document.getElementById('mtQres').innerHTML=rs.length?'<div class="mtres">'
     +rs.slice(0,8).map(r=>'<div data-go="'+mtEsc(r.sym)+'"><span class="s">'+mtEsc(r.sym)+'</span>'
      +'<span class="n">'+mtEsc(r.name)+'</span><span class="e">'+mtEsc(r.exch)+'</span></div>').join('')+'</div>'
     :'<p class="mtnote">Nothing matched. Try a ticker, or a theme like bitcoin or uranium.</p>';
    mtWireSyms();
   }catch(e){document.getElementById('mtQres').innerHTML='<p class="mtnote">Search unavailable ('+mtEsc(e.message)+').</p>';}
  },320);};
 inp.onkeydown=e=>{if(e.key==='Enter'&&inp.value.trim())mtLoadSymbol(inp.value.trim().toUpperCase());};}

async function mtLoadSymbol(sym){
 sym=String(sym||'').trim().toUpperCase();if(!sym)return;
 RV={sym,st:null,range:'1Y',mode:'rebased',cmp:'^GSPC',peers:[],pstats:[],fundA:null,fundQ:null};
 const host=document.getElementById('mtSym');
 host.innerHTML='<div class="rvcard"><div class="mtload">Loading '+mtEsc(sym)+'...</div></div>';
 let st;
 try{st=await mtGet('/stats',{t:sym});}catch(e){
  host.innerHTML='<div class="rvcard"><div class="mtempty">Could not load '+mtEsc(sym)+' ('+mtEsc(e.message)+').</div></div>';return;}
 RV.st=st;
 const kv=[['Market cap',mtCap(st.mcap)],['P/E trailing',mtX(st.pe)],['P/E forward',mtX(st.fpe)],
  ['EV/EBITDA',mtX(st.evEbitda)],['Op margin',mtP(st.ebitMargin,1)],['Rev growth',mtP(st.revGrowth,1)],
  ['Div yield',st.divYield==null?'-':mtN(st.divYield,2)+'%'],['Beta',st.beta==null?'-':mtN(st.beta,2)]];
 host.innerHTML=
  '<div class="rvnav" id="rvNav">'+RV_SECTIONS.map(s=>'<button data-sec="'+s[0]+'">'+s[1]+'</button>').join('')+'</div>'
  +'<div class="rvcard" style="margin-top:8px"><div class="mthead">'
   +'<span class="s">'+mtEsc(st.sym||sym)+'</span><span class="n">'+mtEsc(st.name||'')+'</span>'
   +'<span class="p">'+mtN(st.price,2)+' <span style="font-size:13px;color:'+mtCol(st.chg)+'">'+mtP(st.chg)+' today</span></span></div>'
   +'<div class="rvchips">'+[st.exch,st.ccy,st.sector,st.industry].filter(Boolean)
     .map(x=>'<span class="rvchip">'+mtEsc(x)+'</span>').join('')+'</div>'
   +'<div class="rvstats">'+kv.map(x=>'<div><div class="k">'+x[0]+'</div><div class="v">'+x[1]+'</div></div>').join('')+'</div>'
   +'<p class="rvfoot">52-week range '+mtN(st.lo52,2)+' - '+mtN(st.hi52,2)
     +(st.nextEarnings?' &middot; next earnings '+st.nextEarnings:'')
     +(st.partial?' &middot; some valuation fields unavailable right now':'')+'</p></div>'
  +rvCard('chart','Price navigator - '+mtEsc(sym),'','')
  +rvCard('peers',mtEsc(sym)+' against its peer set','',
    'Every line rebased to 100 at the start of the window, so a share price and an index level sit on one scale. What you are reading is relative preference.')
  +rvCard('valuation','Valuation against the peer set','',
    'Each track spans the range across the comparable set; the grey tick is the peer median and the dot is '+mtEsc(sym)+'. A dot left of the tick means the market is asking less for this company\u2019s earnings than for its peers\u2019.')
  +rvCard('valuemap','Valuation against growth','',
    'Trailing P/E up the side, revenue growth across, bubble area by market capitalisation. Dashed lines are the peer medians - a high multiple is only a problem when the growth does not justify it.')
  +rvCard('quarters','The reporting record','',
    'Each quarter in one line, computed from the reported figures rather than written. Growth is year on year where there is enough history and sequential otherwise, labelled per row.')
  +rvCard('scale','Revenue and profit, as reported','',
    'The absolute scale of the business by fiscal year, with recent quarters shown separately so a turn shows up before the annuals catch it.')
  +rvCard('margins','Margin ladder - what survives each layer of cost','',
    'Gross, EBITDA, operating and net margin on one scale. The distance between the lines is the cost layer sitting between them.')
  +rvCard('traj','Growth-profitability trajectory','',
    'Each dot is one fiscal year: revenue growth across, operating margin up. The dashed diagonal is the Rule of 40, where growth plus margin equals 40.')
  +rvCard('cash','Earnings quality - profit versus cash','',
    'Accounting profit next to the cash the business actually generated. When net income runs persistently ahead of operating cash flow, the difference is accruals.')
  +rvCard('pershare','Does the growth reach the shareholder?','',
    'Revenue, revenue per share and free cash flow per share, all indexed to 100, against the diluted share count. If the share count climbs as fast as revenue, the business grew and the owner of one share did not.')
  +rvCard('reinv','Reinvestment intensity','',
    'Capital expenditure, research and selling costs as a share of revenue. Rising capex intensity with flat revenue is a business spending more to stand still.')
  +rvCard('balance','Balance-sheet resilience','',
    'Gross debt against the cash that offsets it. Under roughly 2x net debt to EBITDA is comfortable for most businesses; above 4x the balance sheet starts making the strategic decisions.')
  +rvCard('street','The street - targets, ratings and delivery','',
    'Where sell-side targets sit against the current quote, how the rating mix has drifted, and whether reported EPS has been beating the estimate it was measured against.')
  +rvCard('risk','Risk - distribution, volatility and drawdown','',
    'Every daily move in the window, bucketed, with realised volatility and the deepest drawdown episodes.')
  +rvCard('seas','Seasonality - calendar-month returns','',
    'Each cell is one calendar month\u2019s return; the right column compounds the year and the bottom row averages each month across every year on record.')
  +rvCard('about','What '+mtEsc(sym)+' actually does','','');
 document.querySelectorAll('#rvNav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#rvNav button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
  const el=document.getElementById('rv-'+b.dataset.sec);
  if(el)el.scrollIntoView({behavior:'smooth',block:'start'});});
 rvChartPanel();
 rvAboutPanel(st);
 rvPeerFlow(sym,st);
 mtGet('/fund',{t:sym,freq:'both'}).then(f=>{
  RV.fundA=f;RV.fundQ=f;
  [['quarters',rvQuarters],['scale',rvScale],['margins',rvMargins],['traj',rvTraj],
   ['cash',rvCash],['pershare',rvPerShare],['reinv',rvReinv],['balance',rvBalance]]
   .forEach(([id,fn])=>{try{fn(f);}catch(e){rvFail(id,e);}});
 }).catch(e=>['quarters','scale','margins','traj','cash','pershare','reinv','balance'].forEach(id=>rvFail(id,e)));
 mtGet('/street',{t:sym}).then(rvStreet).catch(e=>rvFail('street',e));
 mtGet('/chart',{t:sym,range:'10y',interval:'1d'}).then(c=>{try{rvRisk(c);}catch(e){rvFail('risk',e);}})
  .catch(e=>rvFail('risk',e));
 mtGet('/chart',{t:sym,range:'10y',interval:'1mo'}).then(c=>{try{rvSeas(c);}catch(e){rvFail('seas',e);}})
  .catch(e=>rvFail('seas',e));
}

/* ---- CHART ---- */
function rvChartPanel(){
 const b=document.getElementById('rvb-chart');
 b.innerHTML='<div class="rvbar">'
  +'<div class="rvseg" id="rvRange">'+RV_RANGES.map(r=>'<button data-r="'+r[0]+'"'+(r[0]===RV.range?' class="on"':'')+'>'+r[0]+'</button>').join('')+'</div>'
  +'<div class="rvseg" id="rvMode"><button data-m="price"'+(RV.mode==='price'?' class="on"':'')+'>Price</button>'
   +'<button data-m="rebased"'+(RV.mode==='rebased'?' class="on"':'')+'>Rebased to 100</button></div>'
  +'<label style="margin-left:auto;color:var(--dim);font-size:11.5px">Compare with '
   +'<select class="rvsel" id="rvCmp">'+RV_BENCH.map(x=>'<option value="'+x[0]+'"'+(x[0]===RV.cmp?' selected':'')+'>'+x[1]+'</option>').join('')+'</select></label>'
  +'</div><div id="rvChartBox"><div class="mtload">Loading...</div></div><div id="rvChartStats"></div>';
 b.querySelectorAll('#rvRange button').forEach(x=>x.onclick=()=>{RV.range=x.dataset.r;rvChartPanel();});
 b.querySelectorAll('#rvMode button').forEach(x=>x.onclick=()=>{RV.mode=x.dataset.m;rvChartPanel();});
 b.querySelector('#rvCmp').onchange=e=>{RV.cmp=e.target.value;rvChartPanel();};
 rvChartDraw();}

async function rvChartDraw(){
 const box=document.getElementById('rvChartBox');
 const cfg=RV_RANGES.find(r=>r[0]===RV.range)||RV_RANGES[4];
 const cmp=RV.cmp!=='none';
 const reb=RV.mode==='rebased'||cmp;
 try{
  const [me,bench]=await Promise.all([
   mtGet('/chart',{t:RV.sym,range:cfg[1],interval:cfg[2]}),
   cmp?mtGet('/chart',{t:RV.cmp,range:cfg[1],interval:cfg[2]}).catch(()=>null):Promise.resolve(null)]);
  const base=me.c[0];
  const mk=(c,t,b)=>c.map((v,i)=>({t:t[i],v:reb?(b?v/b*100:null):v}));
  const series=[{name:RV.sym,color:'var(--gold)',pts:mk(me.c,me.t,base),w:2}];
  if(bench&&bench.c.length>1)
   series.push({name:(RV_BENCH.find(x=>x[0]===RV.cmp)||[,RV.cmp])[1],color:'var(--accent)',
     pts:mk(bench.c,bench.t,bench.c[0])});
  rvPlot(box,series,{h:300,baseline:reb?100:null,
   fmtY:v=>reb?mtN(v,0):mtN(v,Math.abs(v)>=1000?0:2),fmtT:v=>mtN(v,2)});
  const first=me.c[0],last=me.c[me.c.length-1];
  const days=Math.max(1,(me.t[me.t.length-1]-me.t[0])/86400);
  const ret=(last/first-1)*100, ann=(Math.pow(last/first,365/days)-1)*100;
  let peak=-Infinity,dd=0;me.c.forEach(v=>{if(v>peak)peak=v;dd=Math.min(dd,(v/peak-1)*100);});
  const S=[['Window return',mtP(ret),mtCol(ret)],['Annualised',mtP(ann),mtCol(ann)],
   ['Window high',mtN(Math.max(...me.c),2),''],['Window low',mtN(Math.min(...me.c),2),''],
   ['Max drawdown',mtP(dd),'var(--down)']];
  document.getElementById('rvChartStats').innerHTML='<div class="rvstats">'
   +S.map(x=>'<div><div class="k">'+x[0]+'</div><div class="v" style="color:'+(x[2]||'var(--ink)')+'">'+x[1]+'</div></div>').join('')
   +'</div>'+(cmp?'<p class="rvfoot">Comparing two instruments, so both series are indexed to 100 at the start of the window - that is the only way a share price and an index level share an axis honestly.</p>':'');
 }catch(e){box.innerHTML='<div class="mtempty">Chart unavailable ('+mtEsc(e.message)+').</div>';}}

/* ---- PEERS / VALUATION / VALUE MAP ---- */
async function rvPeerFlow(sym,st){
 let peers=[];
 try{peers=await mtGet('/peers',{t:sym});}catch(e){}
 if(!peers.length){const me=[...DATA.up,...DATA.down].find(s=>s.sym===sym);
  if(me&&me.sector)peers=[...DATA.up,...DATA.down].filter(s=>s.sector===me.sector&&s.sym!==sym).slice(0,5).map(s=>s.sym);}
 peers=peers.filter(p=>p&&p!==sym).slice(0,5);
 RV.peers=peers;
 if(!peers.length){['peers','valuation','valuemap'].forEach(id=>rvFail(id,{message:'no comparable set published'}));return;}
 const cfg=RV_RANGES.find(r=>r[0]==='1Y');
 Promise.all([sym,...peers].map(s=>mtGet('/chart',{t:s,range:'1y',interval:'1d'}).catch(()=>null)))
  .then(cs=>{
   const live=cs.filter(Boolean);
   if(!live.length)return rvFail('peers',{message:'no price history'});
   const series=live.map((c,i)=>({name:c.sym,color:RV_COLORS[i%RV_COLORS.length],w:i===0?2.2:1.4,
     pts:c.c.map((v,j)=>({t:c.t[j],v:v/c.c[0]*100}))}));
   const box=document.createElement('div');
   document.getElementById('rvb-peers').innerHTML='<div id="rvPeerChart"></div><div id="rvPeerTable"></div>';
   rvPlot(document.getElementById('rvPeerChart'),series,{h:300,baseline:100,fmtY:v=>mtN(v,0),fmtT:v=>mtN(v,1)});
   const rows=live.map(c=>({sym:c.sym,name:c.sym===RV.sym?'This company':c.name,
     ret:(c.c[c.c.length-1]/c.c[0]-1)*100})).sort((a,b)=>b.ret-a.ret);
   document.getElementById('rvPeerTable').innerHTML='<table class="rvt"><thead><tr><th>Symbol</th><th style="text-align:left">Name</th><th>Window return</th></tr></thead><tbody>'
    +rows.map(r=>'<tr class="'+(r.sym===RV.sym?'me':'')+'"><td><span class="mtsym" data-go="'+r.sym+'">'+r.sym+'</span></td>'
     +'<td style="text-align:left;color:var(--muted)">'+mtEsc(r.name)+'</td>'
     +'<td style="color:'+mtCol(r.ret)+'">'+mtP(r.ret,1)+'</td></tr>').join('')+'</tbody></table>';
   mtWireSyms();
  });
 const ps=(await Promise.all(peers.map(p=>mtGet('/stats',{t:p}).catch(()=>null)))).filter(Boolean);
 RV.pstats=ps;
 rvValuation(st,ps);
 rvValueMap(st,ps);}

function rvMedian(a){const v=a.filter(x=>x!=null&&isFinite(x)).sort((p,q)=>p-q);
 return v.length?v[Math.floor(v.length/2)]:null;}
function rvValuation(st,ps){
 const el=document.getElementById('rvb-valuation');
 const rows=[['P/E (trailing)','pe','x'],['P/E (forward)','fpe','x'],['EV / EBITDA','evEbitda','x'],
  ['Price / sales','ps','x'],['Price / book','pb','x'],['Operating margin','ebitMargin','%'],
  ['Revenue growth','revGrowth','%']];
 const fmt=(v,u)=>v==null?'-':(u==='x'?mtX(v):mtP(v,1));
 el.innerHTML=rows.map(([label,key,u])=>{
  const mine=st[key];
  const vals=ps.map(p=>p[key]).filter(v=>v!=null&&isFinite(v)&&Math.abs(v)<500);
  const med=rvMedian(vals);
  if(mine==null||!vals.length)
   return '<div class="rvtrack"><span>'+label+'</span><span class="tk"><span class="ln"></span></span>'
    +'<span class="vv"><b>'+fmt(mine,u)+'</b> <span>/ '+fmt(med,u)+'</span></span></div>';
  const lo=Math.min(...vals,mine),hi=Math.max(...vals,mine),r=(hi-lo)||1;
  const pos=v=>((v-lo)/r*100).toFixed(1);
  const good=(u==='%')?(mine>=med):(mine<=med);
  return '<div class="rvtrack"><span>'+label+'</span>'
   +'<span class="tk"><span class="ln"></span><span class="md" style="left:'+pos(med)+'%"></span>'
    +'<span class="dot" style="left:'+pos(mine)+'%;background:'+(good?'var(--up)':'var(--gold)')+'"></span></span>'
   +'<span class="vv"><b>'+fmt(mine,u)+'</b> <span>/ '+fmt(med,u)+'</span></span></div>';}).join('')
  +'<div class="rvleg"><span><i style="background:var(--gold);border-radius:50%;width:9px;height:9px"></i>'+mtEsc(st.sym)+'</span>'
   +'<span><i style="background:var(--dim);width:2px;height:11px"></i>peer median</span></div>'
  +'<table class="rvt"><thead><tr><th>Peer</th><th>Mkt cap</th><th>P/E</th><th>EV/EBITDA</th><th>P/S</th><th>Op margin</th><th>Rev growth</th></tr></thead><tbody>'
  +[st,...ps].map(p=>'<tr class="'+(p.sym===st.sym?'me':'')+'"><td><span class="mtsym" data-go="'+p.sym+'">'+p.sym+'</span>'
    +'<div style="color:var(--dim);font-size:10.5px">'+mtEsc((p.name||'').slice(0,28))+'</div></td>'
    +'<td>'+mtCap(p.mcap)+'</td><td>'+mtX(p.pe)+'</td><td>'+mtX(p.evEbitda)+'</td><td>'+mtX(p.ps)+'</td>'
    +'<td>'+mtP(p.ebitMargin,1)+'</td><td style="color:'+mtCol(p.revGrowth)+'">'+mtP(p.revGrowth,1)+'</td></tr>').join('')
  +'</tbody></table>'
  +'<p class="rvfoot">Peer set comes from the free comparable list the data provider publishes for this symbol - a proxy for a formal industry classification, not a substitute for one. Multiples beyond 500x are dropped as reporting artefacts.</p>';
 mtWireSyms();}

function rvValueMap(st,ps){
 const el=document.getElementById('rvb-valuemap');
 const pts=[st,...ps].map(p=>({sym:p.sym,x:p.revGrowth,y:p.pe,m:p.mcap,me:p.sym===st.sym}))
  .filter(p=>p.x!=null&&p.y!=null&&isFinite(p.x)&&isFinite(p.y)&&p.y<500);
 if(pts.length<3){el.innerHTML='<div class="mtempty">Not enough comparable multiples to map.</div>';return;}
 const W=900,H=320,pl=54,pr=18,pt=18,pb=32;
 const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y);
 let x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys,0),y1=Math.max(...ys);
 const xp=(x1-x0)*0.15||5,yp=(y1-y0)*0.15||5;x0-=xp;x1+=xp;y1+=yp;
 const X=v=>pl+(v-x0)/((x1-x0)||1)*(W-pl-pr),Y=v=>H-pb-(v-y0)/((y1-y0)||1)*(H-pt-pb);
 const mx=rvMedian(xs),my=rvMedian(ys);
 const maxM=Math.max(...pts.map(p=>p.m||0))||1;
 let g='';
 for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="var(--line)"/>'
   +'<text x="'+(pl-6)+'" y="'+(+y+3.5).toFixed(1)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">'+v.toFixed(0)+'x</text>';}
 for(let k=0;k<=4;k++){const v=x0+(x1-x0)*k/4;
  g+='<text x="'+X(v).toFixed(1)+'" y="'+(H-9)+'" font-size="9.5" fill="var(--dim)" text-anchor="middle">'+v.toFixed(0)+'%</text>';}
 g+='<line x1="'+X(mx).toFixed(1)+'" y1="'+pt+'" x2="'+X(mx).toFixed(1)+'" y2="'+(H-pb)+'" stroke="var(--dim)" stroke-dasharray="4 3" opacity=".6"/>'
  +'<line x1="'+pl+'" y1="'+Y(my).toFixed(1)+'" x2="'+(W-pr)+'" y2="'+Y(my).toFixed(1)+'" stroke="var(--dim)" stroke-dasharray="4 3" opacity=".6"/>';
 const dots=pts.map(p=>{const r=8+22*Math.sqrt((p.m||0)/maxM);
  return '<circle cx="'+X(p.x).toFixed(1)+'" cy="'+Y(p.y).toFixed(1)+'" r="'+r.toFixed(1)+'" fill="'
   +(p.me?'var(--gold)':'var(--accent)')+'" opacity="'+(p.me?'.75':'.35')+'"/>'
   +'<text x="'+X(p.x).toFixed(1)+'" y="'+(Y(p.y)-r-5).toFixed(1)+'" font-size="10" fill="'
   +(p.me?'var(--gold)':'var(--muted)')+'" text-anchor="middle">'+p.sym+'</text>';}).join('');
 const me=pts.find(p=>p.me);
 let verdict='';
 if(me){const cheap=me.y<my,fast=me.x>mx;
  verdict=cheap&&fast?mtEsc(st.sym)+' is cheaper than the median peer while growing faster - the quadrant that needs no explanation.'
   :!cheap&&fast?mtEsc(st.sym)+' carries a premium multiple and is growing faster than the median; the growth is what has to hold.'
   :cheap&&!fast?mtEsc(st.sym)+' is cheaper than the median peer and growing more slowly - the discount may be deserved.'
   :mtEsc(st.sym)+' is dearer than the median peer while growing more slowly, which is the quadrant that needs the best explanation.';}
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'+g+dots
  +'<text x="'+(W-pr)+'" y="'+(H-9)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">revenue growth</text></svg>'
  +(verdict?'<p class="rvfoot" style="color:var(--muted);font-size:12.5px">'+verdict+'</p>':'');}

/* ---- QUARTERS ---- */
function rvQuarters(f){
 const a=rvAlign(f,'quarterly',['TotalRevenue','OperatingIncome','NetIncome','FreeCashFlow'],6);
 const el=document.getElementById('rvb-quarters');
 const rows=a.dates.map((d,i)=>({d,rev:a.TotalRevenue[i],oi:a.OperatingIncome[i],
  ni:a.NetIncome[i],fcf:a.FreeCashFlow[i]})).filter(r=>r.rev!=null);
 if(rows.length<2){el.innerHTML='<div class="mtempty">No quarterly history published.</div>';return;}
 rows.forEach((r,i)=>{
  r.margin=r.oi!=null&&r.rev?r.oi/r.rev*100:null;
  const prev=rows[i-1],yoy=rows[i-4];
  r.growth=yoy&&yoy.rev?{v:(r.rev/yoy.rev-1)*100,k:'year on year'}
   :prev&&prev.rev?{v:(r.rev/prev.rev-1)*100,k:'on the quarter'}:null;
  r.dm=(prev&&prev.margin!=null&&r.margin!=null)?(r.margin-prev.margin)*100:null;
  r.cc=(r.fcf!=null&&r.ni&&r.ni>0)?r.fcf/r.ni*100:null;});
 const disp=rows.slice(-5).reverse();
 const maxRev=Math.max(...rows.map(r=>r.rev));
 const maxM=Math.max(...rows.filter(r=>r.margin!=null).map(r=>r.margin));
 const minM=Math.min(...rows.filter(r=>r.margin!=null).map(r=>r.margin));
 el.innerHTML=disp.map(r=>{
  const bits=[];
  bits.push('Revenue '+mtCap(r.rev)+(r.growth?', '+mtP(r.growth.v,1)+' '+r.growth.k:''));
  if(r.rev===maxRev)bits.push('the highest revenue in the reported history');
  if(r.margin!=null&&r.margin===maxM)bits.push('the widest operating margin here at '+mtP(r.margin,1));
  if(r.margin!=null&&r.margin===minM)bits.push('the narrowest operating margin here at '+mtP(r.margin,1));
  if(r.cc!=null&&r.cc<60)bits.push('free cash flow covering only '+Math.round(r.cc)+'% of net income');
  if(r.dm!=null&&Math.abs(r.dm)>=20&&r.margin!=null&&r.margin!==maxM)
   bits.push('operating margin '+mtP(r.margin,1)+', '+(r.dm>=0?'up ':'down ')+Math.abs(Math.round(r.dm))+'bp');
  return '<div class="rvq"><div class="h"><b>'+rvQL(r.d)+'</b>'
   +'<span style="color:'+mtCol(r.growth?r.growth.v:null)+'">'+(r.growth?mtP(r.growth.v,1):'\u2014')+'</span></div>'
   +'<p>'+bits.join('; ')+'.</p></div>';}).join('')
  +'<table class="rvt"><thead><tr><th>Quarter</th><th>Revenue</th><th>Growth</th><th>Op margin</th><th>&Delta; margin</th><th>Cash conv.</th></tr></thead><tbody>'
  +disp.map(r=>'<tr><td>'+rvQL(r.d)+'</td><td>'+mtCap(r.rev)+'</td>'
   +'<td style="color:'+mtCol(r.growth?r.growth.v:null)+'">'+(r.growth?mtP(r.growth.v,1):'\u2014')+'</td>'
   +'<td>'+mtP(r.margin,1)+'</td><td>'+(r.dm==null?'\u2014':(r.dm>=0?'+':'')+Math.round(r.dm)+'bp')+'</td>'
   +'<td>'+(r.cc==null?'\u2014':Math.round(r.cc)+'%')+'</td></tr>').join('')+'</tbody></table>';
 rvNote('quarters','Every sentence is arithmetic over the reported figures, so the same filing always produces the same line and each clause can be checked against the table. Cash conversion is free cash flow as a share of net income, left blank where the ratio means nothing.');}

/* ---- SCALE / MARGINS / TRAJECTORY / CASH / PER SHARE / REINVESTMENT / BALANCE ---- */
function rvScale(f){
 const a=rvAlign(f,'annual',['TotalRevenue','NetIncome'],5);
 const q=rvAlign(f,'quarterly',['TotalRevenue','NetIncome'],5);
 const el=document.getElementById('rvb-scale');
 el.innerHTML='<div id="rvScaleA"></div><div class="mtlbl">Most recent quarters</div><div id="rvScaleQ"></div>';
 rvBars(document.getElementById('rvScaleA'),
  [{name:'Revenue',v:a.TotalRevenue,color:'var(--accent)'},{name:'Net income',v:a.NetIncome,color:'var(--gold)'}],
  a.dates.map(rvFY),{h:230});
 rvBars(document.getElementById('rvScaleQ'),
  [{name:'Revenue',v:q.TotalRevenue,color:'var(--accent)'},{name:'Net income',v:q.NetIncome,color:'var(--gold)'}],
  q.dates.map(d=>String(d).slice(2,7)),{h:190});}

function rvMargins(f){
 const a=rvAlign(f,'annual',['TotalRevenue','GrossProfit','EBITDA','OperatingIncome','NetIncome'],6);
 const el=document.getElementById('rvb-margins');
 const pts=k=>rvPctOf(a[k],a.TotalRevenue).map((v,i)=>({t:new Date(a.dates[i]).getTime()/1000,v}));
 rvPlot(el,[{name:'Gross margin',color:'var(--accent)',pts:pts('GrossProfit')},
  {name:'EBITDA margin',color:'#4ec9d4',pts:pts('EBITDA')},
  {name:'Operating margin',color:'var(--gold)',pts:pts('OperatingIncome')},
  {name:'Net margin',color:'var(--up)',pts:pts('NetIncome')}],
  {h:260,zero:true,fmtY:v=>v.toFixed(0)+'%',fmtT:v=>v==null?'-':v.toFixed(1)+'%'});}

function rvTraj(f){
 const a=rvAlign(f,'annual',['TotalRevenue','OperatingIncome'],6);
 const el=document.getElementById('rvb-traj');
 const pts=[];
 for(let i=1;i<a.dates.length;i++){
  const r=a.TotalRevenue,o=a.OperatingIncome;
  if(r[i]==null||!r[i-1]||o[i]==null)continue;
  pts.push({y:rvFY(a.dates[i]),g:(r[i]/r[i-1]-1)*100,m:o[i]/r[i]*100});}
 if(pts.length<2){el.innerHTML='<div class="mtempty">Not enough annual history to plot the path.</div>';return;}
 const W=900,H=300,pl=52,pr=20,pt=20,pb=32;
 const gs=pts.map(p=>p.g),ms=pts.map(p=>p.m);
 let x0=Math.min(...gs,0),x1=Math.max(...gs,45),y0=Math.min(...ms,0),y1=Math.max(...ms,45);
 const xp=(x1-x0)*.12||5,yp=(y1-y0)*.12||5;x0-=xp;x1+=xp;y0-=yp;y1+=yp;
 const X=v=>pl+(v-x0)/((x1-x0)||1)*(W-pl-pr),Y=v=>H-pb-(v-y0)/((y1-y0)||1)*(H-pt-pb);
 let g='';
 for(let k=0;k<=4;k++){const v=y0+(y1-y0)*k/4,y=Y(v).toFixed(1);
  g+='<line x1="'+pl+'" y1="'+y+'" x2="'+(W-pr)+'" y2="'+y+'" stroke="var(--line)"/>'
   +'<text x="'+(pl-6)+'" y="'+(+y+3.5).toFixed(1)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">'+v.toFixed(0)+'%</text>';}
 for(let k=0;k<=4;k++){const v=x0+(x1-x0)*k/4;
  g+='<text x="'+X(v).toFixed(1)+'" y="'+(H-9)+'" font-size="9.5" fill="var(--dim)" text-anchor="middle">'+v.toFixed(0)+'%</text>';}
 g+='<line x1="'+X(x0).toFixed(1)+'" y1="'+Y(40-x0).toFixed(1)+'" x2="'+X(x1).toFixed(1)+'" y2="'+Y(40-x1).toFixed(1)
   +'" stroke="var(--dim)" stroke-dasharray="5 4" opacity=".55"/>';
 const path=pts.map((p,i)=>(i?'L':'M')+X(p.g).toFixed(1)+' '+Y(p.m).toFixed(1)).join(' ');
 const dots=pts.map((p,i)=>{const t=i/(pts.length-1||1);
  const col='rgb('+Math.round(91+130*t)+','+Math.round(140-20*t)+','+Math.round(255-140*t)+')';
  return '<circle cx="'+X(p.g).toFixed(1)+'" cy="'+Y(p.m).toFixed(1)+'" r="'+(i===pts.length-1?7:5.5)+'" fill="'+col+'"/>'
   +'<text x="'+X(p.g).toFixed(1)+'" y="'+(Y(p.m)-11).toFixed(1)+'" font-size="10" fill="var(--muted)" text-anchor="middle">'+p.y+'</text>';}).join('');
 const last=pts[pts.length-1],score=Math.round(last.g+last.m);
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'+g
  +'<path d="'+path+'" fill="none" stroke="var(--dim)" stroke-width="1.4"/>'+dots
  +'<text x="'+(W-pr)+'" y="'+(H-9)+'" font-size="9.5" fill="var(--dim)" text-anchor="end">revenue growth</text></svg>'
  +'<div class="rvleg"><span>'+last.y+' score '+score+' (growth + margin)</span><span>dots run cold to warm, oldest to newest</span></div>';}

function rvCash(f){
 const a=rvAlign(f,'annual',['NetIncome','OperatingCashFlow','FreeCashFlow'],5);
 rvBars(document.getElementById('rvb-cash'),
  [{name:'Net income',v:a.NetIncome,color:'var(--accent)'},
   {name:'Operating cash flow',v:a.OperatingCashFlow,color:'var(--gold)'},
   {name:'Free cash flow',v:a.FreeCashFlow,color:'var(--up)'}],
  a.dates.map(rvFY),{h:250});
 const ni=a.NetIncome[a.NetIncome.length-1],fcf=a.FreeCashFlow[a.FreeCashFlow.length-1];
 if(ni&&fcf!=null)rvNote('cash','Latest year free cash flow covers '+(fcf/ni).toFixed(2)+'x net income.');}

function rvPerShare(f){
 const a=rvAlign(f,'annual',['TotalRevenue','FreeCashFlow','DilutedAverageShares'],6);
 const el=document.getElementById('rvb-pershare');
 const sh=a.DilutedAverageShares;
 if(!sh.some(v=>v!=null)){el.innerHTML='<div class="mtempty">No share-count history published.</div>';return;}
 const rps=a.TotalRevenue.map((v,i)=>(v!=null&&sh[i])?v/sh[i]:null);
 const fps=a.FreeCashFlow.map((v,i)=>(v!=null&&sh[i])?v/sh[i]:null);
 const t=a.dates.map(d=>new Date(d).getTime()/1000);
 const mk=(arr,name,color)=>({name,color,pts:rvIdx(arr).map((v,i)=>({t:t[i],v}))});
 rvPlot(el,[mk(a.TotalRevenue,'Revenue','var(--accent)'),mk(rps,'Revenue / share','var(--gold)'),
  mk(fps,'FCF / share','var(--up)'),mk(sh,'Diluted share count','var(--down)')],
  {h:260,baseline:100,fmtY:v=>mtN(v,0),fmtT:v=>v==null?'-':mtN(v,1)});
 const first=sh.find(v=>v!=null),last=[...sh].reverse().find(v=>v!=null);
 if(first&&last)rvNote('pershare','Share count '+mtP((last/first-1)*100,1)+' across the window. All four series are indexed to 100 at the first reported year, so this is a comparison of shape, not level.');}

function rvReinv(f){
 const a=rvAlign(f,'annual',['TotalRevenue','CapitalExpenditure','ResearchAndDevelopment','SellingGeneralAndAdministration'],6);
 const el=document.getElementById('rvb-reinv');
 const t=a.dates.map(d=>new Date(d).getTime()/1000);
 const S=[['Capex','CapitalExpenditure','var(--gold)'],['R&D','ResearchAndDevelopment','var(--accent)'],
  ['SG&A','SellingGeneralAndAdministration','var(--up)']]
  .map(([name,k,color])=>({name,color,pts:rvPctOf(a[k].map(v=>v==null?null:Math.abs(v)),a.TotalRevenue)
    .map((v,i)=>({t:t[i],v}))}));
 rvPlot(el,S,{h:240,zero:true,fmtY:v=>v.toFixed(0)+'%',fmtT:v=>v==null?'-':v.toFixed(1)+'%'});}

function rvBalance(f){
 const a=rvAlign(f,'annual',['CashAndCashEquivalents','TotalDebt','EBITDA'],5);
 const el=document.getElementById('rvb-balance');
 el.innerHTML='<div id="rvBalBars"></div><div class="mtlbl">Net debt / EBITDA</div><div id="rvBalLev"></div>';
 rvBars(document.getElementById('rvBalBars'),
  [{name:'Cash & equivalents',v:a.CashAndCashEquivalents,color:'var(--up)'},
   {name:'Total debt',v:a.TotalDebt,color:'var(--down)'}],a.dates.map(rvFY),{h:220});
 const lev=a.dates.map((d,i)=>{const nd=(a.TotalDebt[i]||0)-(a.CashAndCashEquivalents[i]||0);
  return (a.EBITDA[i]&&a.EBITDA[i]>0)?nd/a.EBITDA[i]:null;});
 rvPlot(document.getElementById('rvBalLev'),
  [{name:'Net debt / EBITDA',color:'var(--gold)',pts:lev.map((v,i)=>({t:new Date(a.dates[i]).getTime()/1000,v}))}],
  {h:170,zero:true,fmtY:v=>v.toFixed(1)+'x',fmtT:v=>v==null?'-':v.toFixed(2)+'x',legend:false});}

/* ---- STREET ---- */
function rvStreet(s){
 const el=document.getElementById('rvb-street');
 if(!s||(!s.target&&!s.ratings)){rvFail('street',{message:'no analyst data published'});return;}
 let h='';
 const t=s.target||{};
 if(t.mean&&t.price){
  const lo=Math.min(t.low||t.mean,t.price),hi=Math.max(t.high||t.mean,t.price),r=(hi-lo)||1;
  const pos=v=>((v-lo)/r*100).toFixed(1);
  const up=(t.mean/t.price-1)*100;
  h+='<div class="rvtrack" style="grid-template-columns:1fr"><span class="tk" style="height:22px">'
   +'<span class="ln" style="top:10px"></span>'
   +'<span class="dot" style="left:'+pos(t.price)+'%;top:6px;background:var(--ink)"></span>'
   +'<span class="dot" style="left:'+pos(t.mean)+'%;top:6px;background:var(--gold)"></span></span></div>'
   +'<div class="rvleg"><span><i style="background:var(--ink);border-radius:50%;width:9px;height:9px"></i>now '+mtN(t.price,2)+'</span>'
   +'<span><i style="background:var(--gold);border-radius:50%;width:9px;height:9px"></i>consensus target '+mtN(t.mean,2)+'</span>'
   +'<span>range '+mtN(t.low,0)+' - '+mtN(t.high,0)+'</span>'
   +'<span style="color:'+mtCol(up)+'">implied '+mtP(up,1)+'</span>'
   +(t.n?'<span>'+t.n+' analysts</span>':'')+'</div>';}
 if(s.ratings&&s.ratings.length){
  const R=s.ratings.slice(0,4).reverse();
  h+='<div class="mtlbl">Rating mix by month</div><table class="rvt"><thead><tr><th>Period</th>'
   +'<th>Strong buy</th><th>Buy</th><th>Hold</th><th>Sell</th><th>Strong sell</th></tr></thead><tbody>'
   +R.map(x=>'<tr><td>'+mtEsc(x.period)+'</td><td style="color:var(--up)">'+(x.strongBuy||0)+'</td>'
    +'<td style="color:var(--up)">'+(x.buy||0)+'</td><td>'+(x.hold||0)+'</td>'
    +'<td style="color:var(--down)">'+(x.sell||0)+'</td><td style="color:var(--down)">'+(x.strongSell||0)+'</td></tr>').join('')
   +'</tbody></table>';}
 if(s.surprises&&s.surprises.length){
  h+='<div class="mtlbl">EPS surprise history</div><table class="rvt"><thead><tr><th>Quarter</th>'
   +'<th>Estimate</th><th>Actual</th><th>Surprise</th></tr></thead><tbody>'
   +s.surprises.slice(0,6).map(x=>'<tr><td>'+mtEsc(x.q||'')+'</td><td>'+mtN(x.est,2)+'</td>'
    +'<td>'+mtN(x.actual,2)+'</td><td style="color:'+mtCol(x.surprisePct)+'">'+mtP(x.surprisePct,1)+'</td></tr>').join('')
   +'</tbody></table>';}
 el.innerHTML=h||'<div class="mtempty">No analyst coverage published for this symbol.</div>';}

/* ---- RISK ---- */
function rvRisk(ch){
 const c=ch.c||[],t=ch.t||[];
 const el=document.getElementById('rvb-risk');
 if(c.length<60){el.innerHTML='<div class="mtempty">Not enough price history.</div>';return;}
 const rets=[];for(let i=1;i<c.length;i++){if(c[i-1])rets.push((c[i]/c[i-1]-1)*100);}
 const mean=rets.reduce((a,b)=>a+b,0)/rets.length;
 const sd=Math.sqrt(rets.reduce((a,b)=>a+(b-mean)*(b-mean),0)/rets.length);
 const vol=sd*Math.sqrt(252);
 const sorted=[...rets].sort((a,b)=>a-b);
 const tail=sorted[Math.floor(sorted.length*0.05)];
 const upDays=rets.filter(r=>r>0).length/rets.length*100;
 let peak=-Infinity;const dd=[];
 for(let i=0;i<c.length;i++){if(c[i]>peak)peak=c[i];dd.push((c[i]/peak-1)*100);}
 const worst=Math.min(...dd);
 // drawdown episodes
 const eps=[];let inEp=null;peak=-Infinity;let peakI=0;
 for(let i=0;i<c.length;i++){
  if(c[i]>=peak){peak=c[i];peakI=i;
   if(inEp){inEp.recovered=i;eps.push(inEp);inEp=null;}}
  else{const d=(c[i]/peak-1)*100;
   if(!inEp)inEp={peakI,troughI:i,depth:d};
   else if(d<inEp.depth){inEp.depth=d;inEp.troughI=i;}}}
 if(inEp)eps.push(inEp);
 const top=eps.filter(e=>e.depth<-4).sort((a,b)=>a.depth-b.depth).slice(0,5);
 const dstr=i=>new Date(t[i]*1000).toISOString().slice(0,10);
 // histogram
 const lo=Math.min(...rets),hi=Math.max(...rets),nb=31,bw=(hi-lo)/nb||1;
 const bins=new Array(nb).fill(0);
 rets.forEach(r=>{let k=Math.floor((r-lo)/bw);k=Math.max(0,Math.min(nb-1,k));bins[k]++;});
 const W=900,H=190,pl=44,pr=12,pt=12,pb=24,mxb=Math.max(...bins);
 let bars='';
 bins.forEach((v,i)=>{const x=pl+i*(W-pl-pr)/nb,w=(W-pl-pr)/nb-2,h=v/mxb*(H-pt-pb);
  const mid=lo+(i+0.5)*bw;
  bars+='<rect x="'+x.toFixed(1)+'" y="'+(H-pb-h).toFixed(1)+'" width="'+w.toFixed(1)+'" height="'+h.toFixed(1)
   +'" fill="'+(mid>=0?'var(--up)':'var(--down)')+'" opacity=".65"><title>'+mid.toFixed(1)+'%: '+v+' days</title></rect>';});
 [lo,0,hi].forEach(v=>{const x=pl+(v-lo)/((hi-lo)||1)*(W-pl-pr);
  bars+='<text x="'+x.toFixed(1)+'" y="'+(H-7)+'" font-size="9.5" fill="var(--dim)" text-anchor="middle">'+v.toFixed(1)+'%</text>';});
 const S=[['Annualised vol',vol.toFixed(1)+'%',''],['Avg daily',mtP(mean,2),mtCol(mean)],
  ['Up days',upDays.toFixed(0)+'%',''],['5% tail day',mtP(tail,2),'var(--down)'],
  ['Worst day',mtP(Math.min(...rets),2),'var(--down)'],['Max drawdown',mtP(worst,1),'var(--down)']];
 el.innerHTML='<div class="rvstats" style="margin-top:0">'
  +S.map(x=>'<div><div class="k">'+x[0]+'</div><div class="v" style="color:'+(x[2]||'var(--ink)')+'">'+x[1]+'</div></div>').join('')+'</div>'
  +'<div class="mtlbl">Daily return distribution</div>'
  +'<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'+bars+'</svg>'
  +'<div class="mtlbl">Underwater curve - how far from the last high</div><div id="rvUw"></div>'
  +(top.length?'<table class="rvt"><thead><tr><th>Peak</th><th>Trough</th><th>Depth</th><th>Days down</th><th>Days to recover</th></tr></thead><tbody>'
   +top.map(e=>'<tr><td>'+dstr(e.peakI)+'</td><td>'+dstr(e.troughI)+'</td>'
    +'<td style="color:var(--down)">'+e.depth.toFixed(1)+'%</td><td>'+(e.troughI-e.peakI)+'</td>'
    +'<td>'+(e.recovered?(e.recovered-e.troughI):'not yet')+'</td></tr>').join('')+'</tbody></table>':'');
 const N=600;let idx=dd.map((_,i)=>i);
 if(dd.length>N){const st=dd.length/N;idx=Array.from({length:N},(_,i)=>Math.min(dd.length-1,Math.floor(i*st)));}
 rvPlot(document.getElementById('rvUw'),
  [{name:'Drawdown',color:'var(--down)',w:1.2,fill:'rgba(255,93,108,.18)',
    pts:idx.map(i=>({t:t[i],v:+dd[i].toFixed(2)}))}],
  {h:200,zero:true,legend:false,fmtY:v=>v.toFixed(0)+'%',fmtT:v=>v==null?'-':v.toFixed(1)+'%'});}

/* ---- SEASONALITY ---- */
function rvSeas(ch){
 const c=ch.c||[],t=ch.t||[];
 const el=document.getElementById('rvb-seas');
 if(c.length<14){el.innerHTML='<div class="mtempty">Not enough monthly history.</div>';return;}
 const byY={};
 for(let i=1;i<c.length;i++){if(!c[i-1])continue;
  const d=new Date(t[i]*1000);(byY[d.getUTCFullYear()]=byY[d.getUTCFullYear()]||{})[d.getUTCMonth()]=(c[i]/c[i-1]-1)*100;}
 const years=Object.keys(byY).sort();
 const M=['J','F','M','A','M','J','J','A','S','O','N','D'];
 const cell=v=>{if(v==null)return '<td style="background:var(--panel2);color:var(--dim)">&middot;</td>';
  const a=Math.min(1,Math.abs(v)/14);
  const bg=v>=0?'rgba(63,224,138,'+(0.10+0.55*a).toFixed(2)+')':'rgba(255,93,108,'+(0.10+0.55*a).toFixed(2)+')';
  return '<td style="background:'+bg+'" title="'+v.toFixed(2)+'%">'+v.toFixed(0)+'</td>';};
 const yr=y=>{const vs=Object.values(byY[y]).filter(v=>v!=null);
  return vs.length?(vs.reduce((a,b)=>a*(1+b/100),1)-1)*100:null;};
 const avg=[];for(let m=0;m<12;m++){const vs=years.map(y=>byY[y][m]).filter(v=>v!=null);
  avg.push(vs.length?vs.reduce((a,b)=>a+b,0)/vs.length:null);}
 el.innerHTML='<table class="rvseas"><thead><tr><th></th>'+M.map(m=>'<th>'+m+'</th>').join('')+'<th>YR</th></tr></thead><tbody>'
  +years.map(y=>'<tr><td class="y">'+y+'</td>'+M.map((_,m)=>cell(byY[y][m])).join('')
   +'<td class="tot" style="color:'+mtCol(yr(y))+'">'+(yr(y)==null?'-':yr(y).toFixed(0))+'</td></tr>').join('')
  +'<tr><td class="y">AVG</td>'+avg.map(cell).join('')+'<td class="tot"></td></tr></tbody></table>'
  +'<p class="rvfoot">Numbers are printed in every cell, so colour is a reading aid rather than the signal. The YR column compounds the year. Past calendar patterns are not a forecast.</p>';}

/* ---- ABOUT ---- */
function rvAboutPanel(st){
 const el=document.getElementById('rvb-about');
 if(!st.about){el.innerHTML='<div class="mtempty">No business description published for this symbol.</div>';return;}
 el.innerHTML='<p class="rvabout">'+mtEsc(st.about)+'</p><div class="rvchips">'
  +[['Sector',st.sector],['Industry',st.industry],['Country',st.country],
    ['Employees',st.employees?Number(st.employees).toLocaleString('en-US'):'']]
   .filter(x=>x[1]).map(x=>'<span class="rvchip">'+x[0]+': '+mtEsc(x[1])+'</span>').join('')+'</div>'
  +'<p class="rvfoot">Every figure on this page is fetched and computed in your browser from free public sources. Fundamental history on the free tier runs about four fiscal years and five quarters. Best-effort and unaudited - market research, not investment advice.</p>';}

/* ===================== NEWS ===================== */
const NW_FEEDS=[
 ['SA','Seeking Alpha','https://seekingalpha.com/market_currents.xml'],
 ['MW','MarketWatch','https://feeds.marketwatch.com/marketwatch/topstories/'],
 ['YF','Yahoo Finance','https://finance.yahoo.com/news/rssindex'],
 ['INV','Investing.com','https://www.investing.com/rss/news_25.rss'],
 ['CNBC','CNBC','https://www.cnbc.com/id/100003114/device/rss/rss.html'],
 ['FT','Fed / macro','https://www.investing.com/rss/news_1.rss']];
const NW_TOPICS={
 'Central banks':['fed','ecb','boj','rate cut','rate hike','central bank','powell','lagarde','minutes'],
 'Inflation':['inflation','cpi','ppi','price index','disinflation'],
 'Earnings':['earnings','q1','q2','q3','q4','results','beats','misses','guidance','transcript'],
 'AI & semis':['ai','nvidia','chip','semiconductor','gpu','data center','openai','tsmc'],
 'Energy':['oil','crude','opec','gas','energy','refinery'],
 'Crypto':['bitcoin','crypto','ethereum','btc','token','stablecoin'],
 'M&A':['acquisition','merger','takeover','deal','buyout','ipo'],
 'Rates & bonds':['yield','treasury','bond','curve','credit spread']};
let NW={view:'wire',src:'all',topic:null,items:null,loading:false};

async function nwLoad(){
 if(NW.items||NW.loading)return NW.items;
 NW.loading=true;
 const got=await Promise.all(NW_FEEDS.map(async ([code,name,url])=>{
  try{const rs=await mtGet('/rss',{u:url});
   return rs.map(r=>({...r,src:code,srcName:name}));}
  catch(e){return [];}}));
 const seen={},items=[];
 [].concat(...got).forEach(it=>{
  const k=(it.title||'').toLowerCase().slice(0,60);
  if(!k||seen[k])return;seen[k]=1;items.push(it);});
 items.sort((a,b)=>(b.ts||0)-(a.ts||0));
 NW.items=items;NW.loading=false;
 return items;}

function nwAge(ts){if(!ts)return '';
 const m=Math.floor((Date.now()-ts)/60000);
 if(m<60)return Math.max(1,m)+'m';
 const h=Math.floor(m/60);return h<48?h+'h':Math.floor(h/24)+'d';}

function nwTopicOf(it){
 const s=((it.title||'')+' '+(it.summary||'')).toLowerCase();
 for(const k in NW_TOPICS){if(NW_TOPICS[k].some(w=>s.includes(w)))return k;}
 return null;}

async function mtRenderNews(){
 if(!mtSetupCard('mtSetupN'))return;
 const tabs=[['wire','Live wire'],['reports','Reports'],['daily','Daily wrap'],['topics','Topics']];
 document.getElementById('nwTabs').innerHTML=tabs.map(t=>
  '<button data-v="'+t[0]+'"'+(NW.view===t[0]?' class="on"':'')+'>'+t[1]+'</button>').join('');
 document.querySelectorAll('#nwTabs button').forEach(b=>b.onclick=()=>{NW.view=b.dataset.v;NW.topic=null;mtRenderNews();});
 const body=document.getElementById('nwBody'),filt=document.getElementById('nwFilt');
 if(NW.view==='daily'){filt.innerHTML='';nwDaily(body);return;}
 body.innerHTML='<div class="mtload">Loading the wire...</div>';
 const items=await nwLoad();
 if(!items.length){body.innerHTML='<div class="mtempty">No feeds reachable right now. They refresh every few minutes.</div>';filt.innerHTML='';return;}
 if(NW.view==='topics'){
  const counts={};items.forEach(it=>{const t=nwTopicOf(it);if(t)counts[t]=(counts[t]||0)+1;});
  filt.innerHTML='<div class="nwfilt">'+Object.keys(NW_TOPICS).map(k=>
   '<button data-t="'+k+'"'+(NW.topic===k?' class="on"':'')+'>'+k+' <span style="color:var(--dim)">'+(counts[k]||0)+'</span></button>').join('')+'</div>';
  filt.querySelectorAll('button[data-t]').forEach(b=>b.onclick=()=>{NW.topic=NW.topic===b.dataset.t?null:b.dataset.t;mtRenderNews();});
 } else {
  filt.innerHTML='<div class="nwfilt"><button data-s="all"'+(NW.src==='all'?' class="on"':'')+'>All sources</button>'
   +NW_FEEDS.map(f=>'<button data-s="'+f[0]+'"'+(NW.src===f[0]?' class="on"':'')+'>'+f[1]+'</button>').join('')+'</div>';
  filt.querySelectorAll('button[data-s]').forEach(b=>b.onclick=()=>{NW.src=b.dataset.s;mtRenderNews();});}
 let list=items;
 if(NW.view==='topics'&&NW.topic)list=items.filter(it=>nwTopicOf(it)===NW.topic);
 else if(NW.view!=='topics'&&NW.src!=='all')list=items.filter(it=>it.src===NW.src);
 const withSum=NW.view==='reports';
 body.innerHTML=list.slice(0,80).map(it=>'<a class="nwrow" href="'+mtEsc(it.link)+'" target="_blank" rel="noopener">'
  +'<span class="ag">'+nwAge(it.ts)+'</span><span class="src">'+it.src+'</span>'
  +'<span class="bd"><span class="hd">'+mtEsc(it.title)+'</span>'
  +(withSum&&it.summary?'<span class="sm">'+mtEsc(it.summary)+'</span>':'')+'</span></a>').join('')
  +'<p class="mtnote">'+list.length+' headlines across '+NW_FEEDS.length+' public feeds, newest first, de-duplicated by headline. Every link opens the original publisher.</p>';}

function nwDaily(body){
 const M=DATA.market||{},S=M.setup||{},K=M.risk||{},I=M.indy||{};
 const hist=(DATA.history||[]);
 const today=hist.length?hist[hist.length-1]:null;
 body.innerHTML='<div class="mtc" style="margin-top:0"><div class="mtch">Daily wrap<span class="r">'
  +(M.asof||DATA.generated||'')+'</span></div>'
  +(I.read?'<p class="mtstory">'+mtEsc(I.read)+'</p>':'')
  +'<p class="mtstory">'+mtEsc(S.story||'')+'</p>'
  +'<div class="mkcols"><div><p class="mkcolh" style="color:var(--accent)">What is priced in</p><ul class="mkbul">'
   +((S.price_in||[]).map(x=>'<li>'+mtEsc(x)+'</li>').join('')||'<li>Snapshot still populating.</li>')+'</ul></div>'
  +'<div><p class="mkcolh" style="color:var(--gold)">What to watch</p><ul class="mkbul">'
   +((S.watch||[]).map(x=>'<li>'+mtEsc(x)+'</li>').join(''))+'</ul></div></div>'
  +(today?'<div class="mtlbl">Screen picks archived '+today.date+'</div>'
    +'<div>'+(today.picks||[]).slice(0,12).map(p=>'<span class="mtchip" data-go="'+p.sym+'">'+p.sym
      +' <span style="color:var(--dim)">'+p.score+'/7</span></span>').join('')+'</div>':'')
  +'<p class="mtnote">Composed from the market snapshot baked into this build - arithmetic over the session\u2019s numbers, not written prose. It updates when the screener next runs.</p></div>';
 mtWireSyms();}
'''

# ============================================================================ ANCHORS
ANCHOR_TABS = '<button data-tab="research">Research</button>'
ANCHOR_PORTFOLIO = '<button data-tab="magellan">Magellan - Portfolio</button>\n'
ANCHOR_SECTION_END = '<div id="mtSym"></div>\n</section>'
ANCHOR_CSS = '.disc .ok:hover{filter:brightness(1.08)}'
ANCHOR_TABJS = " if(b.dataset.tab==='research') mtRenderResearch();});"
TABJS_NEW = (" if(b.dataset.tab==='research') mtRenderResearch();\n"
             " if(b.dataset.tab==='news') mtRenderNews();});")
ANCHOR_END = "</script></body></html>"


def patch(src):
    if "MAGELLAN TERMINAL (client-side, via worker)" not in src:
        raise SystemExit("! run terminal_upgrade.py first - research_v2 builds on it.")
    steps = [
        ("news tab button",  ANCHOR_TABS,        ANCHOR_TABS + "\n" + HTML_NEWS_TAB),
        ("remove portfolio tab", ANCHOR_PORTFOLIO, ""),
        ("news section",     ANCHOR_SECTION_END, ANCHOR_SECTION_END + HTML_NEWS_SECTION),
        ("css",              ANCHOR_CSS,         ANCHOR_CSS + "\n" + CSS_BLOCK.strip()),
        ("tab switch",       ANCHOR_TABJS,       TABJS_NEW),
        ("js",               ANCHOR_END,         JS_BLOCK + "\n" + ANCHOR_END),
    ]
    for name, anchor, repl in steps:
        n = src.count(anchor)
        if n != 1:
            raise SystemExit(f"! anchor for '{name}' found {n} times (expected 1).")
        src = src.replace(anchor, repl, 1)
        print(f"  + {name}")
    return src


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "screener.py"
    if not os.path.exists(path):
        raise SystemExit(f"! {path} not found.")
    src = open(path, "r", encoding="utf-8").read()
    if MARK in src:
        print(f"{path} already has the research terminal - nothing to do.")
        return
    print(f"Patching {path} ...")
    out = patch(src)
    shutil.copyfile(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(out)
    compile(out, path, "exec")
    print("Done. Deploy worker_v2.js to Cloudflare as well - the new panels need its routes.")


if __name__ == "__main__":
    main()
