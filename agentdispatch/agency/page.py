"""the agency's web page (served at /agency): submit jobs, see their state and steps, read the diff, and - with the owner token - approve / reject / unblock / cancel / retry.
Plain HTML + JS, no libraries, every value from the server goes in as text (never as HTML)."""

AGENCY_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentDispatch - Agency</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b1118;color:#d7e3ee;font:13px/1.45 Segoe UI,system-ui,sans-serif}
header{display:flex;gap:14px;align-items:center;padding:10px 16px;background:#101a24;border-bottom:1px solid #1f3446;flex-wrap:wrap}
header h1{margin:0;font-size:15px;letter-spacing:.14em;color:#e0ae52}
.chip{padding:2px 9px;border-radius:10px;background:#16293a;border:1px solid #27465f;font-size:11px;color:#9fc4e0}
.chip.warn{background:#3a1c1c;border-color:#8a2f2f;color:#ffb0a8}.chip.ok{background:#0f2e26;border-color:#1f6b57;color:#8ff0cf}
header .sp{flex:1}
input,select,textarea,button{font:inherit;color:inherit;background:#0e1822;border:1px solid #27465f;border-radius:5px;padding:5px 8px}
button{cursor:pointer;background:#17314a}button:hover{background:#1f4466}button:disabled{opacity:.4;cursor:not-allowed}
button.go{background:#0f5a45;border-color:#1f8a6b}button.bad{background:#5a1f1f;border-color:#8a2f2f}
main{display:grid;grid-template-columns:360px 1fr;gap:0;min-height:calc(100vh - 96px)}
#left{border-right:1px solid #1f3446;padding:10px;overflow:auto;max-height:calc(100vh - 96px)}
#right{padding:14px 18px;overflow:auto;max-height:calc(100vh - 96px)}
.filters{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}.filters button.on{background:#2b5a85;border-color:#4a86b8}
.job{padding:7px 9px;margin:4px 0;border:1px solid #1f3446;border-radius:6px;cursor:pointer;background:#0e1822}.job:hover{border-color:#3a6a94}.job.sel{border-color:#e0ae52;background:#14212d}
.job .t{font-weight:600}.job .m{color:#7f9bb3;font-size:11px}
.st{display:inline-block;padding:0 7px;border-radius:9px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;margin-right:6px}
.st.queued,.st.planning{background:#2a3340;color:#9fb6cc}.st.running,.st.reviewing{background:#133a5c;color:#8cc8ff}
.st.ready{background:#4a3b0f;color:#ffd34d}.st.blocked{background:#4a2a0f;color:#ff9a3d}.st.failed,.st.cancelled{background:#4a1818;color:#ff9b93}
.st.merged,.st.delivered{background:#0f3d2e;color:#7bf0c4}
h2{margin:0 0 4px;font-size:16px}h3{margin:16px 0 6px;font-size:12px;letter-spacing:.1em;color:#e0ae52;text-transform:uppercase}
table{border-collapse:collapse;width:100%}td,th{padding:4px 8px;border-bottom:1px solid #1a2d3d;text-align:left;vertical-align:top}th{color:#7f9bb3;font-weight:500;font-size:11px}
pre{margin:0;padding:10px;background:#070c12;border:1px solid #1a2d3d;border-radius:6px;overflow:auto;max-height:420px;font:12px/1.4 Consolas,monospace;white-space:pre}
.add{color:#7bf0c4}.del{color:#ff9b93}.hunk{color:#8cc8ff}
.row{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}.why{color:#c9b98a}.muted{color:#7f9bb3}
.agents{display:flex;flex-wrap:wrap;gap:8px}.ag{padding:4px 9px;border:1px solid #1f3446;border-radius:6px;font-size:11px}.ag.off{opacity:.5}.ag i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;background:#3a4a5a}.ag.on i{background:#19f0c8}
#form{background:#0e1822;border:1px solid #27465f;border-radius:8px;padding:10px;margin-bottom:12px;display:none}#form label{display:block;margin:6px 0 2px;color:#7f9bb3;font-size:11px}
#form input,#form textarea,#form select{width:100%}#toast{position:fixed;right:16px;bottom:16px;background:#17314a;border:1px solid #4a86b8;padding:8px 12px;border-radius:6px;display:none;max-width:420px}
</style></head><body>
<header><h1>AGENCY</h1><span id="counts" class="chip">loading...</span><span id="kill" class="chip" style="display:none"></span><span id="merge" class="chip"></span>
<span class="sp"></span><button id="newbtn">+ New job</button><input id="tok" type="password" placeholder="owner token (kept in this browser)" size="26"><button id="savetok">Save</button></header>
<main><div id="left"><div id="form">
<b>New job</b>
<label>Start from a template (optional: it fills in the steps, the rubric and the safe defaults)</label><select id="f_tpl"><option value="">none: a plain job</option></select>
<div class="row" id="tplrow" style="display:none"><div style="flex:1"><label>Stage / gate</label><input id="t_gate" placeholder="e.g. G06"></div><div style="flex:1"><label>Campaign</label><input id="t_camp" placeholder="e.g. REL"></div></div>
<label>Title</label><input id="f_title" maxlength="120"><label>Goal (what should be true when it is done)</label><textarea id="f_goal" rows="4" maxlength="4000"></textarea>
<div class="row"><div style="flex:1"><label>Kind</label><select id="f_kind"><option>code</option><option>general</option><option>production</option></select></div>
<div style="flex:1"><label>Priority</label><select id="f_prio"><option value="1">normal</option><option value="0">low</option><option value="2">high</option><option value="3">urgent</option></select></div></div>
<label>Target folder (code jobs; must be an allowed project folder)</label><input id="f_target" placeholder="/path/to/projects/calc-demo">
<label>Acceptance check (a command that must pass, e.g. python -m pytest -q  or  python test_x.py)</label><input id="f_check">
<label>Review rubric (what a reviewer must see)</label><textarea id="f_rubric" rows="2" maxlength="1500"></textarea>
<label>Steps (optional, one per line; leave empty and an agent plans it)</label><textarea id="f_steps" rows="3"></textarea>
<details id="adv"><summary>Advanced: campaign, independence, scope, controls</summary>
<div class="row"><div style="flex:1"><label>Campaign</label><input id="m_camp"></div><div style="flex:1"><label>Stage / gate</label><input id="m_gate"></div></div>
<div class="row"><div style="flex:1"><label>Role</label><select id="m_role"><option>primary</option><option>adversarial</option><option>verifier</option><option>reviewer</option><option>provenance</option><option>governor</option></select></div>
<div style="flex:1"><label>Reasoning</label><select id="m_reason"><option>medium</option><option>routine</option><option>high</option><option>formal</option></select></div></div>
<div class="row"><div style="flex:1"><label>Independence</label><select id="m_ind"><option value="NONE">none</option><option value="POST_EXPOSURE_REVIEW">post-exposure review</option><option value="SEALED_SHADOW">sealed shadow</option><option value="BLIND_INDEPENDENT">blind independent</option></select></div>
<div style="flex:1"><label>Evidence cutoff (a commit)</label><input id="m_cut"></div></div>
<label>Repository the cutoff and git checks use</label><input id="m_repo" placeholder="/path/to/repo">
<label>May read (one pattern per line, empty = anything)</label><textarea id="m_read" rows="2"></textarea><label>May write</label><textarea id="m_write" rows="2"></textarea><label>Must never touch</label><textarea id="m_forb" rows="2"></textarea>
<label>Depends on jobs (ids, comma separated)</label><input id="m_dj"><label>Depends on gates (gate, or gate=STATUS, comma separated)</label><input id="m_dg"><label>Unlocks gates</label><input id="m_un"><label>Expected outputs (file names)</label><input id="m_out">
<div class="row"><label><input type="checkbox" id="m_rb"> result-bearing (a restricted run: needs your approval to start)</label></div>
<label>Human approval</label><select id="m_ap"><option value="before_promotion">before promotion</option><option value="none">none</option><option value="before_run">before the run</option><option value="before_result">before the result</option></select>
<label>Stop if (tick any)</label><div class="row" id="m_stops"></div>
<label>Controls (JSON list: positive controls must pass, adverse ones must fail) e.g. [{"label":"proof","tool":"lean","file":"Check.lean","expect":"pass"}]</label><textarea id="m_ctl" rows="3"></textarea>
</details>
<div class="row"><button class="go" id="f_go">Submit</button><button id="f_cancel">Close</button><span id="f_msg" class="why"></span></div></div>
<div class="filters" id="filters"></div><div id="jobs"></div></div>
<div id="right"><div id="agents" class="agents"></div><div id="detail"><p class="muted">Pick a job on the left, or create one.</p></div></div></main>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const el=(t,a,...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="class")e.className=v;else if(x.startsWith("on"))e[x]=v;else if(v===false||v==null){}else e.setAttribute(x,v===true?"":v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c==null?"":String(c))));return e};
let TOKEN=localStorage.getItem("sb_owner_token")||"",SEL=null,FILTER="all",STATUS=null,JOBS=[],BUSY=false,LAST={};
$("#tok").value=TOKEN;
const toast=m=>{const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",4500)};
async function api(path,body){const h={"Content-Type":"application/json"};if(TOKEN)h.Authorization="Bearer "+TOKEN;
 const r=await fetch(path,body===undefined?{headers:h}:{method:"POST",headers:h,body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));
 if(!r.ok||j.ok===false)throw new Error(j.message||j.error||("HTTP "+r.status));return j}
const ago=t=>{if(!t)return"";const s=Math.max(0,Date.now()/1000-t);return s<90?Math.round(s)+"s ago":s<5400?Math.round(s/60)+" min ago":s<172800?Math.round(s/3600)+" h ago":Math.round(s/86400)+" d ago"};
const ST=["queued","planning","running","reviewing","ready","blocked","failed","merged","delivered","cancelled"];
function filters(){const f=$("#filters");f.replaceChildren();[["all","All"],["need","Needs you"],["active","Active"],["done","Done"]].forEach(([k,l])=>f.append(el("button",{class:FILTER===k?"on":"",onclick:()=>{FILTER=k;render()}},l)))}
const match=j=>FILTER==="all"||(FILTER==="need"&&["ready","blocked","failed"].includes(j.state))||(FILTER==="active"&&["queued","planning","running","reviewing"].includes(j.state))||(FILTER==="done"&&["merged","delivered","cancelled","failed"].includes(j.state));
function stepsSummary(j){const s=j.steps||{};return Object.entries(s).map(([k,v])=>v+" "+k).join(", ")}
function render(){filters();const box=$("#jobs");box.replaceChildren();
 const list=JOBS.filter(match);if(!list.length)box.append(el("p",{class:"muted"},"No jobs here."));
 list.forEach(j=>box.append(el("div",{class:"job"+(j.id===SEL?" sel":""),onclick:()=>{SEL=j.id;LAST={};render();detail()}},el("div",{class:"t"},j.title),el("div",{class:"m"},el("span",{class:"st "+(j.shown==="waiting"?"queued":j.state),title:j.shown==="waiting"?"no step has started yet: it is waiting (see why)":""},j.shown||j.state),j.id+" - "+j.kind+" - "+ago(j.updated)),el("div",{class:"m"},stepsSummary(j)))))}
async function loadStatus(){STATUS=await api("/api/agency");const c=STATUS.counts||{},tot=Object.values(c).reduce((a,b)=>a+b,0);
 $("#counts").textContent=tot?ST.filter(k=>c[k]).map(k=>k+" "+c[k]).join(" · "):"no jobs yet";
 const k=$("#kill");k.style.display=STATUS.killed?"inline-block":"none";k.className="chip warn";k.textContent="KILL SWITCH ON";
 const m=$("#merge");m.className="chip "+(STATUS.auto_merge?"warn":"ok");m.textContent=STATUS.auto_merge?"auto-merge ON (safe repos)":"auto-merge off";
 const a=$("#agents");a.replaceChildren();(STATUS.agents||[]).forEach(x=>a.append(el("span",{class:"ag "+(x.available?"on":"off"),title:x.why+" ("+x.runs_today+"/"+x.daily_cap+" runs today)"},el("i"),x.name+(x.available?"":" - "+x.why))))}
async function loadJobs(){const r=await api("/api/jobs?limit=100");JOBS=r.jobs||[]}
async function refresh(force){if(BUSY&&!force)return;try{await Promise.all([loadStatus(),loadJobs()]);const k=JSON.stringify([STATUS,JOBS,SEL,FILTER]);if(force||k!==LAST.list){LAST.list=k;render()}if(SEL)detail(!force)}catch(e){$("#counts").textContent="broker not answering"}}
function diffNode(text){const pre=el("pre");(text||"(no changes)").split("\n").forEach(l=>{const c=l.startsWith("+")&&!l.startsWith("+++")?"add":l.startsWith("-")&&!l.startsWith("---")?"del":l.startsWith("@@")?"hunk":"";pre.append(el("div",{class:c},l||" "))});return pre}
async function act(path,body,msg){if(!TOKEN){toast("Enter your owner token first (top right).");return}BUSY=true;try{await api(path,body);toast(msg)}catch(e){toast("Refused: "+e.message)}BUSY=false;await refresh(true)}
async function detail(quiet){if(!SEL)return;let j,d;try{j=(await api("/api/jobs/"+SEL)).job;d=await api("/api/jobs/"+SEL+"/diff")}catch(e){$("#detail").textContent=e.message;return}
 const dk=JSON.stringify([j,d,!!TOKEN]);if(quiet&&dk===LAST.detail)return;LAST.detail=dk;
 const box=$("#detail");const scroll=box.scrollTop;box.replaceChildren();
 box.append(el("h2",{},j.title),el("div",{},el("span",{class:"st "+(j.shown==="waiting"?"queued":j.state),title:j.shown==="waiting"?"no step has started yet: it is waiting (see why)":""},j.shown||j.state),j.id+" - "+j.kind+" - priority "+j.priority+" - created "+ago(j.created)+(j.target?" - target "+j.target:"")),el("div",{class:"why"},j.why||""));
 const row=el("div",{class:"row"}),has=(...s)=>s.includes(j.state);
 const btn=(label,cls,path,msg,on)=>on&&row.append(el("button",{class:cls||"",disabled:!TOKEN,title:TOKEN?"":"needs the owner token",onclick:()=>act(path,{job_id:j.id},msg)},label));
 btn("Accept (I merged / approve)","go","/api/jobs/accept","Accepted",has("ready"));btn("Reject","bad","/api/jobs/reject","Rejected",has("ready","blocked"));
 btn("Unblock","go","/api/jobs/unblock","Unblocked",has("blocked"));btn("Cancel","bad","/api/jobs/cancel","Cancelled",!["merged","delivered","failed","cancelled"].includes(j.state));btn("Retry as a new job","", "/api/jobs/retry","New job created",has("failed","cancelled"));
 box.append(row,el("h3",{},"Goal"),el("div",{},j.goal));
 const mt=j.meta||{};if(Object.keys(mt).length){const bits=[];if(mt.campaign)bits.push("campaign "+mt.campaign);if(mt.gate)bits.push("gate "+mt.gate);if(mt.role)bits.push("role "+mt.role);if(mt.independence&&mt.independence.mode!=="NONE")bits.push(mt.independence.mode.toLowerCase().replace(/_/g," ")+(mt.independence.cutoff?" at "+mt.independence.cutoff.slice(0,10):""));
  if(mt.result_bearing)bits.push("RESULT-BEARING");if(mt.depends_on_jobs&&mt.depends_on_jobs.length)bits.push("after "+mt.depends_on_jobs.join(", "));if(mt.stop_on&&mt.stop_on.length)bits.push("stops on "+mt.stop_on.join(", "));box.append(el("h3",{},"Governance"),el("div",{class:"muted"},bits.join(" · ")||"defaults"))}
 const acc=j.acceptance||{};if(acc.check||acc.rubric)box.append(el("h3",{},"Acceptance"),el("div",{},acc.check?"check: "+(Array.isArray(acc.check)?acc.check.join(" "):acc.check):""),el("div",{},acc.rubric?"rubric: "+acc.rubric:""));
 box.append(el("h3",{},"Steps"));const t=el("table",{},el("tr",{},el("th",{},"#"),el("th",{},"objective"),el("th",{},"state"),el("th",{},"agent"),el("th",{},"tries"),el("th",{},"result")));
 (j.steps||[]).forEach(s=>t.append(el("tr",{},el("td",{},s.n),el("td",{},s.objective),el("td",{},el("span",{class:"st "+(s.state==="done"?"merged":s.state==="failed"?"failed":s.state==="todo"?"queued":"running")},s.state)),el("td",{},s.agent||""),el("td",{},s.attempts),el("td",{class:"muted"},(s.evidence||"").slice(0,200)))));
 box.append((j.steps||[]).length?t:el("p",{class:"muted"},"No steps yet: an agent will plan this job when the agency runs."));
 const r=j.result||{};if(r.controls&&r.controls.length)box.append(el("h3",{},"Controls"),...r.controls.map(c=>el("div",{class:c.met?"add":"del"},(c.met?"met: ":"NOT met: ")+c.label+" (wanted "+c.expect+", got "+c.got+")")));
 try{const ev=await api("/api/jobs/"+SEL+"/evidence");if(ev.sealed)box.append(el("h3",{},"Evidence"),el("div",{class:ev.seal_verifies?"add":"del"},(ev.seal_verifies?"sealed and verified: ":"SEAL DOES NOT VERIFY: ")+ev.seal.outputs.length+" file(s), seal "+ev.seal.seal_sha256.slice(0,16)),...(ev.stop?[el("div",{class:"del"},"STOPPED: "+ev.stop.reasons.join("; "))]:[]),...(ev.reconcile?[el("div",{class:ev.reconcile.match===false?"del":"add"},"shadow comparison: "+(ev.reconcile.match===true?"the independent routes agree":ev.reconcile.match===false?"DISAGREE: "+ev.reconcile.differences.slice(0,3).join("; "):"not machine-comparable"))]:[]))}catch(e){}
 if(r.check_ok!==undefined)box.append(el("h3",{},"Check"),el("div",{class:r.check_ok?"add":"del"},r.check_ok?"PASSED":"FAILED"),el("pre",{},r.check_out||""));
 if(Object.keys(d.changes||{}).length){box.append(el("h3",{},"Changes (a throw-away copy; nothing here touches the real project)"),el("div",{class:"muted"},Object.entries(d.changes).map(([k,v])=>k+" ("+v+")").join(", ")),diffNode(d.diff))}
 else if(d.note)box.append(el("h3",{},"Changes"),el("div",{class:"muted"},d.note));
 box.scrollTop=scroll}
$("#savetok").onclick=()=>{TOKEN=$("#tok").value.trim();localStorage.setItem("sb_owner_token",TOKEN);toast(TOKEN?"Token saved in this browser":"Token cleared");detail()};
$("#newbtn").onclick=()=>{$("#form").style.display=$("#form").style.display==="block"?"none":"block"};if(new URLSearchParams(location.search).get("new")==="1")$("#form").style.display="block";addEventListener("sb-token",()=>{TOKEN=localStorage.getItem("sb_owner_token")||"";$("#tok").value=TOKEN;detail()});$("#f_cancel").onclick=()=>$("#form").style.display="none";
const STOPS=["check_failed","control_failed","discrepancy","hash_mismatch","tree_dirty","unauthorized_gate","frozen_input_changed","independence_breach","scope_breach"];
STOPS.forEach(k=>$("#m_stops").append(el("label",{},el("input",{type:"checkbox","data-stop":k,checked:["check_failed","hash_mismatch"].includes(k)}),k.replace(/_/g," ")+" ")));
const lines=v=>v.split("\n").map(x=>x.trim()).filter(Boolean),csv=v=>v.split(",").map(x=>x.trim()).filter(Boolean);
function buildMeta(){const g=i=>$("#"+i).value.trim();const dg={};csv(g("m_dg")).forEach(x=>{const [a,b]=x.split("=");dg[a.trim()]=(b||"CLOSED").trim()});
 const m={campaign:g("m_camp"),gate:g("m_gate"),role:$("#m_role").value,reasoning:$("#m_reason").value,repo:g("m_repo"),independence:{mode:$("#m_ind").value,cutoff:g("m_cut"),forbidden_inputs:[],compare_after_seal:$("#m_ind").value!=="NONE"},
  scope:{read:lines(g("m_read")),write:lines(g("m_write")),forbidden:lines(g("m_forb"))},depends_on_jobs:csv(g("m_dj")),depends_on_gates:dg,unlocks:csv(g("m_un")),expected_outputs:csv(g("m_out")),result_bearing:$("#m_rb").checked,human_approval:$("#m_ap").value,
  stop_on:[...document.querySelectorAll("[data-stop]")].filter(x=>x.checked).map(x=>x.dataset.stop)};
 if(g("m_ctl"))m.controls=JSON.parse(g("m_ctl"));return (m.campaign||m.gate||m.repo||m.independence.mode!=="NONE"||m.scope.read.length||m.scope.write.length||m.scope.forbidden.length||m.depends_on_jobs.length||Object.keys(dg).length||m.unlocks.length||m.result_bearing||m.controls)?m:null}
fetch("/api/templates").then(r=>r.json()).then(r=>{(r.templates||[]).forEach(t=>$("#f_tpl").append(el("option",{value:t.id},t.title+" - "+(t.summary||""))))}).catch(()=>{});
$("#f_tpl").onchange=()=>{$("#tplrow").style.display=$("#f_tpl").value?"flex":"none"};
$("#f_go").onclick=async()=>{const m=$("#f_msg");m.textContent="";if(!TOKEN){m.textContent="Enter a token first (top right).";return}
 if($("#f_tpl").value){try{const f={gate:$("#t_gate").value.trim(),campaign:$("#t_camp").value.trim(),goal:$("#f_goal").value.trim(),title:$("#f_title").value.trim(),target:$("#f_target").value.trim(),check:$("#f_check").value.trim(),repo:$("#m_repo").value.trim(),cutoff:$("#m_cut").value.trim(),priority:+$("#f_prio").value};
  const r=await api("/api/jobs/from-template",{template:$("#f_tpl").value,fields:f});SEL=r.job.id;toast("Job "+r.job.id+" created from the template");$("#form").style.display="none";await refresh(true)}catch(e){m.textContent=e.message}return}
 const body={title:$("#f_title").value.trim(),goal:$("#f_goal").value.trim(),kind:$("#f_kind").value,priority:+$("#f_prio").value,target:$("#f_target").value.trim(),acceptance:{}};
 try{const mm=buildMeta();if(mm)body.meta=mm}catch(e){m.textContent="Controls must be valid JSON: "+e.message;return}
 if($("#f_check").value.trim())body.acceptance.check=$("#f_check").value.trim();if($("#f_rubric").value.trim())body.acceptance.rubric=$("#f_rubric").value.trim();
 const steps=$("#f_steps").value.split("\n").map(s=>s.trim()).filter(Boolean);if(steps.length)body.steps=steps.map(o=>({objective:o,needs:[body.kind==="code"?"code":"research"]}));
 let r;try{try{r=await api("/api/jobs",body)}catch(e){if(/allowed project folders/.test(e.message)&&body.target&&confirm("Agents may only work on folders you allow.\n\nAllow agents to work on copies of:\n"+body.target+" ?")){await api("/api/agency/allow_root",{path:body.target});r=await api("/api/jobs",body)}else throw e}SEL=r.job.id;toast("Job "+r.job.id+" created");["f_title","f_goal","f_target","f_check","f_rubric","f_steps"].forEach(i=>$("#"+i).value="");$("#form").style.display="none";await refresh(true)}catch(e){m.textContent=e.message}};
refresh();setInterval(refresh,5000);
</script></body></html>
"""
