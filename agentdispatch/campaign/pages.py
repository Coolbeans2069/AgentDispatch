"""the Campaign, Tools and Library pages. Plain HTML + JS, every value from the server is inserted as text."""

HEAD = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentDispatch - %TITLE%</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1118;color:#d7e3ee;font:13px/1.45 Segoe UI,system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:#101a24;border-bottom:1px solid #1f3446;flex-wrap:wrap}header h1{margin:0;font-size:15px;letter-spacing:.14em;color:#e0ae52}
.chip{padding:2px 9px;border-radius:10px;background:#16293a;border:1px solid #27465f;font-size:11px;color:#9fc4e0}.sp{flex:1}
input,select,textarea,button{font:inherit;color:inherit;background:#0e1822;border:1px solid #27465f;border-radius:5px;padding:5px 8px}button{cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}
main{display:grid;grid-template-columns:minmax(380px,1.1fr) minmax(320px,1fr);gap:0;min-height:calc(100vh - 96px)}.col{padding:12px 16px;overflow:auto;max-height:calc(100vh - 96px)}.col+.col{border-left:1px solid #1f3446}
h3{margin:14px 0 6px;font-size:12px;letter-spacing:.1em;color:#e0ae52;text-transform:uppercase}h3:first-child{margin-top:0}
.card{padding:8px 11px;margin:6px 0;border:1px solid #1f3446;border-radius:6px;background:#0e1822}.card .t{font-weight:600}.card .m{color:#7f9bb3;font-size:11px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}.muted{color:#7f9bb3}.empty{color:#4f6b83}
.pill{display:inline-block;padding:0 8px;border-radius:9px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;margin-right:6px;border:1px solid currentColor}
.closed{color:#7bf0c4}.open{color:#00e5ff}.locked{color:#6a7f92}.candidate{color:#ffd34d}.ok{color:#7bf0c4}.bad{color:#ff9b93}.warn{color:#ffb25a}
pre{margin:0;padding:8px;background:#070c12;border:1px solid #1a2d3d;border-radius:6px;overflow:auto;max-height:300px;font:12px/1.4 Consolas,monospace;white-space:pre-wrap}
table{border-collapse:collapse;width:100%}td,th{padding:4px 8px;border-bottom:1px solid #1a2d3d;text-align:left;vertical-align:top}th{color:#7f9bb3;font-weight:500;font-size:11px}
textarea{width:100%}#toast{position:fixed;right:16px;bottom:16px;background:#17314a;border:1px solid #4a86b8;padding:8px 12px;border-radius:6px;display:none;max-width:460px;z-index:80}
.gate{border-left:4px solid var(--c)}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--c);margin-right:6px}
@media(max-width:900px){main{grid-template-columns:1fr}.col+.col{border-left:0;border-top:1px solid #1f3446}}
</style></head><body>"""

TAIL = r"""<div id="toast"></div><script>
const $=s=>document.querySelector(s);
const el=(t,a,...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="class")e.className=v;else if(x==="c")e.style.setProperty("--c",v);else if(x.startsWith("on"))e[x]=v;else if(v===false||v==null){}else e.setAttribute(x,v===true?"":v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c==null?"":String(c))));return e};
let TOKEN=localStorage.getItem("sb_owner_token")||"";addEventListener("sb-token",()=>{TOKEN=localStorage.getItem("sb_owner_token")||"";load&&load()});
const toast=m=>{const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",5000)};
async function api(path,body){const h={"Content-Type":"application/json"};if(TOKEN)h.Authorization="Bearer "+TOKEN;const r=await fetch(path,body===undefined?{headers:h}:{method:"POST",headers:h,body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));if(!r.ok||j.ok===false)throw new Error(j.message||j.error||("HTTP "+r.status));return j}
async function act(path,body,msg){if(!TOKEN){toast("Enter your owner key first (Key button, top right).");return null}try{const r=await api(path,body);toast(msg||"Done");await load();return r}catch(e){toast("Refused: "+e.message);return null}}
const when=t=>t?new Date(t*1000).toLocaleString():"";
%SCRIPT%
load();setInterval(load,8000);
</script></body></html>"""

CAMPAIGN_HTML = HEAD.replace("%TITLE%", "Campaign") + r"""
<header><h1>CAMPAIGN</h1><span id="sum" class="chip">loading...</span><span id="chain" class="chip"></span><span class="sp"></span><select id="csel"></select><button id="imp">Import status</button><button id="ex">Load example</button></header>
<main><div class="col"><h3>Gates</h3><div id="gates"></div></div><div class="col"><h3>Needs you</h3><div id="needs"></div><h3>Next to work on</h3><div id="next"></div>
<div id="imppanel" style="display:none"><h3>Import status lines</h3><p class="muted">Paste lines like <code>G06 = OPEN</code> (from a hand-off file). New gates are created, changed statuses are logged.</p><div class="row"><input id="impc" placeholder="campaign id, e.g. REL" size="16"></div><textarea id="impt" rows="8"></textarea><div class="row"><button id="impgo">Import</button></div></div>
<h3>Recent changes</h3><div id="log"></div></div></main>
""" + TAIL.replace("%SCRIPT%", r"""
let DATA=null,CUR="",TEMPLATES=[];
const colors={closed:"#7bf0c4",open:"#00e5ff",locked:"#6a7f92",candidate:"#ffd34d"};
function gate(cid,g){const c=colors[g.derived]||"#6a7f92",kids=[el("div",{class:"t"},el("span",{class:"pill "+g.derived},g.derived),g.title||g.id),el("div",{class:"m"},g.id+" = "+g.status+(g.depends_on.length?" · needs "+g.depends_on.join(", "):"")+(g.waiting_for.length?" · waiting for "+g.waiting_for.join(", "):""))];
 if(g.note)kids.push(el("div",{class:"m"},g.note.slice(0,200)));
 const row=el("div",{class:"row"});
 if(g.derived==="open"||g.derived==="candidate")row.append(el("button",{onclick:()=>startJob(cid,g)},"Start a job"));
 row.append(el("button",{onclick:()=>{const v=prompt("New status for "+g.id,g.status);if(v)act("/api/campaign/transition",{campaign:cid,gate:g.id,to:v,why:"set on the Campaign page"},"Status set")}},"Set status"));
 kids.push(row);return el("div",{class:"card gate",c},kids)}
function startJob(cid,g){const opts=TEMPLATES.map(t=>t.id+" - "+t.summary).join("\n");const t=prompt("Which template?\n\n"+opts+"\n\nType a template id:","primary-closure");if(!t)return;const tid=t.split(" ")[0];
 const f={gate:g.id,campaign:cid};if(tid==="independent-shadow"||tid==="result-bearing-compute"){const cut=prompt("Evidence cutoff (a commit) - leave empty if none","");if(cut)f.cutoff=cut}
 act("/api/jobs/from-template",{template:tid,fields:f},"Job created: open the Agency page")}
async function load(){try{DATA=await api("/api/campaign");TEMPLATES=DATA.templates;
 const sel=$("#csel");const prev=CUR||sel.value;sel.replaceChildren(...DATA.campaigns.map(c=>el("option",{value:c.id},c.id+" - "+c.title)));if(DATA.campaigns.length){CUR=DATA.campaigns.some(c=>c.id===prev)?prev:DATA.campaigns[0].id;sel.value=CUR}
 $("#chain").textContent=DATA.chain_ok&&DATA.evidence_chain_ok?"chains intact":"CHAIN BROKEN";$("#chain").style.color=DATA.chain_ok&&DATA.evidence_chain_ok?"":"#ff9b93";
 const c=DATA.campaigns.find(x=>x.id===CUR);$("#sum").textContent=c?c.counts.closed+" closed · "+c.counts.open+" open · "+c.counts.locked+" locked"+(c.counts.candidate?" · "+c.counts.candidate+" candidate":""):"no campaign yet";
 const g=$("#gates");g.replaceChildren();if(!c)g.append(el("p",{class:"empty"},"No campaign yet. Press Load example, or Import status to read the status lines you already keep."));else c.gates.forEach(x=>g.append(gate(c.id,x)));
 const n=$("#needs");n.replaceChildren();
 DATA.pending.forEach(p=>n.append(el("div",{class:"card"},el("div",{class:"t"},"Proposal "+p.id+": "+p.campaign+"/"+p.gate+" → "+p.to),el("div",{class:"m"},"from job "+(p.job||"?")+" by "+p.by+(p.claim?" · claim "+p.claim:"")+" · "+p.why),
  el("div",{class:"row"},el("button",{class:"go",onclick:()=>act("/api/campaign/decide",{id:p.id,approve:true},"Approved: the gate moved")},"Approve"),el("button",{onclick:()=>act("/api/campaign/decide",{id:p.id,approve:false,why:"rejected on the Campaign page"},"Rejected")},"Reject")))));
 DATA.waiting.filter(w=>w.needs_approval).forEach(w=>n.append(el("div",{class:"card"},el("div",{class:"t"},w.id+" "+w.title),el("div",{class:"m"},w.why),el("div",{class:"row"},el("button",{class:"go",onclick:()=>act("/api/campaign/approve_run",{job_id:w.id},"Approved to run")},"Approve to run")))));
 if(!n.children.length)n.append(el("p",{class:"empty"},"Nothing is waiting for you."));
 const nx=$("#next");nx.replaceChildren();const ng=c&&c.gates.find(x=>x.id===c.next);if(ng)nx.append(gate(c.id,ng));else nx.append(el("p",{class:"empty"},"Nothing is open: every gate is closed or waiting for another."));
 const w2=DATA.waiting.filter(w=>!w.needs_approval);if(w2.length){nx.append(el("h3",{},"Held back by a rule"));w2.forEach(w=>nx.append(el("div",{class:"card"},el("div",{class:"t"},w.id+" "+w.title),el("div",{class:"m"},w.why))))}
 const lg=await api("/api/campaign/log"+(CUR?"?campaign="+encodeURIComponent(CUR):""));const L=$("#log");L.replaceChildren();lg.log.slice().reverse().slice(0,25).forEach(r=>L.append(el("div",{class:"m"},when(r.t)+" · "+r.action+(r.gate?" "+r.gate:"")+(r.to?" → "+r.to:"")+(r.actor?" ("+r.actor+")":""))));
 }catch(e){$("#sum").textContent="broker not answering"}}
$("#csel").onchange=e=>{CUR=e.target.value;load()};
$("#imp").onclick=()=>{const p=$("#imppanel");p.style.display=p.style.display==="none"?"":"none";$("#impc").value=CUR||"C1"};
$("#impgo").onclick=()=>act("/api/campaign/import",{campaign:$("#impc").value.trim(),text:$("#impt").value},"Imported");
$("#ex").onclick=async()=>{try{const e=await api("/api/campaign/example");act("/api/campaign/load",{spec:e.example},"Example loaded: "+e.example.id)}catch(x){toast(x.message)}};
""")

TOOLS_HTML = HEAD.replace("%TITLE%", "Tools") + r"""
<header><h1>TOOLS</h1><span id="sum" class="chip">loading...</span><span class="sp"></span><button id="rescan">Rescan this computer</button></header>
<main><div class="col"><h3>What this computer can do for the models</h3><p class="muted">A model sends a request, this computer does the work, and only the short answer goes back: that is how subscription tokens are saved.</p><div id="tools"></div>
<h3>Install requests</h3><div id="inst"></div></div>
<div class="col"><h3>Try one</h3><div class="row"><select id="rt"><option value="calc">calc (Python)</option><option value="lean">lean (Lean 4)</option></select><button id="run" class="go">Run</button></div>
<textarea id="code" rows="7" placeholder="import sympy as sp&#10;x=sp.symbols('x')&#10;print(sp.integrate(sp.sin(x)**2,(x,0,sp.pi)))"></textarea><pre id="out" style="margin-top:6px">(output appears here)</pre>
<h3>Settings (owner)</h3><div class="row"><input id="troot" placeholder="a folder whose code may run (tests, builds)" size="34"><button id="addroot">Trust it</button></div><div id="roots" class="muted"></div>
<div class="row"><label><input type="checkbox" id="autopip"> let the program install missing tools by itself (each is staged and test-reviewed in isolation first)</label></div>
<h3>Recent runs</h3><div id="hist"></div><h3>Python environments found</h3><div id="pys" class="muted"></div></div></main>
""" + TAIL.replace("%SCRIPT%", r"""
let DATA=null;
function toolRow(t){const st=t.available?el("span",{class:"pill ok"},"ready"):el("span",{class:"pill warn"},"missing");
 return el("tr",{},el("td",{},el("span",{class:"dot",c:t.color}),t.name,t.custom?el("span",{class:"pill candidate"},"built"):""),el("td",{},st),el("td",{class:"muted"},t.available?(t.version||"")+" "+(t.path||"").slice(-40):t.note),el("td",{},String(t.runs||0)),
  el("td",{},(!t.available&&t.installable)?el("button",{onclick:()=>act("/api/tools/install",{tool:t.id,reason:"requested on the Tools page"},"Requested")},"Install"):"",""))}
async function load(){try{DATA=await api("/api/tools?history=15");const av=DATA.tools.filter(t=>t.available).length;$("#sum").textContent=av+" of "+DATA.tools.length+" ready";
 const tb=$("#tools");tb.replaceChildren(el("table",{},el("thead",{},el("tr",{},el("th",{},"Tool"),el("th",{},""),el("th",{},"Version / where"),el("th",{},"Runs"),el("th",{},""))),el("tbody",{},DATA.tools.map(toolRow))));
 const ins=$("#inst");ins.replaceChildren();if(!DATA.installs.length)ins.append(el("p",{class:"empty"},"No requests."));
 DATA.installs.slice().reverse().forEach(i=>ins.append(el("div",{class:"card"},el("div",{class:"t"},i.id+" "+i.name+" · "+i.state),el("div",{class:"m"},i.plan.kind+": "+(i.plan.packages||[i.plan.id||i.plan.url]).join(", ")+" from "+i.plan.source+" ("+i.plan.size+") · asked by "+i.requested_by+(i.reason?" · "+i.reason:"")),
  i.log?el("pre",{},i.log):"",i.state==="pending"?el("div",{class:"row"},el("button",{class:"go",onclick:()=>act("/api/tools/approve",{id:i.id},"Approved: installing")},"Approve"),el("button",{onclick:()=>act("/api/tools/deny",{id:i.id},"Denied")},"Deny")):"")));
 const rt=$("#roots");rt.textContent="Trusted: "+((DATA.config.trusted_roots||[]).concat(DATA.config.allowed_roots||[]).join(", ")||"none");$("#autopip").checked=(DATA.config.auto_install||[]).length>0;
 const h=$("#hist");h.replaceChildren();DATA.history.forEach(r=>h.append(el("div",{class:"m"},when(r.t)+" · "+r.tool+" "+(r.args||[]).join(" ").slice(0,50)+" · "+(r.ok?"ok":"failed")+" · "+r.secs+"s"+(r.caller?" · "+r.caller:""))));
 $("#pys").replaceChildren(...DATA.pythons.map(p=>el("div",{},p.path+" ("+p.version+") "+p.libs.join(", "))))}catch(e){$("#sum").textContent="broker not answering"}}
$("#rescan").onclick=()=>act("/api/tools/scan",{},"Scanned");
$("#run").onclick=async()=>{if(!TOKEN){toast("Enter your key first (Key button, top right).");return}$("#out").textContent="running...";try{const r=await api("/api/tools/run",{tool:$("#rt").value,code:$("#code").value});$("#out").textContent=(r.ok?"OK":"NOT OK")+" ("+r.secs+"s)\n"+(r.stdout||"")+(r.stderr?"\n--stderr--\n"+r.stderr:"")+((r.problems||[]).length?"\n"+r.problems.join("\n"):"");load()}catch(e){$("#out").textContent=e.message}};
$("#addroot").onclick=()=>{const v=$("#troot").value.trim();if(v)act("/api/tools/config",{trusted_roots:(DATA.config.trusted_roots||[]).concat([v])},"Folder trusted")};
$("#autopip").onchange=e=>act("/api/tools/config",{auto_install:e.target.checked?["pip","elan","winget"]:[]},"Saved");
""")

LIBRARY_HTML = HEAD.replace("%TITLE%", "Library") + r"""
<header><h1>LIBRARY</h1><span id="sum" class="chip">loading...</span><span class="sp"></span><input id="q" placeholder="search finished workflows" size="30"><select id="oc"><option value="">any outcome</option><option>ok</option><option>failed</option><option>blocked</option></select></header>
<main><div class="col"><h3>Categories</h3><div id="cats" class="row"></div><h3>Finished workflows</h3><div id="list"></div></div>
<div class="col"><div id="detail"><p class="empty">Pick a workflow to read how it was done. Every finished job is written down here with its route, steps, tools, commands and failure points.</p></div>
<h3>Recurring failure points</h3><div id="fails"></div><h3>Start something new</h3><div class="row"><input id="goal" placeholder="describe the new goal" style="flex:1"><button id="sug">What worked before?</button></div><div id="sugout"></div></div></main>
""" + TAIL.replace("%SCRIPT%", r"""
let DATA=null,CAT="";
function item(p){return el("div",{class:"card",onclick:()=>show(p.id),style:"cursor:pointer"},el("div",{class:"t"},el("span",{class:"pill "+(p.outcome==="ok"?"ok":p.outcome==="failed"?"bad":"warn")},p.outcome),p.title),el("div",{class:"m"},p.job+" · "+p.category+" · "+p.steps+" steps · "+p.failures+" failure point(s)"+(p.tools.length?" · tools: "+p.tools.join(", "):"")+" · "+when(p.finished)))}
async function show(id){try{const r=await api("/api/playbooks/"+encodeURIComponent(id)),p=r.playbook,d=$("#detail");d.replaceChildren(el("h3",{},p.job+" · "+p.outcome),el("div",{class:"t"},p.title),el("p",{class:"muted"},p.goal.slice(0,400)),
  el("h3",{},"Route"),...p.route.map(h=>el("div",{class:"m"},(h.kind||"")+" "+(h.step||"")+" → "+(h.model||h.agent)+" ("+h.lobe+") "+(h.outcome||"")+" "+(h.secs?h.secs+"s":"")+" · "+(h.why||""))),
  el("h3",{},"Steps"),...p.steps.map(s=>el("div",{class:"m"},s.n+". "+s.objective.slice(0,140)+" · "+(s.agent||"")+(s.tool?" · tool "+s.tool:"")+" · "+s.state)),
  p.tools.length?el("h3",{},"Local tool runs"):"",...p.tools.map(t=>el("div",{class:"m"},t.tool+" "+(t.args||[]).join(" ").slice(0,60)+" · "+(t.ok?"ok":"failed")+" · "+t.secs+"s")),
  p.failure_points.length?el("h3",{},"Failure points"):"",...p.failure_points.map(f=>el("div",{class:"m bad"},f.where+": "+f.reason)),
  p.lessons.length?el("h3",{},"Lessons"):"",...p.lessons.map(l=>el("div",{class:"m"},"• "+l)),el("h3",{},"Cost"),el("div",{class:"m"},p.cost.agent_runs+" model run(s) · "+p.cost.local_tool_runs+" local tool run(s)"))}catch(e){toast(e.message)}}
async function load(){try{const q=$("#q").value,oc=$("#oc").value;DATA=await api("/api/playbooks?q="+encodeURIComponent(q)+"&outcome="+oc+"&category="+encodeURIComponent(CAT)+"&limit=60");$("#sum").textContent=DATA.total+" workflows";
 $("#cats").replaceChildren(el("button",{class:CAT===""?"on":"",onclick:()=>{CAT="";load()}},"All"),...DATA.categories.map(c=>el("button",{class:CAT===c.category?"on":"",onclick:()=>{CAT=c.category;load()}},c.category+" "+c.total+(c.failed?" ("+c.failed+" failed)":""))));
 const l=$("#list");l.replaceChildren();if(!DATA.playbooks.length)l.append(el("p",{class:"empty"},"Nothing yet: workflows appear here as jobs finish."));DATA.playbooks.forEach(p=>l.append(item(p)));
 const f=await api("/api/playbooks/failures?category="+encodeURIComponent(CAT));$("#fails").replaceChildren(...(f.failures.length?f.failures.map(x=>el("div",{class:"card"},el("div",{class:"t"},x.count+"× "+x.kind),el("div",{class:"m"},x.reason+" · "+x.where.join(", ")+(x.agents.length?" · "+x.agents.join(", "):"")))):[el("p",{class:"empty"},"No failure points recorded.")]);
 }catch(e){$("#sum").textContent="broker not answering"}}
$("#q").oninput=()=>{clearTimeout(load.t);load.t=setTimeout(load,300)};$("#oc").onchange=load;
$("#sug").onclick=async()=>{const g=$("#goal").value.trim();if(!g)return;try{const r=await api("/api/playbooks/suggest?goal="+encodeURIComponent(g)),o=$("#sugout");o.replaceChildren();
 if(!r.similar_successes.length)o.append(el("p",{class:"empty"},"Nothing similar has worked yet."));r.similar_successes.forEach(s=>o.append(el("div",{class:"card",onclick:()=>show(s.id),style:"cursor:pointer"},el("div",{class:"t"},s.title+" ("+Math.round(s.score*100)+"% alike)"),el("div",{class:"m"},s.steps.map(x=>x.objective.slice(0,50)).join(" → ")),el("div",{class:"m"},"tools: "+(s.tools.join(", ")||"none")))));
 r.warnings.forEach(w=>o.append(el("div",{class:"m bad"},"Watch out ("+w.playbook+"): "+w.reason)))}catch(e){toast(e.message)}};
""")
