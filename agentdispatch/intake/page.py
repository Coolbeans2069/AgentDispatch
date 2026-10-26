"""the /intake page: talk (or type, or drop files, or use the microphone and camera) and the job form fills itself in on the right."""

INTAKE_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentDispatch - New job</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1118;color:#d7e3ee;font:13px/1.45 Segoe UI,system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:#101a24;border-bottom:1px solid #1f3446;flex-wrap:wrap}header h1{margin:0;font-size:15px;letter-spacing:.14em;color:#e0ae52}
.chip{padding:2px 9px;border-radius:10px;background:#16293a;border:1px solid #27465f;font-size:11px;color:#9fc4e0}.sp{flex:1}
input,select,textarea,button{font:inherit;color:inherit;background:#0e1822;border:1px solid #27465f;border-radius:5px;padding:5px 8px}button{cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}
main{display:grid;grid-template-columns:minmax(360px,1fr) minmax(340px,1fr);height:calc(100vh - 96px)}
#chatcol{display:flex;flex-direction:column;min-height:0;border-right:1px solid #1f3446;position:relative}#formcol{overflow:auto;padding:12px 16px}
#msgs{flex:1;overflow:auto;padding:12px 16px}.msg{max-width:92%;margin:6px 0;padding:7px 11px;border-radius:8px;white-space:pre-wrap;border:1px solid #1f3446}
.msg.user{margin-left:auto;background:#13324d;border-color:#2b5a85}.msg.assistant{background:#0e1822}.msg.auto{border-style:dashed;color:#9fb6cc}.msg small{display:block;color:#7f9bb3;font-size:10px;margin-top:3px}
#bar{padding:10px 14px;border-top:1px solid #1f3446;background:#0a121a}#bar .row{display:flex;gap:6px;align-items:flex-end}#txt{flex:1;resize:none;height:54px}
#drop{position:absolute;inset:0;background:#02121be0;border:2px dashed #00e5ff;display:none;align-items:center;justify-content:center;font-size:18px;color:#00e5ff;z-index:5}
.f label{display:block;margin:9px 0 2px;color:#7f9bb3;font-size:11px}.f input,.f select,.f textarea{width:100%}.f .two{display:flex;gap:8px}.f .two>div{flex:1}
@keyframes flash{from{box-shadow:0 0 0 2px #e0ae52}to{box-shadow:0 0 0 0 #e0ae5200}}.flash{animation:flash 1.6s ease-out}
h3{margin:16px 0 6px;font-size:12px;letter-spacing:.1em;color:#e0ae52;text-transform:uppercase}h3:first-child{margin-top:0}
.stp{display:inline-block;padding:1px 9px;margin:0 4px 4px 0;border-radius:9px;font-size:10px;border:1px solid #27465f;color:#7f9bb3}.stp.on{color:#00e5ff;border-color:#00e5ff;box-shadow:0 0 8px #00e5ff55}.stp.past{color:#7bf0c4;border-color:#1f6b57}
.bpsec{margin:8px 0;padding:7px 10px;border:1px solid #1f3446;border-radius:6px;background:#0e1822}.bpsec b{color:#e0ae52}.bpsec .m{color:#9fb6cc;font-size:12px}.bad{color:#ff9b93}.ok2{color:#7bf0c4}
.thk{font-size:11px;color:#9fb6cc;max-height:110px;overflow:auto;margin:6px 0}.thk div{margin:1px 0}
.file{display:flex;gap:10px;padding:7px;margin:5px 0;border:1px solid #1f3446;border-radius:6px;background:#0e1822}.file img,.file video{width:84px;height:60px;object-fit:cover;border-radius:4px;background:#000}
.file .t{font-weight:600}.file .m{color:#7f9bb3;font-size:11px;white-space:pre-wrap}.pill{display:inline-block;padding:0 7px;border-radius:9px;font-size:10px;border:1px solid currentColor;margin-left:6px}
.done{color:#7bf0c4}.running,.pending,.waiting{color:#ffd34d}.failed{color:#ff9b93}.rec{color:#ff6b5e;font-weight:700}
#cam{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center;z-index:90}#cam .box{background:#0b1118;border:1px solid #00e5ff;padding:14px;border-radius:8px;max-width:760px;width:92%}#cam video{width:100%;background:#000;border-radius:6px}
#toast{position:fixed;right:16px;bottom:16px;background:#17314a;border:1px solid #4a86b8;padding:8px 12px;border-radius:6px;display:none;max-width:460px;z-index:80}
@media(max-width:900px){main{grid-template-columns:1fr;height:auto}#chatcol{height:70vh}}
</style></head><body>
<header><h1>NEW JOB</h1><span id="sum" class="chip">Describe what you want: I fill in the form as we talk</span><span class="sp"></span><span id="usage" class="chip" title="strong-model calls today (estimated tokens) and how much of the work ran locally">usage...</span>
<label class="chip" title="a plan that passes every gate is created and run without asking, inside the autonomous projects folder; failed jobs are repaired within the budget"><input type="checkbox" id="autoc"> Autonomous</label>
<button id="trial" title="give the architect a large end-to-end job (a CSV report service) and let it run overnight">Overnight trial: CSV report service</button></header>
<main><div id="chatcol"><div id="drop">Drop files to attach them</div><div id="msgs"></div>
<div id="bar"><div style="margin-bottom:6px;font-size:11px" class="muted">Thinking: <select id="mode"><option value="architect">strong architect (thorough, uses a subscription model)</option><option value="local">quick (local model)</option></select></div><div class="row"><textarea id="txt" placeholder="Describe the job... (Enter sends, Shift+Enter for a new line)"></textarea>
<button id="mic" title="speak: it is transcribed on this computer, then answered like typed text">&#127908; Mic</button><button id="camb" title="record a clip or take a photo with the camera: it is looked at afterwards">&#128247; Camera</button>
<button id="att" title="attach pictures, video, audio or text">&#128206;</button><button id="send" class="go">Send</button></div><input type="file" id="fi" multiple style="display:none"><div id="recstat" class="muted"></div></div></div>
<div id="formcol" class="f"><div id="bpbox"><h3>Blueprint <span id="phase" class="pill pending">interview</span></h3><div id="stepper"></div><div id="thk"></div><div id="rd"></div><div id="bp"></div><div style="display:flex;gap:8px;margin:10px 0"><button id="think" class="go" title="study what must be understood, design it, plan it, and have a second model attack the plan">Think it through</button><button id="mkplan" class="go" disabled>Create the jobs</button><span id="pmsg" class="muted"></span></div></div><h3>Attachments</h3><div id="files"><p class="muted">Nothing attached yet.</p></div>
<details id="quick"><summary>Quick form: one simple job, no architect</summary><h3>The job</h3><label>Title</label><input id="f_title" maxlength="120"><label>Goal (grows as you talk; edit freely)</label><textarea id="f_goal" rows="8"></textarea>
<label>Requirements (one per line)</label><textarea id="f_req" rows="4"></textarea>
<div class="two"><div><label>Kind</label><select id="f_kind"><option>general</option><option>code</option><option>production</option></select></div><div><label>Priority</label><select id="f_prio"><option value="1">normal</option><option value="0">low</option><option value="2">high</option><option value="3">urgent</option></select></div></div>
<label>Project folder (code jobs; agents work on a copy of it)</label><input id="f_target" placeholder="/path/to/my-project">
<label>Acceptance check (a command that must pass, e.g. python tools/acceptance.py)</label><input id="f_check">
<label>What a reviewer must see</label><textarea id="f_rubric" rows="3"></textarea>
<label>Steps (optional, one per line; empty = an agent plans it)</label><textarea id="f_steps" rows="3"></textarea>
<div class="two"><div><label>Template (optional)</label><select id="f_tpl"><option value="">none</option></select></div><div><label>Stage / gate</label><input id="f_gate"></div></div>
<div class="two"><div><label>Campaign</label><input id="f_camp"></div><div></div></div>
<div style="margin-top:14px;display:flex;gap:8px;align-items:center"><button id="go" class="go">Create the job</button><span id="msg" class="muted"></span></div></div></details></div></main>
<div id="cam"><div class="box"><video id="camv" autoplay muted playsinline></video><div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button id="crec" class="go">&#9210; Record clip</button><button id="cshot">&#128247; Take photo</button><span id="ctime" class="muted"></span><span class="sp" style="flex:1"></span><button id="cclose">Close</button></div></div></div>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const el=(t,a,...k)=>{const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="class")e.className=v;else if(x.startsWith("on"))e[x]=v;else if(v===false||v==null){}else e.setAttribute(x,v===true?"":v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c==null?"":String(c))));return e};
let TOKEN=localStorage.getItem("sb_owner_token")||"",SID=(new URLSearchParams(location.search).get("s")||sessionStorage.getItem("sb_intake")||""),S=null,BUSY=false;addEventListener("sb-token",()=>{TOKEN=localStorage.getItem("sb_owner_token")||""});
const toast=m=>{const t=$("#toast");t.textContent=m;t.style.display="block";clearTimeout(toast.t);toast.t=setTimeout(()=>t.style.display="none",5000)};
async function call(path,body,raw,q){const h={};if(TOKEN)h.Authorization="Bearer "+TOKEN;let opt={headers:h};if(raw!==undefined){opt.method="POST";opt.body=raw}else if(body!==undefined){opt.method="POST";h["Content-Type"]="application/json";opt.body=JSON.stringify(body)}
 const r=await fetch(path,opt);const j=await r.json().catch(()=>({}));if(!r.ok||j.ok===false){const e=new Error(j.message||j.error||("HTTP "+r.status));e.data=j;throw e}return j}
function need(){if(!TOKEN){toast("Enter your owner key first (Key button, top right).");return false}return true}
/* ---------- session and drawing ---------- */
async function start(){if(!TOKEN){$("#msgs").replaceChildren(el("div",{class:"msg assistant"},"Press the Key button (top right) and paste your owner key, then we can start. It stays in this browser only."));return}try{if(SID){try{S=(await call("/api/intake/"+SID)).session}catch(e){SID=""}}if(!SID||!S||S.status!=="open"){S=(await call("/api/intake/session",{})).session;SID=S.id;sessionStorage.setItem("sb_intake",SID)}draw(true);poll()}catch(e){toast(e.message)}}
const F=["title","goal","target","check","rubric","gate","campaign"];
function setVal(id,v,flash){const e=$(id);if(document.activeElement===e)return;if(e.value!==v){e.value=v;if(flash){e.classList.remove("flash");void e.offsetWidth;e.classList.add("flash")}}}
function draw(first,changed){changed=changed||[];const f=S.form;F.forEach(k=>setVal("#f_"+k,f[k]||"",changed.includes(k)));setVal("#f_req",(f.requirements||[]).join("\n"),changed.includes("requirements"));
 setVal("#f_steps",(f.steps||[]).map(s=>s.objective).join("\n"),changed.includes("steps"));$("#f_kind").value=f.kind;$("#f_prio").value=String(f.priority);$("#f_tpl").value=f.template||"";
 const m=$("#msgs"),n=m.children.length;if(S.messages.length!==n||first){m.replaceChildren(...S.messages.map(x=>el("div",{class:"msg "+x.role+(x.auto?" auto":"")},x.text)));m.scrollTop=m.scrollHeight}
 if(!S.messages.length&&first)m.append(el("div",{class:"msg assistant"},"Hello. Tell me what you want built, fixed or made, as much as you like: type it, press Mic and speak, drop pictures or a video, or open the Camera. I will fill in the form on the right and ask only what I need."));
 drawBp();const fl=$("#files");fl.replaceChildren();if(!S.files.length)fl.append(el("p",{class:"muted"},"Nothing attached yet."));
 S.files.forEach(x=>{const url="/api/intake/"+SID+"/file/"+x.id,a=x.analysis,th=x.kind==="image"?el("img",{src:url}):x.kind==="video"?el("video",{src:url,controls:true,preload:"metadata"}):x.kind==="audio"?el("audio",{src:url,controls:true,style:"width:150px"}):el("div",{class:"muted"},x.kind);
  const nm=el("input",{placeholder:"this is... (name a person)",size:14});fl.append(el("div",{class:"file"},th,el("div",{style:"flex:1"},el("div",{class:"t"},x.name,el("span",{class:"pill "+a.state},a.state==="done"?"looked at":a.state)),el("div",{class:"m"},(a.summary||"").slice(0,700)),
   el("div",{class:"row",style:"display:flex;gap:6px;margin-top:4px"},x.kind==="image"?[nm,el("button",{onclick:async()=>{if(need()&&nm.value.trim()){try{await call("/api/intake/"+SID+"/known",{fid:x.id,name:nm.value.trim()});toast("Saved: I will recognise "+nm.value.trim()+" in later pictures")}catch(e){toast(e.message)}}}},"Remember")]:"",
   (a.state==="waiting"||a.state==="failed")?el("button",{onclick:()=>call("/api/intake/"+SID+"/analyze",{fid:x.id}).then(()=>toast("Looking again..."))},"Look again"):""))))})}
const PH=["interview","study","design","plan","review","revise","ready"];
function drawBp(){const bp=S.blueprint||{},ph=S.phase||"interview",run=S.work&&S.work.state==="running";$("#phase").textContent=(run?"working: ":"")+ph;$("#phase").className="pill "+(ph==="ready"?"done":run?"running":"pending");
 $("#stepper").replaceChildren(...PH.map(p=>el("span",{class:"stp "+(p===ph?"on":PH.indexOf(p)<PH.indexOf(ph)?"past":"")},p)));
 $("#thk").className="thk";$("#thk").replaceChildren(...(S.thinking||[]).slice(-7).map(t=>el("div",{},"\u00B7 "+t.text)));
 const rd=S.readiness,box=$("#rd");box.replaceChildren();if(rd){rd.blockers.forEach(b=>box.append(el("div",{class:"bad"},"\u2716 "+b)));rd.warnings.forEach(w=>box.append(el("div",{class:"muted"},"\u26A0 "+w)));if(rd.ok)box.append(el("div",{class:"ok2"},"\u2714 Ready: every requirement is delivered by a job, every job can be verified, no loops, no open questions."))}
 const sec=(t,...k)=>el("div",{class:"bpsec"},el("b",{},t),...k),out=[];
 if(bp.summary)out.push(sec(bp.title||"Summary",el("div",{class:"m"},bp.summary),bp.feasibility?el("div",{class:"m "+(bp.feasibility.verdict==="not_feasible"?"bad":"")},"Feasibility: "+bp.feasibility.verdict+" - "+bp.feasibility.why):""));
 if((bp.open_questions||[]).length)out.push(sec("Questions for you",...bp.open_questions.map(q=>{const inp=el("input",{placeholder:q.default?"default: "+q.default:"your answer",value:q.answered||"",size:40,"data-q":q.id});return el("div",{class:"m",style:"margin:4px 0"},q.id+" "+q.question+(q.why?" ("+q.why+")":""),el("br"),inp)}),el("button",{onclick:saveAnswers},"Save my answers")));
 if((bp.requirements||[]).length)out.push(sec("Requirements",...bp.requirements.map(r=>{const cov=rd&&rd.coverage&&rd.coverage[r.id];return el("div",{class:"m"},r.id+" ["+r.priority+"] "+r.text+(cov?(cov.length?"  \u2192 "+cov.join(", "):"  \u2192 NOT COVERED"):""))})));
 if((bp.assumptions||[]).length)out.push(sec("Assumptions",...bp.assumptions.map(a=>el("div",{class:"m"},"\u2022 "+a.text+(a.risk?" (risk: "+a.risk+")":"")))));
 if((bp.findings||[]).length)out.push(sec("What was learned (reverse engineering and research)",...bp.findings.map(f=>el("div",{class:"m"},"\u2022 "+f.about+": "+f.text))));
 const d=bp.design;if(d&&(d.architecture||(d.components||[]).length))out.push(sec("Design",el("div",{class:"m"},d.architecture),...(d.components||[]).map(c=>el("div",{class:"m"},"\u25AA "+c.name+": "+c.responsibility)),...(d.hooks||[]).map(h=>el("div",{class:"m"},"\u21AA hook "+h.name+" ("+h.when+"): "+h.does)),...(d.tools_needed||[]).map(t=>el("div",{class:"m"},"\u2692 tool to build: "+t.name+" - "+t.description))));
 const ps=S.plan_status||{};if(Object.keys(ps).length){const cnt={};Object.values(ps).forEach(p=>cnt[p.state]=(cnt[p.state]||0)+1);out.unshift(sec("Running: "+Object.entries(cnt).map(([k,v])=>v+" "+k).join(" \u00B7 "),...Object.entries(ps).map(([k,p])=>el("div",{class:"m "+(p.state==="failed"||p.state==="blocked"?"bad":p.state==="merged"||p.state==="delivered"?"ok2":"")},p.job+" "+p.state+" - "+p.title+(p.why&&!["merged","delivered"].includes(p.state)?"  ("+p.why+")":"")))))}
 const jobs=bp.plan&&bp.plan.jobs;if(jobs&&jobs.length)out.push(sec("Plan: "+jobs.length+" job(s)",...jobs.map(j=>el("div",{class:"m"},(j.phase?j.phase+" \u00B7 ":"")+j.key+" ["+j.kind+"] "+j.title+(j.depends_on.length?"  after "+j.depends_on.join(", "):"")+"  covers "+(j.covers.join(", ")||"-")+(j.check?"  check: "+j.check:j.rubric?"  rubric":"")+(Object.keys(j.fixtures||{}).length?"  +"+Object.keys(j.fixtures).length+" test file(s)":"")))));
 if(S.critique&&S.critique.issues&&S.critique.issues.length)out.push(sec("Attacked by "+S.critique.by,...S.critique.issues.slice(0,6).map(i=>el("div",{class:"m "+(i.severity==="blocker"?"bad":"")},"["+i.severity+"] "+i.area+": "+i.issue))));
 if((bp.risks||[]).length)out.push(sec("Risks",...bp.risks.map(r=>el("div",{class:"m"},"\u2022 "+r.risk+(r.mitigation?" \u2192 "+r.mitigation:"")))));
 $("#bp").replaceChildren(...out);$("#think").disabled=!!run||!(bp.requirements||[]).length;$("#mkplan").disabled=!(rd&&rd.ok)||S.status!=="open";$("#mkplan").textContent="Create the "+(jobs?jobs.length:"")+" job"+(jobs&&jobs.length===1?"":"s")}
async function saveAnswers(){if(!need())return;const qs=(S.blueprint.open_questions||[]).map(q=>{const v=document.querySelector('[data-q="'+q.id+'"]');return Object.assign({},q,{answered:v?v.value.trim():q.answered})});try{S=(await call("/api/intake/"+SID+"/blueprint",{blueprint:{open_questions:qs}})).session;drawBp();toast("Saved. Press Think it through to use your answers.")}catch(e){toast(e.message)}}
$("#think").onclick=async()=>{if(!need())return;try{S=(await call("/api/intake/"+SID+"/think",{})).session;drawBp();poll();toast("Thinking it through: study, design, plan, then a second model attacks it. This can take several minutes.")}catch(e){toast(e.message)}};
$("#mkplan").onclick=async()=>{if(!need())return;const m=$("#pmsg");m.textContent="";try{let r;try{r=await call("/api/intake/"+SID+"/plan_submit",{})}catch(e){const t=((S.blueprint.plan||{}).jobs||[]).map(j=>j.target).filter(Boolean);if(/allowed project folders/.test(e.message)&&t.length&&confirm("Agents may only work on folders you allow.\n\nAllow agents to work on copies of:\n"+[...new Set(t)].join("\n")+" ?")){for(const f of new Set(t))await call("/api/agency/allow_root",{path:f}).catch(()=>{});r=await call("/api/intake/"+SID+"/plan_submit",{})}else throw e}
 sessionStorage.removeItem("sb_intake");toast(r.jobs.length+" job(s) created");location.href="/agency"}catch(e){m.textContent=e.message}};
function readForm(){const lines=v=>v.split("\n").map(x=>x.trim()).filter(Boolean);return{title:$("#f_title").value,goal:$("#f_goal").value,target:$("#f_target").value,check:$("#f_check").value,rubric:$("#f_rubric").value,gate:$("#f_gate").value,campaign:$("#f_camp").value,
 kind:$("#f_kind").value,priority:+$("#f_prio").value,template:$("#f_tpl").value,requirements:lines($("#f_req").value),steps:lines($("#f_steps").value).map(o=>({objective:o,needs:["research"]}))}}
let saveT=null;function edited(){clearTimeout(saveT);saveT=setTimeout(async()=>{if(!SID||!TOKEN)return;try{S=(await call("/api/intake/"+SID+"/form",{form:readForm()})).session}catch(e){}},700)}
document.querySelectorAll("#formcol input,#formcol textarea,#formcol select").forEach(e=>e.addEventListener("input",edited));
async function poll(){clearInterval(poll.t);poll.t=setInterval(async()=>{if(!SID||!TOKEN)return;const thinking=S&&((S.work&&S.work.state==="running")||(S.status==="submitted"&&S.phase!=="done")),busy=S&&S.files.some(f=>["pending","running"].includes(f.analysis.state));if(!busy&&!thinking)return;try{const s=(await call("/api/intake/"+SID)).session;if(JSON.stringify([s.files,s.messages.length,s.blueprint,s.thinking.length,s.work,s.phase,s.plan_status])!==JSON.stringify([S.files,S.messages.length,S.blueprint,S.thinking.length,S.work,S.phase,S.plan_status])){const ch=[];S=s;draw(false,ch)}}catch(e){}},2000)}
/* ---------- chat ---------- */
async function send(text){if(!need())return;text=(text||$("#txt").value).trim();if(!text||BUSY)return;if(!SID)await start();BUSY=true;$("#send").disabled=true;S.messages.push({role:"user",text});$("#txt").value="";draw(false);$("#msgs").append(el("div",{class:"msg assistant auto"},"thinking..."));$("#msgs").scrollTop=1e9;
 try{const r=await call("/api/intake/"+SID+"/chat",{text,mode:$("#mode").value});S=r.session;draw(false,r.changed);poll()}catch(e){toast(e.message);S=(await call("/api/intake/"+SID)).session;draw(false)}BUSY=false;$("#send").disabled=false}
$("#send").onclick=()=>send();$("#txt").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send()}});
/* ---------- attachments: button, drag and drop, paste ---------- */
async function upload(file){if(!need())return;if(!SID)await start();toast("Attaching "+file.name+"...");try{await call("/api/intake/"+SID+"/upload?name="+encodeURIComponent(file.name)+"&mime="+encodeURIComponent(file.type||""),undefined,file);S=(await call("/api/intake/"+SID)).session;draw(false);poll()}catch(e){toast(e.message)}}
$("#att").onclick=()=>$("#fi").click();$("#fi").onchange=async e=>{for(const f of e.target.files)await upload(f);e.target.value=""};
const dz=$("#chatcol");let dc=0;addEventListener("dragenter",e=>{e.preventDefault();dc++;$("#drop").style.display="flex"});addEventListener("dragleave",e=>{dc=Math.max(0,dc-1);if(!dc)$("#drop").style.display="none"});addEventListener("dragover",e=>e.preventDefault());
addEventListener("drop",async e=>{e.preventDefault();dc=0;$("#drop").style.display="none";for(const f of e.dataTransfer.files)await upload(f)});
addEventListener("paste",async e=>{for(const it of e.clipboardData.items)if(it.kind==="file"){const f=it.getAsFile();if(f)await upload(new File([f],f.name||("pasted-"+Date.now()+".png"),{type:f.type}))}});
/* ---------- microphone: speak, it is transcribed here and answered like typed text ---------- */
let mr=null,mchunks=[],mt0=0,mtimer=null;
$("#mic").onclick=async()=>{if(!need())return;if(mr){mr.stop();return}try{const st=await navigator.mediaDevices.getUserMedia({audio:true});mchunks=[];mr=new MediaRecorder(st);mr.ondataavailable=e=>e.data.size&&mchunks.push(e.data);mt0=Date.now();
  mr.onstop=async()=>{st.getTracks().forEach(t=>t.stop());clearInterval(mtimer);const blob=new Blob(mchunks,{type:mr.mimeType||"audio/webm"});mr=null;$("#mic").classList.remove("rec");$("#mic").textContent="\uD83C\uDFA4 Mic";$("#recstat").textContent="transcribing on this computer...";
   if(!SID)await start();BUSY=true;try{const r=await call("/api/intake/"+SID+"/voice?name=voice.webm&mime="+encodeURIComponent(blob.type),undefined,blob);S=r.session;draw(false,r.changed||[])}catch(e){toast(e.message)}BUSY=false;$("#recstat").textContent=""};
  mr.start();$("#mic").classList.add("rec");mtimer=setInterval(()=>{const s=Math.round((Date.now()-mt0)/1000);$("#mic").textContent="\u23F9 Stop ("+s+"s)";$("#recstat").textContent="listening... press Stop when you are done"},500)}catch(e){toast("The microphone is not available: "+e.message)}};
/* ---------- camera: record a clip or take a photo; it is looked at afterwards, not live ---------- */
let cs=null,cr=null,cch=[],ct0=0,ctm=null;
$("#camb").onclick=async()=>{if(!need())return;try{cs=await navigator.mediaDevices.getUserMedia({video:true,audio:true});$("#camv").srcObject=cs;$("#cam").style.display="flex"}catch(e){toast("The camera is not available: "+e.message)}};
function camClose(){if(cr&&cr.state==="recording")cr.stop();if(cs)cs.getTracks().forEach(t=>t.stop());cs=null;$("#cam").style.display="none";clearInterval(ctm);$("#ctime").textContent="";$("#crec").textContent="\u23FA Record clip"}
$("#cclose").onclick=camClose;
$("#cshot").onclick=()=>{const v=$("#camv"),c=document.createElement("canvas");c.width=v.videoWidth;c.height=v.videoHeight;c.getContext("2d").drawImage(v,0,0);c.toBlob(b=>upload(new File([b],"photo-"+Date.now()+".jpg",{type:"image/jpeg"})),"image/jpeg",.92);toast("Photo taken")};
$("#crec").onclick=()=>{if(cr&&cr.state==="recording"){cr.stop();return}cch=[];cr=new MediaRecorder(cs);cr.ondataavailable=e=>e.data.size&&cch.push(e.data);ct0=Date.now();cr.onstop=()=>{clearInterval(ctm);$("#crec").textContent="\u23FA Record clip";$("#ctime").textContent="";const b=new Blob(cch,{type:cr.mimeType||"video/webm"});upload(new File([b],"clip-"+Date.now()+".webm",{type:b.type}))};
 cr.start(1000);$("#crec").textContent="\u23F9 Stop";ctm=setInterval(()=>$("#ctime").textContent=Math.round((Date.now()-ct0)/1000)+" s",500)};
/* ---------- templates and creating the job ---------- */
fetch("/api/templates").then(r=>r.json()).then(r=>(r.templates||[]).forEach(t=>$("#f_tpl").append(el("option",{value:t.id},t.title)))).catch(()=>{});
$("#go").onclick=async()=>{if(!need())return;const m=$("#msg");m.textContent="";if(!SID)return;try{await call("/api/intake/"+SID+"/form",{form:readForm()});let r;try{r=await call("/api/intake/"+SID+"/submit",{})}catch(e){
  const tg=$("#f_target").value.trim();if(/allowed project folders/.test(e.message)&&tg&&confirm("Agents may only work on folders you allow.\n\nAllow agents to work on copies of:\n"+tg+" ?")){await call("/api/agency/allow_root",{path:tg});r=await call("/api/intake/"+SID+"/submit",{})}else throw e}
  sessionStorage.removeItem("sb_intake");toast("Job "+r.job.id+" created");location.href="/agency"}catch(e){m.textContent=e.message}};
async function loadAuto(){try{const a=await call("/api/autonomy"),u=a.usage;$("#autoc").checked=!!a.config.enabled;
 $("#usage").textContent="strong today: "+u.strong_calls_today+" call(s), ~"+Math.round(u.strong_tokens_est/1000)+"k tokens · local share "+u.local_share+"% · limit "+a.config.strong_calls_per_day+"/day, "+a.config.strong_calls_per_session+"/plan"}catch(e){}}
$("#autoc").onchange=async e=>{if(!need()){e.target.checked=!e.target.checked;return}try{await call("/api/autonomy",{enabled:e.target.checked});toast(e.target.checked?"Autonomous mode ON: ready plans run by themselves inside the projects folder":"Autonomous mode off");loadAuto()}catch(x){e.target.checked=!e.target.checked;toast(x.message)}};
$("#trial").onclick=async()=>{if(!need())return;if(!$("#autoc").checked){toast("Switch Autonomous on first: the trial runs without anyone approving it.");return}if(!confirm("Start the overnight trial?\n\nThe architect will study, design and plan the FreeCAD workstation, have a second model attack the plan, then create and run the jobs by itself. It uses strong-model calls within your limits."))return;
 try{const r=await call("/api/intake/trial",{name:"csv_report_service"});sessionStorage.setItem("sb_intake",r.session.id);location.reload()}catch(e){toast(e.message)}};
if(TOKEN)loadAuto();setInterval(()=>{if(TOKEN)loadAuto()},15000);
start();addEventListener("sb-token",()=>{start();loadAuto()});
</script></body></html>
"""
