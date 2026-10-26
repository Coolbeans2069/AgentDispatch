"""the /models page: every AI model found on this computer, what each is for, and where you choose which one to use. Plain HTML + JS; every value goes in as text."""

MODELS_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentDispatch - Models</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b1118;color:#d7e3ee;font:13px/1.45 Segoe UI,system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:#101a24;border-bottom:1px solid #1f3446;flex-wrap:wrap}
header h1{margin:0;font-size:15px;letter-spacing:.14em;color:#e0ae52}
.chip{padding:2px 9px;border-radius:10px;background:#16293a;border:1px solid #27465f;font-size:11px;color:#9fc4e0}.chip a{color:#8cc8ff;text-decoration:none}
input,select,button{font:inherit;color:inherit;background:#0e1822;border:1px solid #27465f;border-radius:5px;padding:5px 8px}
button{cursor:pointer;background:#17314a}button:hover{background:#1f4466}button:disabled{opacity:.4;cursor:not-allowed}button.go{background:#0f5a45;border-color:#1f8a6b}button.on{background:#2b5a85;border-color:#4a86b8}
.sp{flex:1}main{padding:14px 18px}
h3{margin:14px 0 6px;font-size:12px;letter-spacing:.1em;color:#e0ae52;text-transform:uppercase}
.use{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px}
.slot{border:1px solid #1f3446;border-radius:7px;padding:7px 10px;background:#0e1822}.slot.set{border-color:#2b7a5f;background:#0f2a24}.slot .f{color:#7f9bb3;font-size:11px}.slot .n{font-weight:600}.slot .by{font-size:10px;color:#7f9bb3}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
table{border-collapse:collapse;width:100%}td,th{padding:4px 8px;border-bottom:1px solid #1a2d3d;text-align:left;vertical-align:middle}th{color:#7f9bb3;font-weight:500;font-size:11px;position:sticky;top:0;background:#0b1118}
tr:hover td{background:#0f1c29}td.n{font-weight:600}.muted{color:#7f9bb3}.fav{color:#ffd34d;cursor:pointer}.act button{padding:2px 7px;font-size:11px;margin-right:3px}
.tag{display:inline-block;padding:0 6px;border-radius:8px;background:#16293a;font-size:10px;margin-right:3px}
#toast{position:fixed;right:16px;bottom:16px;background:#17314a;border:1px solid #4a86b8;padding:8px 12px;border-radius:6px;display:none;max-width:420px}
</style></head><body>
<header><h1>MODELS</h1><span id="summary" class="chip">loading...</span><span id="scanmsg" class="chip" style="display:none"></span><span class="sp"></span>
<button id="scan" title="known places and every drive, about a minute">Scan</button><button id="deep" title="walk every drive for up to 10 minutes">Deep scan</button>
<input id="root" placeholder="add a folder with models" size="28"><button id="addroot">Add</button>
<input id="tok" type="password" placeholder="owner token" size="18"><button id="savetok">Save</button></header>
<main><h3>In use - which model does what</h3><div class="use" id="use"></div>
<h3>Everything found</h3><div class="filters" id="filters"></div>
<div class="filters"><input id="q" placeholder="search name or folder" size="30"><select id="runner"><option value="">any runner</option></select><span class="muted" id="count"></span></div>
<table><thead><tr><th></th><th>Model</th><th>For</th><th>Size</th><th>Params</th><th>Stored as</th><th>Runs on</th><th>Where</th><th></th></tr></thead><tbody id="rows"></tbody></table></main>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const el=(t,a,...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="class")e.className=v;else if(x.startsWith("on"))e[x]=v;else if(v===false||v==null){}else e.setAttribute(x,v===true?"":v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c==null?"":String(c))));return e};
addEventListener("sb-token",()=>{TOKEN=localStorage.getItem("sb_owner_token")||"";load()});
let TOKEN=localStorage.getItem("sb_owner_token")||"",FN="",ST=null,MODELS=[];$("#tok").value=TOKEN;
const toast=m=>{const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",4500)};
async function api(path,body){const h={"Content-Type":"application/json"};if(TOKEN)h.Authorization="Bearer "+TOKEN;const r=await fetch(path,body===undefined?{headers:h}:{method:"POST",headers:h,body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));if(!r.ok||j.ok===false)throw new Error(j.message||j.error||("HTTP "+r.status));return j}
async function act(path,body,msg){if(!TOKEN){toast("Enter your owner token first (top right).");return}try{await api(path,body);toast(msg)}catch(e){toast("Refused: "+e.message)}await load()}
function slots(){const u=$("#use");u.replaceChildren();Object.entries(ST.functions).forEach(([f,label])=>{const s=ST.selection[f],n=ST.counts[f]||0;
 u.append(el("div",{class:"slot"+(s?" set":"")},el("div",{class:"f"},label+" ("+n+" found)"),el("div",{class:"n"},s?s.name:"nothing chosen: the AI picks"),el("div",{class:"by"},s?"chosen by "+(s.by==="user"?"you":"an AI")+" ":"best match is chosen automatically"),
  s?el("button",{onclick:()=>act("/api/models/select",{function:f,model_id:null},"Cleared")},"Clear"):""))})}
function filters(){const f=$("#filters");f.replaceChildren(el("button",{class:FN===""?"on":"",onclick:()=>{FN="";render()}},"All"));Object.entries(ST.functions).forEach(([k,l])=>{if(ST.counts[k])f.append(el("button",{class:FN===k?"on":"",onclick:()=>{FN=k;render()}},k+" "+ST.counts[k]))})}
function render(){filters();const q=$("#q").value.toLowerCase(),rn=$("#runner").value,rows=$("#rows");rows.replaceChildren();
 const list=MODELS.filter(m=>(!FN||m.function===FN)&&(!rn||(m.runners||[]).includes(rn))&&(!q||((m.label||m.name)+" "+m.path).toLowerCase().includes(q)));$("#count").textContent=list.length+" shown";
 list.slice(0,600).forEach(m=>rows.append(el("tr",{},el("td",{},el("span",{class:"fav",title:"favourite",onclick:()=>act("/api/models/override",{model_id:m.id,favorite:!m.favorite},"Updated")},m.favorite?"★":"☆")),
  el("td",{class:"n",title:m.name},(m.label||m.name)+(m.selected_for.length?" ✓":"")),el("td",{},el("span",{class:"tag"},m.function||m.modality),m.subtype&&!m.function?m.subtype:""),el("td",{},m.size_gb+" GB"),el("td",{},m.params_b?m.params_b+"B":""),
  el("td",{class:"muted"},m.format+(m.quant?" · "+m.quant:"")),el("td",{class:"muted"},(m.runners||[]).join(", ")),el("td",{class:"muted",title:m.path},m.source+" · "+m.path.slice(0,48)),
  el("td",{class:"act"},m.function?el("button",{class:"go",onclick:()=>act("/api/models/select",{function:m.function,model_id:m.id,by:"user"},"Now used for "+m.function)},"Use"):"",
   el("button",{onclick:()=>{const n=prompt("Name for this model",m.label||m.name);if(n!==null)act("/api/models/override",{model_id:m.id,label:n},"Renamed")}},"Rename"),el("button",{onclick:()=>act("/api/models/override",{model_id:m.id,hidden:true},"Hidden")},"Hide")))))}
async function load(){try{ST=await api("/api/models/status");MODELS=(await api("/api/models?limit=2000")).models;
 const s=ST.scan;$("#summary").textContent=ST.total+" models"+(ST.scanned?" · scanned "+new Date(ST.scanned*1000).toLocaleString():" · not scanned yet");
 const m=$("#scanmsg");m.style.display=s.running||s.message?"inline-block":"none";m.textContent=(s.running?"scanning: ":"")+(s.message||"");
 const rs=new Set();MODELS.forEach(x=>(x.runners||[]).forEach(r=>rs.add(r)));const sel=$("#runner"),cur=sel.value;sel.replaceChildren(el("option",{value:""},"any runner"),...[...rs].sort().map(r=>el("option",{value:r},r)));sel.value=cur;
 slots();render()}catch(e){$("#summary").textContent="broker not answering"}}
$("#q").oninput=render;$("#runner").onchange=render;
$("#savetok").onclick=()=>{TOKEN=$("#tok").value.trim();localStorage.setItem("sb_owner_token",TOKEN);toast(TOKEN?"Token saved in this browser":"Token cleared")};
$("#scan").onclick=async()=>{if(!TOKEN){toast("Enter your owner token first (top right).");return}try{const r=await api("/api/models/scan",{seconds:60,deep:true});toast(r.started?"Scan started":"A scan is already running")}catch(e){toast(e.message)}load()};
$("#deep").onclick=async()=>{if(!TOKEN){toast("Enter your owner token first (top right).");return}try{const r=await api("/api/models/scan",{seconds:600,deep:true});toast(r.started?"Deep scan started (up to 10 minutes)":"A scan is already running")}catch(e){toast(e.message)}load()};
$("#addroot").onclick=()=>{const p=$("#root").value.trim();if(p)act("/api/models/roots",{path:p,action:"add"},"Folder added: scan to include it")};
load();setInterval(()=>{if(!ST||ST.scan.running)load()},3000);setInterval(load,30000);
</script></body></html>
"""
