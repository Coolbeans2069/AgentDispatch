"""Command Centre home page: live brain background, main instruction chat, Ops side chat, projects sidebar, running log, completion steam gauge."""

HOME_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentDispatch - Command Centre</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:#02070d;color:#d6f6ff;font:13px/1.45 Segoe UI,system-ui,sans-serif}
#brainbg{position:fixed;inset:0;top:44px;border:0;width:100%;height:calc(100% - 44px);opacity:.38;pointer-events:none;filter:saturate(1.1) brightness(.85);z-index:0}
#veil{position:fixed;inset:0;top:44px;z-index:1;background:radial-gradient(ellipse at 50% 40%,rgba(2,10,18,.35),rgba(2,7,13,.78) 70%);pointer-events:none}
#cc{position:fixed;inset:0;top:44px;z-index:2;display:grid;grid-template-columns:260px minmax(0,1fr) 320px;grid-template-rows:1fr 118px;gap:10px;padding:10px;pointer-events:none}
#cc>*{pointer-events:auto}
.panel{background:rgba(2,14,23,.78);border:1px solid rgba(0,229,255,.28);border-radius:4px;backdrop-filter:blur(8px);display:flex;flex-direction:column;min-height:0;box-shadow:0 0 18px rgba(0,229,255,.08)}
.phd{display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid rgba(0,229,255,.2);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#ff9a1a;flex-shrink:0}
.phd .sp{flex:1}.phd .chip{letter-spacing:.04em;text-transform:none;font-size:10px;padding:2px 8px;border-radius:2px;border:1px solid rgba(0,229,255,.35);color:#9be9ff;background:rgba(0,229,255,.06)}
#ops{grid-column:1;grid-row:1}#main{grid-column:2;grid-row:1}#proj{grid-column:3;grid-row:1}
#logwrap{grid-column:1;grid-row:2}#gauge{grid-column:3;grid-row:2}#statusbar{grid-column:2;grid-row:2}
#msgs,#omsgs,#plog{flex:1;overflow:auto;padding:10px 12px;min-height:0}
.msg{max-width:94%;margin:6px 0;padding:8px 11px;border-radius:3px;white-space:pre-wrap;border:1px solid rgba(0,229,255,.2);background:rgba(0,16,26,.72)}
.msg.user{margin-left:auto;background:rgba(0,60,90,.45);border-color:rgba(0,229,255,.45)}
.msg.assistant{border-color:rgba(255,154,26,.35)}.msg.auto{border-style:dashed;color:#9fb6cc}.msg.sys{border-style:dashed;color:#6aa9bd;font-size:12px}
.msg small{display:block;color:#6aa9bd;font-size:10px;margin-top:4px}
#bar,#obar{padding:8px 10px;border-top:1px solid rgba(0,229,255,.2);flex-shrink:0;background:rgba(0,10,18,.55)}
#bar .row,#obar .row{display:flex;gap:6px;align-items:flex-end}
textarea{flex:1;resize:none;min-height:48px;max-height:120px;background:rgba(0,10,18,.85);border:1px solid rgba(0,229,255,.28);border-radius:2px;color:#d6f6ff;padding:6px 8px;font:inherit}
button{font:inherit;cursor:pointer;background:rgba(0,229,255,.07);border:1px solid rgba(0,229,255,.5);border-radius:2px;color:#aeefff;text-transform:uppercase;letter-spacing:.08em;font-size:11px;font-weight:600;padding:6px 10px}
button:hover:not(:disabled){background:rgba(0,229,255,.2);box-shadow:0 0 12px rgba(0,229,255,.45);color:#fff}
button:disabled{opacity:.35;cursor:not-allowed}
button.go{border-color:#19f0c8;color:#19f0c8;background:rgba(25,240,200,.08)}
button.go:hover:not(:disabled){background:rgba(25,240,200,.22);box-shadow:0 0 12px rgba(25,240,200,.5)}
.muted{color:#6aa9bd}.bad{color:#ff9b93}.ok2{color:#7bf0c4}
.jobrow,.prow{display:flex;flex-direction:column;gap:6px;padding:8px 9px;margin:5px 0;border:1px solid rgba(0,229,255,.18);border-radius:3px;background:rgba(0,16,26,.5)}
.jobrow{cursor:pointer}.jobrow:hover,.prow:hover,.prow.sel{border-color:#00e5ff;box-shadow:0 0 10px rgba(0,229,255,.25)}
.prow.sel{background:rgba(0,60,90,.35);border-color:#ff9a1a;box-shadow:0 0 12px rgba(255,154,26,.25)}
.jobrow .t,.prow .t{font-weight:600;color:#d6f6ff}.jobrow .m,.prow .m{font-size:11px;color:#6aa9bd;word-break:break-all}
.prow .acts{display:flex;flex-wrap:wrap;gap:4px}
.prow .acts button,.prow .acts a{padding:3px 7px;font-size:10px;letter-spacing:.04em;text-decoration:none;display:inline-flex;align-items:center}
.prow .acts a.chip{color:#9be9ff}
.prow .bar{height:4px;background:rgba(0,229,255,.12);border-radius:2px;overflow:hidden}.prow .bar u{display:block;height:100%;background:#00e5ff;box-shadow:0 0 6px #00e5ff}
.pill{display:inline-block;padding:0 7px;border-radius:9px;font-size:10px;border:1px solid currentColor;margin-left:4px}
.done,.merged,.delivered{color:#7bf0c4}.running,.pending,.waiting,.queued,.planning,.reviewing,.ready{color:#ffd34d}.failed,.blocked,.cancelled{color:#ff9b93}
#starter{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-bottom:1px solid rgba(0,229,255,.15)}
#starter button{text-transform:none;letter-spacing:.02em;font-weight:500;font-size:11px;padding:5px 9px;border-color:rgba(255,154,26,.45);color:#ffc27a}
#bpmini{padding:8px 12px;border-bottom:1px solid rgba(0,229,255,.15);max-height:160px;overflow:auto;font-size:12px}
#bpmini .sec{margin:4px 0}#bpmini b{color:#ff9a1a}
#actions{display:flex;gap:6px;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid rgba(0,229,255,.15)}
#logwrap #plog{font:11px/1.4 ui-monospace,Consolas,monospace;color:#7fbfd4}
#plog div{margin:1px 0;border-left:2px solid rgba(0,229,255,.25);padding-left:6px}
#plog div.warn{border-color:#ff9a1a;color:#ffc27a}#plog div.err{border-color:#ff6b5e;color:#ff9b93}#plog div.ok{border-color:#19f0c8;color:#7ff5dc}
#gauge{align-items:center;justify-content:center;padding:8px;position:relative}
#steam{width:100px;height:100px}
#gread{position:absolute;bottom:10px;left:0;right:0;text-align:center;font-size:11px;letter-spacing:.12em;color:#ff9a1a;text-transform:uppercase}
#statusbar{padding:10px 14px;display:flex;flex-direction:column;gap:6px;justify-content:center}
#statusbar .row2{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.chip{padding:2px 9px;border-radius:2px;background:rgba(0,229,255,.06);border:1px solid rgba(0,229,255,.28);font-size:11px;color:#9be9ff}
#toast{position:fixed;left:50%;top:56px;transform:translateX(-50%);background:rgba(0,26,40,.97);border:1px solid #00e5ff;padding:10px 16px;border-radius:2px;display:none;max-width:min(520px,90vw);z-index:90;box-shadow:0 0 18px rgba(0,229,255,.45);text-align:center}
#drop{position:absolute;inset:0;background:rgba(2,18,27,.88);border:2px dashed #00e5ff;display:none;align-items:center;justify-content:center;font-size:18px;color:#00e5ff;z-index:5}
#main{position:relative}
@media(max-width:1100px){#cc{grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr 100px}#ops{grid-column:1;grid-row:1}#proj{grid-column:2;grid-row:1}#main{grid-column:1/-1;grid-row:2}#logwrap{grid-column:1;grid-row:3}#gauge{grid-column:2;grid-row:3}#statusbar{display:none}#brainbg{opacity:.22}}
@media(max-width:720px){#cc{grid-template-columns:1fr;grid-template-rows:auto}#ops,#proj,#logwrap,#gauge{display:none}#main{grid-column:1;grid-row:1;min-height:70vh}}
</style></head><body>
<iframe id="brainbg" src="about:blank" title="live brain" tabindex="-1"></iframe>
<div id="veil"></div>
<div id="cc">
  <aside id="ops" class="panel">
    <div class="phd">Ops <span class="chip" title="live course corrections while jobs run">live</span><span class="sp"></span><span id="jcount" class="chip">0 jobs</span></div>
    <div id="jlist" style="max-height:38%;overflow:auto;padding:6px 8px;border-bottom:1px solid rgba(0,229,255,.15)"></div>
    <div id="omsgs"></div>
    <div id="obar"><div class="row"><textarea id="otxt" placeholder="Course correction… (change a requirement, nudge a running job)"></textarea><button id="osend" class="go">Send</button></div></div>
  </aside>
  <section id="main" class="panel">
    <div class="phd">Command Centre <span id="phase" class="chip">interview</span><span class="sp"></span>
      <label class="chip" title="ready plans run themselves inside the projects root"><input type="checkbox" id="autoc"> Autonomous</label>
      <span id="usage" class="chip">usage…</span>
    </div>
    <div id="starter">
      <button data-p="Make a vertical short video about …">Video short</button>
      <button data-p="Build a local website for … that runs offline">Website</button>
      <button data-p="Design a laser-cut / FreeCAD mechanical part: …">CAD / part</button>
      <button data-p="Concept a small UAV / model airplane for mission …">Airplane</button>
      <button data-p="Concept a car / vehicle for … with targets …">Car concept</button>
      <button data-p="Commercial product pack: name, sell sheet and packaging for …">Commercial</button>
      <button data-p="I want to …">Blank</button>
    </div>
    <div id="actions">
      <button id="think" class="go" title="study, design, plan, then a second model attacks it">Think it through</button>
      <button id="mkplan" class="go" disabled>Create jobs</button>
      <a class="chip" href="/intake" style="text-decoration:none;align-self:center">Classic intake</a>
      <a class="chip" href="/agency" style="text-decoration:none;align-self:center">Agency</a>
      <span id="pmsg" class="muted"></span>
    </div>
    <div id="bpmini"></div>
    <div id="drop">Drop files to attach</div>
    <div id="msgs"></div>
    <div id="bar">
      <div style="margin-bottom:6px;font-size:11px" class="muted">Guide: <select id="mode"><option value="command">command centre (playbook + local/strong)</option><option value="architect">strong architect</option><option value="local">quick local</option></select>
        <button id="mic" title="speak">&#127908;</button><button id="att" title="attach">&#128206;</button>
      </div>
      <div class="row"><textarea id="txt" placeholder="Describe what you want built, fixed or made… Enter sends, Shift+Enter newline"></textarea><button id="send" class="go">Send</button></div>
      <input type="file" id="fi" multiple style="display:none"><div id="recstat" class="muted"></div>
    </div>
  </section>
  <aside id="proj" class="panel">
    <div class="phd">Projects <span id="pselchip" class="chip" style="display:none"></span><span class="sp"></span>
      <button id="popenroot" title="open projects root in Explorer" style="padding:3px 7px">Root</button>
      <button id="prefresh" style="padding:3px 8px">Refresh</button></div>
    <div id="proot" class="muted" style="padding:6px 12px;font-size:11px"></div>
    <div id="pdetail" style="display:none;padding:6px 10px;border-bottom:1px solid rgba(0,229,255,.15);font-size:11px"></div>
    <div id="plist" style="flex:1;overflow:auto;padding:6px 8px"></div>
  </aside>
  <div id="logwrap" class="panel"><div class="phd">Program log</div><div id="plog"></div></div>
  <div id="statusbar" class="panel">
    <div class="row2"><span id="sum" class="chip">Command Centre — start to finish</span><span id="sidchip" class="chip">no session</span></div>
    <div class="row2 muted" id="hint">Paste your owner key (Key, top right), then describe a job or pick a starter.</div>
  </div>
  <div id="gauge" class="panel">
    <canvas id="steam" width="100" height="100"></canvas>
    <div id="gread">0% complete</div>
  </div>
</div>
<div id="toast"></div>
<script>
/*__PROJECTS_BOOT__*/
const $=s=>document.querySelector(s);
const el=(t,a,...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="class")e.className=v;else if(x.startsWith("on"))e[x]=v;else if(v===false||v==null){}else e.setAttribute(x,v===true?"":v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c==null?"":String(c))));return e};
let TOKEN=localStorage.getItem("sb_owner_token")||"",SID=(new URLSearchParams(location.search).get("s")||sessionStorage.getItem("sb_home")||sessionStorage.getItem("sb_intake")||""),S=null,BUSY=false,OPS_BUSY=false,LOG=[],PCT=0,PROJECTS=[],SEL_PROJ=null,PROJECTS_ROOT="",BRAIN_ON=false;
addEventListener("sb-token",()=>{TOKEN=localStorage.getItem("sb_owner_token")||""});
const toast=m=>{const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",6000)};
function log(line,kind){LOG.push({t:Date.now(),line:String(line).slice(0,240),kind:kind||""});if(LOG.length>80)LOG=LOG.slice(-80);const box=$("#plog");if(!box)return;box.replaceChildren(...LOG.slice(-40).map(x=>el("div",{class:x.kind||""},new Date(x.t).toLocaleTimeString()+"  "+x.line)));box.scrollTop=1e9}
function applyBoot(r){if(!r||!r.ok)return false;PROJECTS_ROOT=r.root||PROJECTS_ROOT;PROJECTS=r.projects||[];return true}
async function call(path,body,raw){const h={};if(TOKEN)h.Authorization="Bearer "+TOKEN;const ctrl=typeof AbortController!=="undefined"?new AbortController():null;
 // Chat / think / plan hit local or remote models — 12s was aborting Continue with no useful reason.
 const slow=/\/(chat|think|plan_submit|ops|voice|analyze|blueprint)\b/.test(path)||(raw!==undefined);
 const ms=slow?600000:20000;
 const timer=ctrl?setTimeout(()=>ctrl.abort(),ms):null;
 let opt={headers:h,cache:"no-store",signal:ctrl?ctrl.signal:undefined};if(raw!==undefined){opt.method="POST";opt.body=raw}else if(body!==undefined){opt.method="POST";h["Content-Type"]="application/json";opt.body=JSON.stringify(body)}
 try{const r=await fetch(path,opt);const j=await r.json().catch(()=>({}));if(!r.ok||j.ok===false){const e=new Error(j.message||j.error||("HTTP "+r.status));e.data=j;throw e}return j}
 catch(e){if(e&&(e.name==="AbortError"||/aborted/i.test(String(e.message||e)))){const err=new Error(slow?"Timed out waiting for the guide (slow model). Press Send again — do not refresh.":"Request timed out.");err.cause=e;throw err}throw e}
 finally{if(timer)clearTimeout(timer)}
}
function need(){if(!TOKEN){toast("Enter your owner key first (Key button, top right). Folder open and chat need a key.");return false}return true}
function startBrain(){if(BRAIN_ON)return;BRAIN_ON=true;const f=$("#brainbg");if(f)f.src="/brain?embed=1"}

/* ---- steam gauge ---- */
function drawGauge(pct){PCT=Math.max(0,Math.min(100,pct|0));const c=$("#steam"),ctx=c.getContext("2d"),w=c.width,h=c.height,cx=w/2,cy=h/2+4,R=38;
 ctx.clearRect(0,0,w,h);
 ctx.beginPath();ctx.arc(cx,cy,R,0,Math.PI*2);ctx.strokeStyle="rgba(0,229,255,.2)";ctx.lineWidth=8;ctx.stroke();
 const a0=-Math.PI*0.75,a1=a0+Math.PI*1.5*(PCT/100);
 ctx.beginPath();ctx.arc(cx,cy,R,a0,a1);ctx.strokeStyle=PCT>=100?"#19f0c8":PCT>=60?"#00e5ff":"#ff9a1a";ctx.lineWidth=8;ctx.lineCap="round";ctx.shadowColor=ctx.strokeStyle;ctx.shadowBlur=10;ctx.stroke();ctx.shadowBlur=0;
 ctx.fillStyle="#d6f6ff";ctx.font="700 18px Segoe UI";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(PCT+"%",cx,cy-2);
 ctx.fillStyle="#6aa9bd";ctx.font="10px Segoe UI";ctx.fillText("PROJECT",cx,cy+16);
 $("#gread").textContent=PCT+"% complete"}

function completionPct(s){const ps=s&&s.plan_status||{};const vals=Object.values(ps);if(!vals.length){const ph=s&&s.phase||"interview";const order=["interview","study","design","plan","review","revise","ready","done"];const i=Math.max(0,order.indexOf(ph));return Math.round((i/Math.max(1,order.length-1))*35)}
 const done=vals.filter(p=>["merged","delivered","accepted"].includes(p.state)).length;const fail=vals.filter(p=>["failed","blocked","cancelled"].includes(p.state)).length;
 return Math.round(100*done/vals.length)}

/* ---- session draw ---- */
async function start(){try{await loadProjects(false)}catch(e){log(e.message,"err")}
 setTimeout(startBrain,400);
 if(!TOKEN){$("#msgs").replaceChildren(el("div",{class:"msg sys"},"Press Key (top right) and paste your owner key to chat, continue projects, and open folders in Explorer. Projects on the right should already be listed."));$("#hint").textContent="Owner key needed for Continue / Folder / chat.";return}
 try{if(SID){try{S=(await call("/api/intake/"+SID)).session}catch(e){SID=""}}
  if(!SID||!S||S.status!=="open"){S=(await call("/api/intake/session",{})).session;SID=S.id;sessionStorage.setItem("sb_home",SID);sessionStorage.setItem("sb_intake",SID);log("session "+SID,"ok")}
  $("#sidchip").textContent=SID;draw(true);poll();loadAgency()}catch(e){toast(e.message);log(e.message,"err")}}

function draw(first){if(!S)return;
 const m=$("#msgs"),n=m.children.length;if((S.messages||[]).length!==n||first){m.replaceChildren(...(S.messages||[]).map(x=>el("div",{class:"msg "+x.role+(x.auto?" auto":"")},x.text,x.t?el("small",{},new Date(x.t*1000).toLocaleTimeString()):"")));
  if(!(S.messages||[]).length&&first)m.append(el("div",{class:"msg assistant"},"Command Centre online. Pick a starter above or describe what you want — video, website, CAD, airplane study, commercial pack, anything the agency can plan. I will walk you start to finish."));
  m.scrollTop=1e9}
 const om=$("#omsgs");om.replaceChildren(...(S.ops_messages||[]).map(x=>el("div",{class:"msg "+x.role},x.text)));if(!(S.ops_messages||[]).length)om.append(el("div",{class:"msg sys"},"Ops stream: once jobs are running, send live changes here."));om.scrollTop=1e9;
 const ph=S.phase||"interview",run=S.work&&S.work.state==="running";$("#phase").textContent=(run?"working · ":"")+ph;
 drawBp();drawJobs();drawGauge(completionPct(S));
 const rd=S.readiness;$("#think").disabled=!!run||!((S.blueprint||{}).requirements||[]).length;$("#mkplan").disabled=!(rd&&rd.ok)||S.status!=="open";
 const jobs=((S.blueprint||{}).plan||{}).jobs||[];$("#mkplan").textContent="Create the "+(jobs.length||"")+" job"+(jobs.length===1?"":"s");
 $("#hint").textContent=run?"Architect is working…":(rd&&rd.ok)?"Blueprint ready — Create jobs when you are happy.":"Describe the work, answer questions, then Think it through."}

function drawBp(){const bp=S.blueprint||{},rd=S.readiness,out=[];
 if(bp.summary)out.push(el("div",{class:"sec"},el("b",{},bp.title||"Summary")," — ",bp.summary.slice(0,220)));
 if(rd){(rd.blockers||[]).slice(0,3).forEach(b=>out.push(el("div",{class:"bad"},"✖ "+b)));if(rd.ok)out.push(el("div",{class:"ok2"},"✔ Ready"))}
 if((bp.open_questions||[]).length)out.push(el("div",{class:"sec"},el("b",{},"Questions")," ",String(bp.open_questions.length)));
 const jobs=(bp.plan&&bp.plan.jobs)||[];if(jobs.length)out.push(el("div",{class:"sec"},el("b",{},"Plan")," "+jobs.length+" job(s)"));
 $("#bpmini").replaceChildren(...(out.length?out:[el("div",{class:"muted"},"Blueprint appears here as we talk.")]))}

function drawJobs(){const ps=S.plan_status||{},entries=Object.entries(ps);$("#jcount").textContent=entries.length+" jobs";
 const box=$("#jlist");if(!entries.length){box.replaceChildren(el("div",{class:"muted",style:"padding:6px;font-size:11px"},"No agency jobs yet."));return}
 box.replaceChildren(...entries.map(([k,p])=>el("div",{class:"jobrow",onclick:()=>location.href="/agency"},el("div",{},el("span",{class:"pill "+p.state},p.state),el("div",{class:"t"},p.job+" · "+(p.title||k)),el("div",{class:"m"},p.why||k)))))}

/* ---- chat ---- */
async function send(text){if(!need())return;text=(text||$("#txt").value).trim();if(!text||BUSY)return;if(!SID)await start();BUSY=true;$("#send").disabled=true;
 S.messages=S.messages||[];S.messages.push({role:"user",text,t:Date.now()/1000});$("#txt").value="";draw(false);$("#msgs").append(el("div",{class:"msg assistant auto"},"thinking…"));$("#msgs").scrollTop=1e9;log("you: "+text.slice(0,80));
 try{const r=await call("/api/intake/"+SID+"/chat",{text,mode:$("#mode").value});S=r.session;draw(false);poll();log("guide replied","ok")}catch(e){toast(e.message);log(e.message,"err");try{S=(await call("/api/intake/"+SID)).session;draw(false)}catch(x){}}
 BUSY=false;$("#send").disabled=false}
$("#send").onclick=()=>send();$("#txt").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send()}});
document.querySelectorAll("#starter button").forEach(b=>b.onclick=()=>{$("#txt").value=b.dataset.p;$("#txt").focus();const i=b.dataset.p.indexOf("…");if(i>=0)$("#txt").setSelectionRange(i,i+1)});

async function sendOps(){if(!need())return;const text=$("#otxt").value.trim();if(!text||OPS_BUSY)return;if(!SID)await start();OPS_BUSY=true;$("#osend").disabled=true;$("#otxt").value="";
 try{const r=await call("/api/intake/"+SID+"/ops",{text});S=r.session;draw(false);log("ops: "+text.slice(0,60),"ok")}catch(e){toast(e.message);log(e.message,"err")}OPS_BUSY=false;$("#osend").disabled=false}
$("#osend").onclick=sendOps;$("#otxt").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendOps()}});

$("#think").onclick=async()=>{if(!need())return;try{S=(await call("/api/intake/"+SID+"/think",{})).session;draw(false);poll();toast("Thinking it through…");log("think started","ok")}catch(e){toast(e.message);log(e.message,"err")}};
$("#mkplan").onclick=async()=>{if(!need())return;const m=$("#pmsg");m.textContent="";try{let r;try{r=await call("/api/intake/"+SID+"/plan_submit",{})}catch(e){const t=((S.blueprint.plan||{}).jobs||[]).map(j=>j.target).filter(Boolean);
  if(/allowed project folders/.test(e.message)&&t.length&&confirm("Allow agents to work on copies of:\n"+[...new Set(t)].join("\n")+" ?")){for(const f of new Set(t))await call("/api/agency/allow_root",{path:f}).catch(()=>{});r=await call("/api/intake/"+SID+"/plan_submit",{})}else throw e}
 sessionStorage.removeItem("sb_home");sessionStorage.removeItem("sb_intake");toast(r.jobs.length+" job(s) created");log("created "+r.jobs.length+" jobs","ok");location.href="/agency"}catch(e){m.textContent=e.message;log(e.message,"err")}};

/* ---- attachments / mic ---- */
async function upload(file){if(!need())return;if(!SID)await start();toast("Attaching "+file.name+"…");try{await call("/api/intake/"+SID+"/upload?name="+encodeURIComponent(file.name)+"&mime="+encodeURIComponent(file.type||""),undefined,file);S=(await call("/api/intake/"+SID)).session;draw(false);poll();log("attached "+file.name,"ok")}catch(e){toast(e.message);log(e.message,"err")}}
$("#att").onclick=()=>$("#fi").click();$("#fi").onchange=async e=>{for(const f of e.target.files)await upload(f);e.target.value=""};
let dc=0;addEventListener("dragenter",e=>{e.preventDefault();dc++;$("#drop").style.display="flex"});addEventListener("dragleave",e=>{dc=Math.max(0,dc-1);if(!dc)$("#drop").style.display="none"});addEventListener("dragover",e=>e.preventDefault());
addEventListener("drop",async e=>{e.preventDefault();dc=0;$("#drop").style.display="none";for(const f of e.dataTransfer.files)await upload(f)});
let mr=null,mchunks=[],mt0=0,mtimer=null;
$("#mic").onclick=async()=>{if(!need())return;if(mr){mr.stop();return}try{const st=await navigator.mediaDevices.getUserMedia({audio:true});mchunks=[];mr=new MediaRecorder(st);mr.ondataavailable=e=>e.data.size&&mchunks.push(e.data);mt0=Date.now();
 mr.onstop=async()=>{st.getTracks().forEach(t=>t.stop());clearInterval(mtimer);const blob=new Blob(mchunks,{type:mr.mimeType||"audio/webm"});mr=null;$("#mic").textContent="\uD83C\uDFA4";$("#recstat").textContent="transcribing…";
  if(!SID)await start();try{const r=await call("/api/intake/"+SID+"/voice?name=voice.webm&mime="+encodeURIComponent(blob.type)+"&mode="+encodeURIComponent($("#mode").value),undefined,blob);S=r.session;draw(false)}catch(e){toast(e.message)}$("#recstat").textContent=""};
 mr.start();mtimer=setInterval(()=>{$("#mic").textContent="\u23F9 "+Math.round((Date.now()-mt0)/1000)+"s"},400)}catch(e){toast("Mic unavailable: "+e.message)}};

/* ---- projects / agency / autonomy ---- */
function jobCountLine(c){c=c||{};const parts=[];["running","failed","ready","queued","planning","reviewing","blocked","merged","delivered"].forEach(k=>{if(c[k])parts.push(c[k]+" "+k)});return parts.join(" · ")||"no agency jobs yet"}
function selectProject(p,opts){opts=opts||{};SEL_PROJ=p;sessionStorage.setItem("sb_project",p.path);paintProjects();paintProjectDetail();
 if(opts.continue!==false){const open=(p.jobs||[]).filter(j=>["running","failed","blocked","ready"].includes(j.state));
  const lines=["Continue work on "+(p.title||p.name)+".","TARGET FOLDER (required): "+p.path,"Kind: code. Do not invent another target path.",
   "Follow docs/PRODUCT_ROADMAP.md in that folder when it exists. Create agency jobs and implement — do not stop at chat."];
  if(open.length)lines.push("Open jobs: "+open.slice(0,6).map(j=>j.id+" ["+j.state+"] "+(j.title||"")).join("; "));
  else lines.push("No open jobs — propose the next slice of work or repair a failed step.");
  $("#txt").value=lines.join("\n");$("#txt").focus()}
 trackProjectJobs(p);drawGauge(p.pct||0);$("#gread").textContent=(p.pct||0)+"% · "+(p.name||"");
 $("#hint").textContent="Selected "+(p.title||p.name)+" — Continue in chat, Open folder, Track jobs, or Modify.";
 log("selected "+p.name,"ok")}
function paintProjectDetail(){const box=$("#pdetail"),chip=$("#pselchip");if(!SEL_PROJ){box.style.display="none";chip.style.display="none";return}
 chip.style.display="";chip.textContent=SEL_PROJ.name;box.style.display="block";
 const sess=(SEL_PROJ.sessions||[])[0];
 box.replaceChildren(
  el("div",{},el("b",{},SEL_PROJ.title||SEL_PROJ.name)),
  el("div",{class:"m"},SEL_PROJ.path),
  el("div",{class:"m"},jobCountLine(SEL_PROJ.job_counts)+(sess?" · session "+sess.id:""))
 )}
function trackProjectJobs(p){const jobs=p.jobs||[];$("#jcount").textContent=jobs.length+" · "+p.name;
 if(!jobs.length){$("#jlist").replaceChildren(el("div",{class:"muted",style:"padding:6px;font-size:11px"},"No open jobs for this project. Continue in chat to plan the next slice."));return}
 $("#jlist").replaceChildren(...jobs.map(j=>el("div",{class:"jobrow",onclick:()=>location.href="/agency"},
  el("div",{},el("span",{class:"pill "+j.state},j.state),el("div",{class:"t"},j.id+" · "+(j.title||"").slice(0,42)),el("div",{class:"m"},j.why||"")))))}
async function openFolder(path){path=path||PROJECTS_ROOT||"the projects root";
 if(!TOKEN){toast("Paste your owner key (Key, top right), then click Folder / Root again.");$("#proot").textContent="Need owner key to open Explorer: "+path;log("open blocked: no key","warn");return}
 try{const r=await call("/api/home/open_folder",{path});toast("Opened in Explorer: "+(r.path||path));log("open "+(r.path||path),"ok");$("#proot").textContent="Opened "+(r.path||path)}catch(e){toast("Could not open folder: "+e.message);$("#proot").textContent="Open failed: "+e.message;log(e.message,"err")}}
function modifyProject(p){selectProject(p,{continue:false});
 $("#txt").value="Modify project "+(p.title||p.name)+" at "+p.path+".\nI want to change: ";
 $("#txt").focus();const t=$("#txt");t.setSelectionRange(t.value.length,t.value.length);toast("Describe the change, then Send")}
async function continueProject(p){if(!TOKEN){toast("Paste your owner key first, then Continue.");return}
 selectProject(p,{continue:true});
 // Lock target to the real project folder BEFORE chat invents <projects_root>/continue-work-on-...
 try{if(!SID)await start();
  const form=Object.assign({},(S&&S.form)||{},{title:(p.title||p.name||"project").slice(0,100),target:p.path,kind:"code",
   goal:"Continue and complete "+(p.title||p.name)+" at "+p.path+" per docs/PRODUCT_ROADMAP.md when present."});
  S=(await call("/api/intake/"+SID+"/form",{form})).session;draw(false);
  toast("Continue ready — paste the full roadmap prompt if you have it, then Send. After the reply: Think it through → Make plan.");
  log("continue locked target "+p.path,"ok")}
 catch(e){toast(e.message);log(e.message,"err")}}
function paintProjects(){const box=$("#plist");if(!box)return;if(!PROJECTS.length){box.replaceChildren(el("div",{class:"muted"},"No project folders yet under "+(PROJECTS_ROOT||"the projects root")+"."));return}
 box.replaceChildren(...PROJECTS.map(p=>{
  const sel=SEL_PROJ&&SEL_PROJ.path===p.path;
  const acts=el("div",{class:"acts"},
   el("button",{class:"go",onclick:ev=>{ev.stopPropagation();continueProject(p)}},"Continue"),
   el("button",{onclick:ev=>{ev.stopPropagation();selectProject(p,{continue:false});toast("Selected "+p.name)}},"Select"),
   el("button",{onclick:ev=>{ev.stopPropagation();modifyProject(p)}},"Modify"),
   el("button",{onclick:ev=>{ev.stopPropagation();selectProject(p,{continue:false});trackProjectJobs(p);toast("Tracking "+p.name+" in Ops")}},"Track"),
   el("button",{onclick:ev=>{ev.stopPropagation();openFolder(p.path)}},"Folder"),
   el("a",{class:"chip",href:"/agency",title:"Agency job board",onclick:ev=>ev.stopPropagation()},"Agency")
  );
  const counts=jobCountLine(p.job_counts);
  return el("div",{class:"prow"+(sel?" sel":""),title:p.path,onclick:()=>selectProject(p,{continue:false})},
   el("div",{},el("div",{class:"t"},p.title||p.name),
    el("div",{class:"m"},p.name+(p.blurb?" — "+p.blurb.slice(0,70):"")),
    el("div",{class:"m"},(p.files||0)+" files · "+(p.mtime||"")+(counts?" · "+counts:"")),
    el("div",{class:"bar"},el("u",{style:"width:"+(p.pct||0)+"%"}))),
   acts)}))}
function finishProjectsPaint(){
 $("#proot").textContent=(PROJECTS_ROOT||"the projects root")+" — Continue / Select / Modify / Track / Folder";
 if(SEL_PROJ){SEL_PROJ=PROJECTS.find(p=>p.path===SEL_PROJ.path)||SEL_PROJ}
 else{const saved=sessionStorage.getItem("sb_project");if(saved)SEL_PROJ=PROJECTS.find(p=>p.path===saved)||null}
 paintProjects();paintProjectDetail();
 if(SEL_PROJ){trackProjectJobs(SEL_PROJ);drawGauge(SEL_PROJ.pct||0)}
 log((PROJECTS.length||0)+" project(s) under "+(PROJECTS_ROOT||"?"),"ok")}
async function loadProjects(force){const box=$("#plist");
 try{
  if(!force&&applyBoot(window.__PROJECTS_BOOT__)){finishProjectsPaint();setTimeout(()=>loadProjects(true),1500);return}
  if(window.__PROJECTS_BOOT_ERR__)log("boot err: "+window.__PROJECTS_BOOT_ERR__,"warn");
  if(box&&!PROJECTS.length)box.replaceChildren(el("div",{class:"muted"},"Loading projects…"));
  const r=await call("/api/home/projects?t="+Date.now());if(!applyBoot(r))throw new Error("bad projects payload");finishProjectsPaint();
 }catch(e){if(PROJECTS.length){log("refresh failed: "+e.message,"warn");finishProjectsPaint();return}
 if(box)box.replaceChildren(el("div",{class:"bad"},"Could not load projects: "+e.message),el("button",{onclick:()=>loadProjects(true),style:"margin-top:8px"},"Retry"));
 log(e.message,"err")}}
$("#prefresh").onclick=()=>{loadProjects(true);loadAgency()};
$("#popenroot").onclick=()=>openFolder(PROJECTS_ROOT||"the projects root");
async function loadAgency(){try{const a=await call("/api/agency"),c=a.counts||{};const tot=Object.values(c).reduce((x,y)=>x+y,0);log("agency: "+tot+" jobs · "+Object.entries(c).map(([k,v])=>v+" "+k).join(", "));
 if(SEL_PROJ){trackProjectJobs(SEL_PROJ);return}
 if(!S||!Object.keys(S.plan_status||{}).length){const jr=await call("/api/jobs?limit=12");const active=(jr.jobs||[]).filter(j=>!["merged","delivered","cancelled"].includes(j.state));
  if(active.length){$("#jlist").replaceChildren(...active.slice(0,8).map(j=>el("div",{class:"jobrow",onclick:()=>location.href="/agency"},el("div",{},el("span",{class:"pill "+j.state},j.state),el("div",{class:"t"},j.id+" · "+(j.title||"").slice(0,40))))));$("#jcount").textContent=active.length+" open"}}}catch(e){}}
async function loadAuto(){try{const a=await call("/api/autonomy"),u=a.usage;$("#autoc").checked=!!a.config.enabled;
 $("#usage").textContent="strong "+u.strong_calls_today+"/"+a.config.strong_calls_per_day+" · local "+u.local_share+"%"}catch(e){}}
$("#autoc").onchange=async e=>{if(!need()){e.target.checked=!e.target.checked;return}try{await call("/api/autonomy",{enabled:e.target.checked});toast(e.target.checked?"Autonomous ON":"Autonomous off");loadAuto();log("autonomous "+(e.target.checked?"on":"off"),"ok")}catch(x){e.target.checked=!e.target.checked;toast(x.message)}};

async function poll(){clearInterval(poll.t);poll.t=setInterval(async()=>{if(!SID||!TOKEN)return;const thinking=S&&((S.work&&S.work.state==="running")||(S.status==="submitted"&&S.phase!=="done"));
 const busy=S&&(S.files||[]).some(f=>["pending","running"].includes((f.analysis||{}).state));if(!busy&&!thinking)return;
 try{const s=(await call("/api/intake/"+SID)).session;if(JSON.stringify([s.messages.length,s.ops_messages,s.blueprint,s.thinking,s.work,s.phase,s.plan_status])!==JSON.stringify([S.messages.length,S.ops_messages,S.blueprint,S.thinking,S.work,S.phase,S.plan_status])){S=s;draw(false);if(thinking)log("phase "+s.phase)}}catch(e){}},2000)}

async function tick(){try{const ev=await call("/api/events?limit=8");(ev.events||[]).slice(-5).forEach(e=>{const line=(e.type||"event")+" "+(e.post||"")+" "+(e.actor||"");if(!tick.seen)tick.seen=new Set();const id=String(e.id||e.ts)+line;if(!tick.seen.has(id)){tick.seen.add(id);if(tick.seen.size>200)tick.seen=new Set([...tick.seen].slice(-100));log(line)}})}catch(e){}loadAgency()}
if(TOKEN){loadAuto();start()}else{drawGauge(0);log("waiting for owner key");start()}
setInterval(()=>{if(TOKEN)loadAuto()},20000);setInterval(()=>{if(TOKEN)tick()},12000);setInterval(loadProjects,60000);
addEventListener("sb-token",()=>{start();loadAuto()});
drawGauge(0);
</script></body></html>
"""
