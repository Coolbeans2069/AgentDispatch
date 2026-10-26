"""the /brain page: a live 3D rotating brain made of ~1300 neurons. Lobes are FUNCTIONS (planning, code, language, vision, speech, sound, fast reflexes, world knowledge); models are tenants that move into
the lobe their current task needs, in their own colour. A lobe's neurons fire as a PERCENTAGE of the lobe that grows with the task's difficulty and time elapsed (an easy task is a short burst, a hard one
keeps the lobe lit longer and wider). Arcs between lobes with numbers are the hand-off chain of the current job, the routing panel says which brain is running, which model took which task and why, and
a rotating DNA strand down the side has one segment per task that fills in as the pipe completes. Drag to turn it, wheel to zoom. `?demo=1` runs a simulation (extra video / speech / music models)."""

BRAIN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentDispatch - Neural Engine</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#04070c;color:#cfe3f3;font:12px/1.4 Segoe UI,system-ui,sans-serif;overflow:hidden}
body{background:radial-gradient(ellipse at 40% 45%,#0b1a2a 0%,#050a11 55%,#02040a 100%)}
header{position:fixed;top:44px;left:0;right:0;height:46px;display:flex;align-items:center;gap:12px;padding:0 16px;z-index:5;background:linear-gradient(#050a11ee,#050a1100)}
header h1{margin:0;font-size:14px;letter-spacing:.32em;color:#e8b556;text-shadow:0 0 12px #e8b55688;font-weight:600}
.chip{padding:2px 9px;border-radius:10px;background:#0f2234cc;border:1px solid #27465f;font-size:11px;color:#9fc4e0}
.chip.warn{background:#3a1c1ccc;border-color:#8a2f2f;color:#ffb0a8}.chip.ok{background:#0f2e26cc;border-color:#1f6b57;color:#8ff0cf}
.sp{flex:1}
header button,header select{font:inherit;color:#cfe3f3;background:#0f2234cc;border:1px solid #27465f;border-radius:5px;padding:3px 9px;cursor:pointer}
header button.on{background:#2b5a85;border-color:#4a86b8}header a{color:#8cc8ff;text-decoration:none}
#stage{position:fixed;left:0;top:90px;bottom:78px;right:250px;cursor:grab}#stage.drag{cursor:grabbing}
#brain{width:100%;height:100%;display:block}
#panel{position:fixed;left:12px;top:100px;width:300px;max-height:calc(100% - 206px);overflow:auto;background:#07111bd9;border:1px solid #1d4766;border-radius:8px;padding:8px 10px;z-index:4;backdrop-filter:blur(3px)}
#cap{position:fixed;left:50%;transform:translateX(-50%);bottom:86px;max-width:min(760px,calc(100% - 520px));text-align:center;font-size:14px;color:#fff;background:#06101bdd;border:1px solid #2b5a85;border-radius:8px;padding:7px 14px;z-index:6;opacity:0;transition:opacity .4s;pointer-events:none}
#panel h4{margin:8px 0 4px;font-size:10px;letter-spacing:.18em;color:#e8b556;text-transform:uppercase;font-weight:600}#panel h4:first-child{margin-top:0}
.row{display:flex;flex-direction:column;gap:1px;margin:0 0 6px;padding:4px 0 5px 8px;border-left:3px solid var(--c)}
.row b{font-weight:600}.row .m{color:#7f9bb3;font-size:11px}.bar{height:4px;background:#12283b;border-radius:2px;margin-top:2px;overflow:hidden}.bar u{display:block;height:100%;background:var(--c);box-shadow:0 0 6px var(--c)}
.empty{color:#4f6b83;font-size:11px}.hop{display:flex;gap:6px;font-size:11px;margin:2px 0}.hop i{font-style:normal;color:#0a141f;background:var(--c);border-radius:8px;min-width:16px;text-align:center;font-weight:700}
#dna{position:fixed;right:0;top:90px;bottom:78px;width:250px;display:flex;flex-direction:column;gap:0;border-left:1px solid #132536;background:#050a11cc}
#dnahelix{flex:1;min-height:120px;display:flex;overflow:hidden}
#dnalabels{width:84px;position:relative;font-size:10px;color:#7f9bb3}
#dnalabels div{position:absolute;right:6px;transform:translateY(-50%);white-space:nowrap;text-align:right}
#dnac{width:166px;height:100%}
#dnalog{height:148px;flex:0 0 148px;overflow:auto;padding:6px 8px;border-top:1px solid #1d4766;font:10px/1.35 Consolas,ui-monospace,monospace;color:#8aa0b4;background:#07111be6}
#dnalog h5{margin:0 0 4px;font:600 9px Segoe UI;letter-spacing:.16em;color:#e8b556;text-transform:uppercase}
#dnalog .lgline{margin:0 0 3px;padding-left:6px;border-left:2px solid #27465f;white-space:pre-wrap;word-break:break-word}
#dnalog .lgline.watchdog{border-left-color:#e8b556;color:#d7c38a}
#dnalog .lgline.warn{border-left-color:#ff9a3d;color:#ffc089}
#dnalog .lgline.ok{border-left-color:#3ddc84}
#dnalog .muted{color:#4f6b83}
#bottom{position:fixed;left:0;right:250px;bottom:0;height:78px;padding:6px 16px;overflow:hidden;background:linear-gradient(#02040a00,#02040acc 30%)}
#stage{right:250px}
#legend{display:flex;flex-wrap:wrap;gap:6px 12px;margin-bottom:4px}
.lg{display:flex;align-items:center;gap:5px;font-size:11px;color:#9fb6cc}.lg i{width:9px;height:9px;border-radius:50%;background:var(--c);opacity:.35}
.lg.active i,.lg.busy i{opacity:1;box-shadow:0 0 9px var(--c)}.lg.online i{opacity:.8}.lg.idle i{opacity:.55}.lg.cooldown i{background:#ffb020;opacity:1}.lg.off{opacity:.45}
#ticker{display:flex;gap:14px;font-size:11px;color:#7f9bb3;white-space:nowrap;overflow:hidden}#ticker span{display:inline-flex;align-items:center;gap:5px}#ticker i{width:7px;height:7px;border-radius:50%;background:var(--c)}
#tip{position:fixed;pointer-events:none;background:#08121cee;border:1px solid #2b5a85;border-radius:6px;padding:6px 9px;font-size:11px;display:none;z-index:9;max-width:280px}
@media (max-width:640px){#panel{display:none}}
</style></head><body>
<header><h1>NEURAL ENGINE</h1><span id="counts" class="chip">connecting...</span><span id="kill" class="chip warn" style="display:none">KILL SWITCH ON</span><span id="merge" class="chip"></span>
<span class="sp"></span><select id="jobsel" title="which job's strand to show"><option value="">latest job</option></select><button id="spin" title="turn the brain by itself">Spin</button><button id="panelbtn" class="on">Routing</button><button id="rec" style="display:none" title="record one full run of the demo as a video">Record</button><button id="demo">Demo</button></header>
<div id="stage"><canvas id="brain"></canvas></div>
<div id="panel"></div><div id="cap"></div>
<div id="dna"><div id="dnahelix"><div id="dnalabels"></div><canvas id="dnac"></canvas></div><div id="dnalog"><h5>Activity</h5><div class="muted">waiting for broker…</div></div></div>
<div id="bottom"><div id="legend"></div><div id="ticker"></div></div><div id="tip"></div>
<script>
const $=s=>document.querySelector(s);
const REDUCED=matchMedia("(prefers-reduced-motion: reduce)").matches;
const DEMO=new URLSearchParams(location.search).get("demo")==="1";
const EMBED=new URLSearchParams(location.search).get("embed")==="1";
if(EMBED){document.documentElement.classList.add("embed");document.body.classList.add("embed");
 const st=document.createElement("style");st.textContent="html.embed body{padding-top:0!important}html.embed #sbnav,html.embed header,html.embed #panel,html.embed #dna,html.embed #dnalog,html.embed #bottom,html.embed #cap,html.embed #tip{display:none!important}html.embed #stage{position:fixed;left:0;top:0;right:0;bottom:0!important;cursor:default}html.embed body{overflow:hidden;background:#02070d}";document.head.appendChild(st)}
const rgba=(hex,a)=>{const n=parseInt(hex.slice(1),16);return "rgba("+(n>>16)+","+((n>>8)&255)+","+(n&255)+","+a+")"};
const state={cards:[],tenants:[],pins:[],chain:[],strand:null,cursor:{event:0,ts:0},pulses:[],counts:{},jobsel:"",sig:"",lobes:{}};
/* ================= the neural mesh: neurons placed in lobe-shaped volumes, joined to near neighbours and by long tracts between lobes ================= */
function rng(s){return()=>{s=(s*1664525+1013904223)>>>0;return s/4294967296}}
const rnd=rng(11);
const LOBE_DEF={ /* c centre, r radii, n neurons, bi = mirrored on both sides, lab = where the label sits, slots = places for model tokens, tint = idle colour */
 frontal:{c:[0,.25,.6],r:[.6,.4,.36],n:220,fis:1,lab:[0,.72,.82],name:"FRONTAL",slots:[[0,.32,.8],[.34,.2,.74],[-.34,.2,.74],[0,.58,.55],[.22,.45,.7],[-.22,.45,.7],[.4,.35,.55],[-.4,.35,.55]]},
 parietal:{c:[0,.5,-.12],r:[.62,.32,.34],n:170,fis:1,lab:[0,1,-.1],name:"PARIETAL",slots:[[0,.7,-.05],[.36,.64,-.15],[-.36,.64,-.15],[0,.72,.2],[.2,.55,-.25],[-.2,.55,-.25],[.5,.6,0],[-.5,.6,0]]},
 occipital:{c:[0,.2,-.7],r:[.55,.38,.28],n:150,fis:1,lab:[0,.62,-.95],name:"OCCIPITAL",slots:[[0,.22,-.92],[.32,.32,-.84],[-.32,.32,-.84],[0,.02,-.9],[.3,.05,-.85],[-.3,.05,-.85],[.15,.4,-.8],[-.15,.4,-.8]]},
 temporal:{c:[.62,-.15,.15],r:[.24,.2,.5],n:90,bi:1,lab:[1,-.48,.2],name:"TEMPORAL",slots:[[.8,-.15,.15],[-.8,-.15,.15],[.76,-.22,.42],[-.76,-.22,.42],[.7,-.05,.3],[-.7,-.05,.3],[.85,-.3,.05],[-.85,-.3,.05],[.65,-.25,.55],[-.65,-.25,.55]]},
 cerebellum:{c:[0,-.5,-.65],r:[.5,.22,.28],n:110,lab:[0,-.88,-.85],name:"CEREBELLUM",slots:[[0,-.55,-.76],[.32,-.5,-.68],[-.32,-.5,-.68],[.15,-.42,-.72],[-.15,-.42,-.72],[.4,-.55,-.55],[-.4,-.55,-.55]]},
 brainstem:{c:[0,-.7,-.2],r:[.1,.3,.1],n:40,lab:[0,-1.12,-.2],name:"BROKER",slots:[[0,-.82,-.2]]},
 broca:{c:[-.55,.05,.55],r:[.15,.15,.15],n:30,lab:[-.95,.3,.7],name:"SPEECH",slots:[[-.7,.1,.62],[-.7,-.06,.5],[-.55,.18,.45]]},
 auditory:{c:[.62,.12,-.08],r:[.12,.12,.2],n:25,bi:1,lab:[-1,.33,-.2],name:"AUDIO",slots:[[.74,.15,-.1],[-.74,.15,-.1],[.7,.0,-.25],[-.7,.0,-.25]]},
 limbic:{c:[0,-.02,.15],r:[.2,.16,.3],n:60,lab:[0,-.2,.5],name:"LIMBIC",slots:[[0,.02,.15],[.14,-.06,.34],[-.14,-.06,.0],[.1,.1,.25],[-.1,.1,.25]]}};
const LOBES=Object.keys(LOBE_DEF),LI={};LOBES.forEach((k,i)=>LI[k]=i);
const N=[];/* neuron: [x,y,z,lobe] */
LOBES.forEach(k=>{const d=LOBE_DEF[k];d.members=[];d.idx=LI[k];const sides=d.bi?[1,-1]:[1];
 sides.forEach(sg=>{for(let i=0;i<d.n;i++){let x,y,z;do{x=rnd()*2-1;y=rnd()*2-1;z=rnd()*2-1}while(x*x+y*y+z*z>1||x*x+y*y+z*z<(rnd()<.72?.55:0));
  const G=1.2;let px=sg*(d.bi?Math.abs(d.c[0]):0)+(d.bi?x*d.r[0]*G:d.c[0]+x*d.r[0]*G),py=d.c[1]+y*d.r[1]*G,pz=d.c[2]+z*d.r[2]*G;
  if(d.fis&&Math.abs(px)<.05)px=(px<0?-1:1)*(.05+rnd()*.03);d.members.push(N.length);N.push([px,py,pz,d.idx])}})});
const NN=N.length,PX=new Float32Array(NN),PY=new Float32Array(NN),PZ=new Float32Array(NN),PF=new Float32Array(NN),E=new Float32Array(NN),COL=new Array(NN).fill("#6fb4ee");
const EA=[],EB=[],adj=Array.from({length:NN},()=>[]);const seen=new Set();
function addEdge(a,b){if(a===b)return;const k=a<b?a*NN+b:b*NN+a;if(seen.has(k))return;seen.add(k);EA.push(a);EB.push(b);adj[a].push(b);adj[b].push(a)}
for(let i=0;i<NN;i++){const best=[[1e9,-1],[1e9,-1],[1e9,-1]];for(let j=0;j<NN;j++){if(j===i)continue;const dx=N[i][0]-N[j][0],dy=N[i][1]-N[j][1],dz=N[i][2]-N[j][2],dd=dx*dx+dy*dy+dz*dz;
  if(dd<best[2][0]){best[2]=[dd,j];best.sort((a,b)=>a[0]-b[0])}}best.forEach(b=>addEdge(i,b[1]))}
const TRACTS=[["frontal","parietal",9],["parietal","occipital",9],["frontal","temporal",7],["temporal","occipital",6],["limbic","frontal",7],["limbic","temporal",7],["limbic","brainstem",5],["brainstem","cerebellum",7],["cerebellum","occipital",5],["broca","frontal",6],["auditory","temporal",6],["brainstem","frontal",5],["brainstem","parietal",4],["parietal","temporal",5]];
TRACTS.forEach(([a,b,n])=>{const A=LOBE_DEF[a].members,B=LOBE_DEF[b].members;for(let i=0;i<n;i++)addEdge(A[(rnd()*A.length)|0],B[(rnd()*B.length)|0])});
const NE=EA.length;
/* ================= camera ================= */
const TINT={frontal:"#c98a6a",parietal:"#6fc79a",occipital:"#c9709f",temporal:"#6f9fd8",cerebellum:"#9a7fd0",broca:"#d0b45e",auditory:"#d09a55",limbic:"#c8d3dc",brainstem:"#a0aab4"};
const cv=$("#brain"),g=cv.getContext("2d");let W=0,H=0,DPR=1,yaw=.7,pitch=.28,zoom=1,spin=true,dragging=false,lastMove=0;
function size(){DPR=Math.min(2,devicePixelRatio||1);W=cv.clientWidth;H=cv.clientHeight;cv.width=W*DPR;cv.height=H*DPR;g.setTransform(DPR,0,0,DPR,0,0)}
function proj(x,y,z,o){const cy=Math.cos(yaw),sy=Math.sin(yaw),x1=x*cy+z*sy,z1=-x*sy+z*cy,cp=Math.cos(pitch),sp=Math.sin(pitch),y1=y*cp-z1*sp,z2=y*sp+z1*cp,f=1/(1-z2*.32),S=Math.min(W/2.05,H/2.0)*zoom;
 o[0]=W/2+(W>1100&&$("#panel").style.display!=="none"?150:0)+x1*S*f;o[1]=H/2-y1*S*f;o[2]=z2;o[3]=f;return o}
const tmp=[0,0,0,0];
$("#stage").addEventListener("mousedown",e=>{dragging=true;$("#stage").classList.add("drag");lastMove=[e.clientX,e.clientY]});
addEventListener("mouseup",()=>{dragging=false;$("#stage").classList.remove("drag")});
addEventListener("mousemove",e=>{if(dragging){yaw+=(e.clientX-lastMove[0])*.008;pitch=Math.max(-1.2,Math.min(1.2,pitch+(e.clientY-lastMove[1])*.006));lastMove=[e.clientX,e.clientY]}else hover(e)});
$("#stage").addEventListener("wheel",e=>{e.preventDefault();zoom=Math.max(.5,Math.min(2.6,zoom*(e.deltaY<0?1.08:.93)))},{passive:false});
$("#spin").classList.toggle("on",spin);$("#spin").onclick=e=>{spin=!spin;e.target.classList.toggle("on",spin)};
$("#panelbtn").onclick=e=>{const p=$("#panel"),on=p.style.display==="none";p.style.display=on?"":"none";e.target.classList.toggle("on",on)};
/* ================= glow sprites ================= */
const sprites={};function sprite(col){if(sprites[col])return sprites[col];const c=document.createElement("canvas");c.width=c.height=48;const x=c.getContext("2d"),gr=x.createRadialGradient(24,24,0,24,24,24);
 gr.addColorStop(0,"rgba(255,255,255,.95)");gr.addColorStop(.16,rgba(col,.95));gr.addColorStop(.5,rgba(col,.32));gr.addColorStop(1,rgba(col,0));x.fillStyle=gr;x.fillRect(0,0,48,48);return sprites[col]=c}
/* ================= firing model ================= */
const L=LOBES.map(()=>({a:0,hard:.3,col:"#6fb4ee",pct:0,standby:0,srcs:0}));
const signals=[];const bolts=[];
function ignite(i,col,depth){E[i]=1;COL[i]=col;if(signals.length>420||depth>3)return;const nb=adj[i];for(let k=0;k<2;k++){if(Math.random()<.55){const j=nb[(Math.random()*nb.length)|0];signals.push({a:i,b:j,t:0,dur:.28+Math.random()*.25,col,d:depth+1})}}}
function source(sorc){/* the firing percentage a running task asks of its lobe */
 const d=sorc.difficulty,el=sorc.el,ex=Math.max(8,sorc.expected_s||40),prog=Math.min(1,el/ex);
 let p=d<.4?.1+.34*Math.exp(-el/3.5):d*(.25+.75*prog);if(el>ex)p=Math.min(.98,p+.06*(el-ex)/ex);return Math.min(.98,p)}
function sim(dt){
 L.forEach(l=>{l.target=0;l.standby=0;l.bestp=0;l.bestHard=0});
 const now=performance.now()/1000;
 state.tenants.forEach(t=>{const li=LI[t.lobe];if(li===undefined)return;const l=L[li];
  if(t.kind==="task"||(t.kind==="window"&&t.working)||(t.kind==="model"&&t.working)){const sc={difficulty:t.difficulty||(t.kind==="model"?.55:.5),el:Math.max(0,now-t.t0),expected_s:t.expected_s||60};t._p=source(sc);l.target=Math.min(.98,l.target+t._p);if(t._p>=l.bestp){l.bestp=t._p;l.col=t.color||l.col;l.bestHard=sc.difficulty}}
  else if(t.kind==="model"&&(t.warm||t.state==="online")){l.standby++;l.target=Math.min(.25,l.target+.04);if(!l.bestp)l.col=t.color||l.col}
  else l.standby++;});
 L.forEach((l,i)=>{if(!l.target&&l.standby)l.target=.04;l.a+=(l.target-l.a)*Math.min(1,dt*3);l.hard+=((l.bestHard||l.hard)-l.hard)*Math.min(1,dt*2);l.pct=Math.round(l.a*100)});
 LOBES.forEach((k,i)=>{const l=L[i],mem=LOBE_DEF[k].members,tau=.22+.9*l.hard;let lit=0;
  for(const m of mem){E[m]*=Math.exp(-dt/tau);if(E[m]>.35)lit++}
  const need=l.a*mem.length-lit;if(need>.5){const burst=Math.min(need,Math.ceil(mem.length*dt*(.9+3.5*(1-l.hard))));for(let q=0;q<burst;q++){ignite(mem[(Math.random()*mem.length)|0],l.col,0)}}});
 for(let i=signals.length-1;i>=0;i--){const s=signals[i];s.t+=dt/s.dur;if(s.t>=1){E[s.b]=Math.max(E[s.b],.85);COL[s.b]=s.col;if(Math.random()<.35&&s.d<4){const nb=adj[s.b],j=nb[(Math.random()*nb.length)|0];if(signals.length<420)signals.push({a:s.b,b:j,t:0,dur:.25+Math.random()*.2,col:s.col,d:s.d+1})}signals.splice(i,1)}}}
/* ================= model tokens: agents and models living in lobes; they migrate when their task moves lobe ================= */
const tokens={};
function slotPos(lobe,i){
 /* keep every token INSIDE its lobe ellipsoid: fixed slots first, then a golden-angle pack scaled by the lobe radii (never spiral into empty space) */
 const d=LOBE_DEF[lobe]||LOBE_DEF.temporal;
 if(i<d.slots.length)return d.slots[i].slice();
 const k=i-d.slots.length,ga=k*2.399963229728653,t=(k+.5)/Math.max(16,k+10),r=Math.min(.72,.18+Math.sqrt(t)*.58);
 const y=Math.cos(Math.PI*((k%19)+.5)/19),rr=Math.sqrt(Math.max(0,1-y*y))*r,x=Math.cos(ga)*rr,z=Math.sin(ga)*rr;
 const sx=d.r[0]*0.78,sy=d.r[1]*0.78,sz=d.r[2]*0.78;
 if(d.bi){const side=(k%2)?1:-1;return[side*(Math.abs(d.c[0])+Math.abs(x)*sx),d.c[1]+y*sy,d.c[2]+z*sz]}
 return[d.c[0]+x*sx,d.c[1]+y*sy,d.c[2]+z*sz]}
function syncTokens(){const want={},per={};
 const add=(id,o)=>{const lb=o.lobe;per[lb]=(per[lb]||0);o.slot=per[lb]++;want[id]=o};
 const taskOf={};state.tenants.filter(t=>t.kind==="task").forEach(t=>{taskOf[t.agent]=t});
 /* when a task names a concrete model, that model token lights up and can migrate into the task's lobe */
 const busyModel={};state.tenants.filter(t=>t.kind==="task"&&t.model).forEach(t=>{busyModel[t.model]=t;busyModel[(t.model||"").split("/").pop()]=t});
 state.cards.forEach(c=>{const t=taskOf[c.id],lobe=t?t.lobe:(c.region==="brainstem"&&c.id==="broker"?null:c.region);if(!lobe||!LOBE_DEF[lobe])return;
  add("a:"+c.id,{kind:"agent",name:c.name,color:c.color,lobe,state:t?"active":c.state,card:c,task:t,label:t?(t.model&&t.model!==c.name?c.name+" · "+t.model:c.name):c.name})});
 state.tenants.forEach(t=>{if(t.kind==="model"){const id=t.id||t.name,busy=t.working||busyModel[id]||busyModel[t.name];
   const lobe=(busy&&busy.lobe)||t.lobe;if(!lobe||!LOBE_DEF[lobe])return;
   const st=busy||t.working?"active":(t.state||(t.warm?"online":"standby"));
   add("m:"+id,{kind:"model",name:t.name,color:t.color||LOBE_COLOR[lobe]||"#8aa0b4",lobe,state:st,label:t.name,t,task:busy||null,mid:id})}
  else if(t.kind==="window")add("w:"+t.name,{kind:"window",name:t.name,color:t.color||"#ff6bb5",lobe:t.lobe,state:t.working?"active":"online",label:t.name,t})});
 state.pins.forEach(p=>add("p:"+p.function,{kind:"pin",name:p.name,color:"#9fd8ff",lobe:p.lobe,state:"pinned",label:p.name+" ("+p.function+")",pin:p}));
 for(const id of Object.keys(tokens))if(!want[id])delete tokens[id];
 for(const [id,o] of Object.entries(want)){let k=tokens[id];const to=slotPos(o.lobe,o.slot);
  if(!k){k=tokens[id]={pos:to.slice(),from:to.slice(),to:to.slice(),t:1,lobe:o.lobe}}
  if(k.lobe!==o.lobe){k.from=k.pos.slice();k.to=to;k.t=0;k.lobe=o.lobe;if(o.state==="active")migrated(o,k.from)}else if(k.t>=1){k.to=to}
  Object.assign(k,{kind:o.kind,name:o.name,color:o.color||"#8aa0b4",state:o.state,label:o.label,card:o.card,task:o.task,t0data:o.t,pin:o.pin,mid:o.mid})}}
function migrated(o){state.pulses.push({agent:o.card?o.card.id:"",kind:"migrate",text:o.name+" moved to "+o.lobe})}
function stepTokens(dt){for(const k of Object.values(tokens)){if(k.t<1){k.t=Math.min(1,k.t+dt/1.3);const e=k.t<.5?2*k.t*k.t:1-Math.pow(-2*k.t+2,2)/2;for(let q=0;q<3;q++)k.pos[q]=k.from[q]+(k.to[q]-k.from[q])*e;k.pos[1]+=Math.sin(Math.PI*e)*.45}else for(let q=0;q<3;q++)k.pos[q]+=(k.to[q]-k.pos[q])*Math.min(1,dt*4)}}
/* ================= hand-off arcs: the chain of the current job ================= */
function arcPoints(a,b,n){const mid=[(a[0]+b[0])/2*1.45,(a[1]+b[1])/2+.35,(a[2]+b[2])/2*1.45],pts=[];for(let i=0;i<=n;i++){const t=i/n,u=1-t;pts.push([u*u*a[0]+2*u*t*mid[0]+t*t*b[0],u*u*a[1]+2*u*t*mid[1]+t*t*b[1],u*u*a[2]+2*u*t*mid[2]+t*t*b[2]])}return pts}
function lobeCenter(k){const d=LOBE_DEF[k];return d?d.bi?[0,d.c[1],d.c[2]]:d.c:[0,0,0]}
function bolt(from,to,col){if(!LOBE_DEF[from]||!LOBE_DEF[to]||from===to)return;bolts.push({pts:arcPoints(lobeCenter(from),lobeCenter(to),28),t:0,col})}
/* ================= drawing ================= */
const P=[0,0,0,0];
function draw(now,dt){g.clearRect(0,0,W,H);
 for(let i=0;i<NN;i++){proj(N[i][0],N[i][1],N[i][2],P);PX[i]=P[0];PY[i]=P[1];PZ[i]=P[2];PF[i]=P[3]}
 /* lobe glow */
 LOBES.forEach((k,i)=>{const l=L[i];if(l.a<.03)return;const d=LOBE_DEF[k],c=d.bi?null:d.c,cs=c?[c]:[[Math.abs(d.c[0]),d.c[1],d.c[2]],[-Math.abs(d.c[0]),d.c[1],d.c[2]]];
  cs.forEach(cc=>{proj(cc[0],cc[1],cc[2],P);const r=Math.max(d.r[0],d.r[1],d.r[2])*Math.min(W/2.05,H/2.0)*zoom*P[3]*1.5,gr=g.createRadialGradient(P[0],P[1],0,P[0],P[1],r);gr.addColorStop(0,rgba(l.col,.12+.3*l.a));gr.addColorStop(1,rgba(l.col,0));g.fillStyle=gr;g.fillRect(P[0]-r,P[1]-r,r*2,r*2)})});
 /* edges: all faint in one path, the lit ones bright */
 g.lineWidth=1;g.strokeStyle="rgba(80,150,215,.2)";g.beginPath();for(let e=0;e<NE;e++){g.moveTo(PX[EA[e]],PY[EA[e]]);g.lineTo(PX[EB[e]],PY[EB[e]])}g.stroke();
 g.lineWidth=1.4;for(let e=0;e<NE;e++){const a=EA[e],b=EB[e],en=Math.max(E[a],E[b]);if(en>.18){const c=E[a]>=E[b]?COL[a]:COL[b];g.strokeStyle=rgba(c,Math.min(.9,en*.85));g.beginPath();g.moveTo(PX[a],PY[a]);g.lineTo(PX[b],PY[b]);g.stroke()}}
 /* neurons: dim ones as tiny dots (far ones dimmer), lit ones as glowing sprites */
 LOBES.forEach(k=>{g.fillStyle=TINT[k];for(const i of LOBE_DEF[k].members){if(E[i]>.06)continue;const s=(1.6+PZ[i]*.6)*PF[i]*1.7;g.globalAlpha=.3+.6*(PZ[i]+1)/2;g.fillRect(PX[i]-s/2,PY[i]-s/2,s,s)}});g.globalAlpha=1;
 for(let i=0;i<NN;i++){const e=E[i];if(e<=.06)continue;const s=(7+20*e)*PF[i];g.globalAlpha=Math.min(1,.35+e);g.drawImage(sprite(COL[i]),PX[i]-s/2,PY[i]-s/2,s,s)}g.globalAlpha=1;
 /* signals travelling along axons */
 signals.forEach(s=>{const x=PX[s.a]+(PX[s.b]-PX[s.a])*s.t,y=PY[s.a]+(PY[s.b]-PY[s.a])*s.t,r=9*PF[s.a];g.globalAlpha=.9;g.drawImage(sprite(s.col),x-r/2,y-r/2,r,r)});g.globalAlpha=1;
 /* hand-off chain arcs with hop numbers */
 const hops=state.chain.slice(-8);hops.forEach((h,i)=>{if(i===0)return;const a=hops[i-1],b=h;if(!LOBE_DEF[a.lobe]||!LOBE_DEF[b.lobe]||a.lobe===b.lobe)return;const col=colorOf(b),pts=arcPoints(lobeCenter(a.lobe),lobeCenter(b.lobe),28),last=i===hops.length-1;
  g.strokeStyle=rgba(col,last?.85:.4);g.lineWidth=last?2.2:1.4;g.setLineDash([6,6]);g.lineDashOffset=-now/60;g.beginPath();pts.forEach((q,j)=>{proj(q[0],q[1],q[2],P);j?g.lineTo(P[0],P[1]):g.moveTo(P[0],P[1])});g.stroke();g.setLineDash([]);
  proj(pts[14][0],pts[14][1],pts[14][2],P);g.fillStyle="#04070c";g.strokeStyle=col;g.lineWidth=1.2;g.beginPath();g.arc(P[0],P[1],8,0,7);g.fill();g.stroke();g.fillStyle=col;g.font="bold 10px Segoe UI";g.textAlign="center";g.textBaseline="middle";g.fillText(String(b.seq),P[0],P[1]+.5)});
 for(let i=bolts.length-1;i>=0;i--){const b=bolts[i];b.t+=dt/.9;if(b.t>=1){bolts.splice(i,1);continue}const q=b.pts[Math.min(b.pts.length-1,(b.t*(b.pts.length-1))|0)];proj(q[0],q[1],q[2],P);const r=26*P[3];g.drawImage(sprite(b.col),P[0]-r/2,P[1]-r/2,r,r)}
 /* tokens (models / agents) sorted far to near; labels only for the ones doing something */
 const list=Object.values(tokens).map(k=>{proj(k.pos[0],k.pos[1],k.pos[2],P);k.sx=P[0];k.sy=P[1];k.sz=P[2];k.sf=P[3];return k}).sort((a,b)=>a.sz-b.sz);
 list.forEach(k=>{const on=k.state==="active",ol=k.state==="online",sb=k.state==="standby",pin=k.state==="pinned",off=k.state==="off"||k.state==="idle",r=(on?15:ol?11:pin?8:sb?7:9)*k.sf,pulse=on&&!REDUCED?1+.15*Math.sin(now/180):1;
  g.globalAlpha=off?.28:on?1:ol?.85:sb?.55:.55;g.drawImage(sprite(k.color),k.sx-r*pulse*1.6,k.sy-r*pulse*1.6,r*pulse*3.2,r*pulse*3.2);
  g.strokeStyle=k.state==="cooldown"?"#ffb020":k.color;g.lineWidth=pin?1:sb?1:1.6;if(pin||sb)g.setLineDash([2,2]);g.beginPath();g.arc(k.sx,k.sy,r*.7,0,7);g.stroke();g.setLineDash([]);g.globalAlpha=1;
  if(on||ol||pin||k.t<1){g.font=(on?"600 ":"")+"11px Segoe UI";g.textAlign="center";g.textBaseline="top";g.lineWidth=3;g.strokeStyle="#04070c";const tx=k.label.length>30?k.label.slice(0,29)+"…":k.label;g.strokeText(tx,k.sx,k.sy+r*.9+2);g.fillStyle=pin?"#9fd8ff":on?"#fff":"#bcd3e6";g.fillText(tx,k.sx,k.sy+r*.9+2)}});
 /* lobe labels with the firing percentage */
 LOBES.forEach((k,i)=>{const d=LOBE_DEF[k];proj(d.lab[0],d.lab[1],d.lab[2],P);const l=L[i],a=.35+.65*(P[2]+1)/2;g.globalAlpha=Math.min(1,a);g.font="600 10px Segoe UI";g.textAlign="center";g.textBaseline="middle";
  g.lineWidth=3;g.strokeStyle="#04070c";const t=d.name+(l.pct>=1?"  "+l.pct+"%":"");g.strokeText(t,P[0],P[1]);g.fillStyle=l.pct>=1?l.col:"#5d7f9c";g.fillText(t,P[0],P[1]);
  if(l.pct>=1){const w=54,x=P[0]-w/2,y=P[1]+8;g.fillStyle="#102535";g.fillRect(x,y,w,3);g.fillStyle=l.col;g.fillRect(x,y,w*Math.min(1,l.a),3)}g.globalAlpha=1});
}
function colorOf(h){const c=state.cards.find(x=>x.id===h.agent);return c?c.color:(LOBE_DEF[h.lobe]?L[LI[h.lobe]].col:"#8aa0b4")}
/* ================= tooltip ================= */
function hover(e){const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,bd=18;for(const k of Object.values(tokens)){const d=Math.hypot(k.sx-x,k.sy-y);if(d<bd){bd=d;best=k}}
 const t=$("#tip");if(!best||x<0||y<0||x>r.width||y>r.height){t.style.display="none";return}t.style.display="block";t.style.left=Math.min(innerWidth-300,e.clientX+14)+"px";t.style.top=(e.clientY+12)+"px";t.replaceChildren();
 const add=(s,b)=>{const d=document.createElement(b?"b":"span");d.textContent=s;t.append(d,document.createElement("br"))};add(best.name,1);
 if(best.card){const c=best.card;add(best.state+(c.why&&c.why!=="ok"?" - "+c.why:""));add("lobe "+best.lobe+" · "+c.cost+" · "+c.runs_today+"/"+c.daily_cap+" runs today");add("strengths: "+(c.modality||[]).join(", "));if((c.models||[]).length)add("models: "+c.models.slice(0,6).join(", "))}
 else if(best.pin)add("chosen for "+best.pin.function+" by "+(best.pin.by==="user"?"you":"an AI")+" ("+best.pin.size_gb+" GB)");
 else if(best.t){add((best.state||best.kind)+" in "+best.lobe);if(best.t.source)add("source "+best.t.source);if((best.t.strengths||[]).length)add("strengths: "+best.t.strengths.join(", "));if(best.mid)add("id "+best.mid)}
 else add(best.kind+" in "+best.lobe);
 if(best.task)add("working: "+(best.task.task_kind||"step")+" · difficulty "+Math.round((best.task.difficulty||0)*100)+"% · "+(best.task.why||""))}
/* ================= routing panel ================= */
function el(t,a,...k){const e=document.createElement(t);for(const[x,v]of Object.entries(a||{})){if(x==="style")e.style.cssText=v;else if(x==="c")e.style.setProperty("--c",v);else e.setAttribute(x,v)}k.flat().forEach(c=>e.append(c instanceof Node?c:document.createTextNode(c)));return e}
function panel(){const p=$("#panel");p.replaceChildren();const now=performance.now()/1000;
 p.append(el("h4",{},"Running now"));
 const tasks=state.tenants.filter(t=>t.kind==="task"||(t.kind==="window"&&t.working)||(t.kind==="model"&&t.working));
 const liveAgents=state.cards.filter(c=>c.state==="active"||c.state==="busy");
 if(!tasks.length&&!liveAgents.length)p.append(el("div",{class:"empty"},"The brain is at rest. Nothing is being worked on."));
 else if(!tasks.length&&liveAgents.length)p.append(el("div",{class:"empty"},"Agents are live on this machine - see glowing tokens. Agency has no open step yet."));
 tasks.forEach(t=>{
  const el_=Math.max(0,(t.t0!=null?(now-t.t0):(t.elapsed_s||0))),pct=Math.round((t._p||(t.kind==="model"?70:35))*100);
  const title=t.kind==="model"?(t.name||t.id):((t.agentName||t.agent||"")+(t.model&&t.model!==t.agentName&&t.task_kind!=="job"?" → "+t.model:""));
  const kind=t.task_kind==="process"?"external process":t.task_kind==="job"?(t.stale?"stale job":"job "+(t.job_state||"")):t.kind==="model"?"model hot":(t.task_kind||t.role||"task");
  const via=t.via==="external"?"external":t.via==="job"?"job board":(t.via||"");
  p.append(el("div",{class:"row",c:t.color||"#8aa0b4"},el("b",{},title+(t.job?" · "+t.job:"")),
   el("span",{class:"m"},(LOBE_DEF[t.lobe]?LOBE_DEF[t.lobe].name.toLowerCase():t.lobe)+" lobe · "+kind+(via?" · "+via:"")+(t.kind==="task"?" · "+Math.round(el_)+"s":"")),
   t.why?el("span",{class:"m"},t.why):"",t.kind==="task"?el("div",{class:"bar"},el("u",{style:"width:"+Math.min(100,pct)+"%"})):"",
   t.kind==="task"?el("span",{class:"m"},(t.external?"live · ":"")+"difficulty "+Math.round((t.difficulty||.5)*100)+"%"):el("span",{class:"m"},"working")))});
 liveAgents.filter(c=>!tasks.some(t=>t.agent===c.id)).forEach(c=>{
  p.append(el("div",{class:"row",c:c.color||"#8aa0b4"},el("b",{},c.name),
   el("span",{class:"m"},(c.region||"")+" · "+(c.processes||0)+" process(es)"+(c.cpu?(" · "+Math.round(c.cpu)+"% CPU"):"")),
   el("span",{class:"m"},c.why||"live on this machine")));
 });
 p.append(el("h4",{},"Hand-off chain"+(state.strand?" · "+state.strand.job.id:"")));if(!state.chain.length)p.append(el("div",{class:"empty"},"No hand-offs yet."));
 state.chain.slice(-10).forEach(h=>p.append(el("div",{class:"hop",c:colorOf(h)},el("i",{},String(h.seq)),el("span",{},(h.kind==="step"?"step "+h.step:h.kind)+" → "+(h.model||h.agent)+" ("+h.lobe+")"+(h.outcome?" · "+h.outcome:"")+(h.secs?" · "+Math.round(h.secs)+"s":"")))));
 p.append(el("h4",{},"Chosen for each function"));if(!state.pins.length)p.append(el("div",{class:"empty"},"None chosen: the AI picks the best match. Choose on the models page."));
 state.pins.forEach(x=>p.append(el("div",{class:"hop",c:"#9fd8ff"},el("i",{},"·"),el("span",{},x.function+": "+x.name+" ("+(x.by==="user"?"you":"AI")+")")))) }
/* ================= legend + ticker ================= */
function legend(){const l=$("#legend");l.replaceChildren();state.cards.forEach(c=>{const d=document.createElement("span");d.className="lg "+c.state;d.style.setProperty("--c",c.color);d.title=c.why;const i=document.createElement("i");d.append(i,c.name+" · "+c.state);l.append(d)})}
function ticker(){const t=$("#ticker");t.replaceChildren();state.pulses.slice(-6).reverse().forEach(p=>{const c=(state.cards.find(x=>x.id===p.agent)||{}).color||"#e6edf3",s=document.createElement("span");s.style.setProperty("--c",c);s.append(document.createElement("i"),p.text);t.append(s)})}
/* ================= DNA strand (canvas): one segment per task; fills in as the pipe completes ================= */
const dv=$("#dnac"),cx=dv.getContext("2d");let CW=0,CH=0;
function sizeDna(){const d=Math.min(2,devicePixelRatio||1);CW=dv.clientWidth;CH=dv.clientHeight;dv.width=CW*d;dv.height=CH*d;cx.setTransform(d,0,0,d,0,0)}
let sealed=0,lastJob=null,lastKey="";
function drawDna(now){cx.clearRect(0,0,CW,CH);const s=state.strand,t=now/1000*(REDUCED?0:1.1),segs=s?s.segments:[],n=Math.max(segs.length,6),top=18,bot=CH-18,h=(bot-top)/n,amp=CW*.34,mid=CW/2;
 const lab=$("#dnalabels");if(lastKey!==JSON.stringify(segs.map(x=>x.label+x.agent))){lastKey=JSON.stringify(segs.map(x=>x.label+x.agent));lab.replaceChildren();
  segs.forEach((x,i)=>{const d=document.createElement("div");d.style.top=(top+h*(i+.5))+"px";d.textContent=x.kind==="step"?(x.label+(x.agent?" · "+x.agent.replace("-cli","").replace("-code",""):"")+(x.rework?" ✱":"")):x.label;d.title=x.objective||x.label;d.style.color=x.color;d.style.opacity=.85;lab.append(d)})}
 const pos=(y,ph)=>({x:mid+Math.sin(y*.045+t+ph)*amp,z:Math.cos(y*.045+t+ph)});
 for(let side=0;side<2;side++){const ph=side?Math.PI:0;for(let i=0;i<segs.length;i++){const sg=segs[i],y0=top+h*i,y1=y0+h,col=sg.state==="todo"?"#2b4258":sg.state==="failed"?"#ff6b5e":sg.color,live=sg.state==="running"||sg.state==="waiting",pul=live?.55+.45*Math.sin(now/260):1;
   cx.beginPath();let first=true;for(let y=y0+1;y<=y1-1;y+=3){if(sg.state==="failed"&&y>y0+h*.38&&y<y0+h*.62){first=true;continue}const p=pos(y,ph);if(first){cx.moveTo(p.x,y);first=false}else cx.lineTo(p.x,y)}
   cx.strokeStyle=col;cx.globalAlpha=(sg.state==="todo"?.55:.95)*pul;cx.lineWidth=side?2.2:2.8;cx.shadowColor=col;cx.shadowBlur=sg.state==="todo"?0:10;cx.stroke();cx.shadowBlur=0;cx.globalAlpha=1}}
 for(let i=0;i<segs.length;i++){const sg=segs[i],col=sg.state==="todo"?"#2b4258":sg.state==="failed"?"#ff6b5e":sg.color,k=Math.max(3,Math.round(h/9));
  for(let j=0;j<k;j++){const y=top+h*i+h*(j+.5)/k,a=pos(y,0),b=pos(y,Math.PI),fade=.35+.65*Math.abs(a.z);
   if(sg.state==="failed"&&j>k*.35&&j<k*.65){cx.fillStyle="#ff6b5e";cx.globalAlpha=.5+.5*Math.random();cx.fillRect(a.x+(b.x-a.x)*Math.random()-1,y-1,2,2);cx.globalAlpha=1;continue}
   const grd=cx.createLinearGradient(a.x,y,b.x,y);grd.addColorStop(0,col);grd.addColorStop(1,sg.state==="todo"?col:"#e6edf3");cx.strokeStyle=grd;cx.globalAlpha=(sg.state==="todo"?.35:.8)*fade*(sg.state==="running"?.6+.4*Math.sin(now/200+j):1);cx.lineWidth=1.6;cx.beginPath();cx.moveTo(a.x,y);cx.lineTo(b.x,y);cx.stroke();
   cx.globalAlpha=1;cx.fillStyle=col;cx.shadowColor=col;cx.shadowBlur=sg.state==="todo"?0:6;cx.beginPath();cx.arc(a.x,y,1.9+a.z*.8,0,7);cx.fill();cx.beginPath();cx.arc(b.x,y,1.9+b.z*.8,0,7);cx.fill();cx.shadowBlur=0}}
 if(s&&s.complete){if(lastJob!==s.job.id){sealed=now;lastJob=s.job.id}const a=Math.max(0,1-(now-sealed)/2500);cx.fillStyle="rgba(123,240,196,"+(.1+.2*a)+")";cx.fillRect(0,0,CW,CH)}else if(s)lastJob=null;
 if(!s){cx.fillStyle="#37526b";cx.font="11px Segoe UI";cx.textAlign="center";cx.fillText("no jobs yet",mid,CH/2)}}
/* ================= data: the real broker, or a demo ================= */
const LOBE_COLOR={frontal:"#e8793a",parietal:"#3ddc84",occipital:"#ff4d9d",temporal:"#4da3ff",cerebellum:"#b57bff",broca:"#ffd34d",auditory:"#ff9a1a",limbic:"#e6edf3",brainstem:"#e6edf3"};
const t0s={};/* when each running task was first seen, so its timer keeps running between polls */
function ingest(r){const byId={};r.agents.forEach(a=>byId[a.id]=a);const now=performance.now()/1000,seen={};
 state.tenants=(r.tenants||[]).map(t=>{const o=Object.assign({},t),key=t.kind+":"+(t.job||"")+":"+(t.step||"")+":"+(t.id||t.agent||t.name),a=byId[t.agent];seen[key]=1;
  /* keep each model's own colour (OpenRouter / Gemini / Claude tints); fall back to the agent or the lobe */
  o.color=t.color||(a?a.color:(t.kind==="window"?"#ff4d9d":LOBE_COLOR[t.lobe]||"#8aa0b4"));o.agentName=a?a.name:(t.agent||t.source||"");
  if(t.kind==="task"){t0s[key]=t0s[key]||(now-(t.elapsed_s||0))}else if(t.kind==="window"&&t.working){t0s[key]=t0s[key]||now}
  else if(t.kind==="model"&&t.working){t0s[key]=t0s[key]||now}
  o.t0=t0s[key]||now;return o});Object.keys(t0s).forEach(k=>{if(!seen[k])delete t0s[k]})}
function apply(r){const sig=JSON.stringify(r.agents.map(a=>[a.id,a.region,a.color,a.name]));if(sig!==state.sig){state.sig=sig}state.cards=r.agents;ingest(r);state.pins=r.pins||[];
 const prev=state.chain.length;state.chain=r.chain||[];state.strand=r.strand;state.cursor=r.cursor||state.cursor;
 if(state.chain.length>prev&&prev>0){const a=state.chain[state.chain.length-2],b=state.chain[state.chain.length-1];if(a&&b)bolt(a.lobe,b.lobe,colorOf(b))}
 const c=r.counts||{},tot=Object.values(c).reduce((a,b)=>a+b,0);$("#counts").textContent=DEMO?"demo mode":tot?Object.entries(c).map(([k,v])=>k+" "+v).join(" · "):"no jobs yet";
 $("#kill").style.display=r.killed?"inline-block":"none";const m=$("#merge");m.className="chip "+(r.auto_merge?"warn":"ok");m.textContent=r.auto_merge?"auto-merge ON":"auto-merge off";
 (r.pulses||[]).forEach(p=>{state.pulses.push(p);const to=p.lobe||((state.cards.find(x=>x.id===p.agent)||{}).region);if(p.kind==="route"&&p.lobe&&state.chain.length<2)bolt("brainstem",p.lobe,"#e6edf3");else if(to&&p.agent!=="broker"&&!/state|job/.test(p.kind))bolt("brainstem",to,(state.cards.find(x=>x.id===p.agent)||{}).color||"#e6edf3")});
 state.pulses=state.pulses.slice(-40);syncTokens();legend();ticker();panel();drawLog(r.activity_log||[])}
function drawLog(rows){const box=$("#dnalog");if(!box)return;const head=el("h5",{},"Activity");
 if(!rows.length){box.replaceChildren(head,el("div",{class:"muted"},"quiet - no recent agency / watchdog lines"));return}
 const lines=rows.slice(-28).reverse().map(r=>{
  const t=r.ts?new Date((r.ts>1e12?r.ts:r.ts*1000)).toLocaleTimeString():"";
  const warn=/(?<!no )\bstuck\b|blocked|failed|held|park/i.test(r.text||"");
  const ok=/retry|reroute|reclaim|merged|clear|no stuck|watching/i.test(r.text||"");
  const cls="lgline"+(r.source==="watchdog"?" watchdog":"")+(warn?" warn":ok?" ok":"");
  return el("div",{class:cls},t+"  "+(r.job?r.job+" · ":"")+(r.text||""))});
 box.replaceChildren(head,...lines);box.scrollTop=0}
async function poll(){if(DEMO)return;try{const q="/api/brain?event="+state.cursor.event+"&ts="+state.cursor.ts+(state.jobsel?"&job="+encodeURIComponent(state.jobsel):"");apply(await (await fetch(q)).json())}catch(e){$("#counts").textContent="broker not answering"}}
async function jobs(){if(DEMO)return;try{const r=await (await fetch("/api/jobs?limit=30")).json(),sel=$("#jobsel"),cur=sel.value;sel.replaceChildren(Object.assign(document.createElement("option"),{value:"",textContent:"latest job"}));(r.jobs||[]).forEach(j=>sel.append(Object.assign(document.createElement("option"),{value:j.id,textContent:j.id+" "+j.title.slice(0,28)+" ["+j.state+"]"})));sel.value=cur}catch(e){}}
$("#jobsel").onchange=e=>{state.jobsel=e.target.value;poll()};
$("#demo").onclick=()=>{const u=new URL(location.href);if(DEMO)u.searchParams.delete("demo");else u.searchParams.set("demo","1");location.href=u};if(DEMO)$("#demo").classList.add("on");
/* ---- demo: a scripted ~90 second story (the same every time) that shows every kind of model finding its lobe, easy and hard tasks, a failed check that sends work back, and a finished pipe. Captions describe each moment as it happens; the Record button saves the whole thing as a video. ---- */
const DEMO_AGENTS=[["claude-code","Claude Code","frontal","#e8793a"],["gemini-cli","Gemini CLI","temporal","#4da3ff"],["codex-cli","Codex CLI","parietal","#3ddc84"],["ollama-local","Local Ollama","cerebellum","#b57bff"],
  ["grok-cli","Grok CLI","limbic","#e6edf3"],["pull-agent","pull agent","limbic","#b9c4cf"],["video-gen","Wan video","occipital","#ff4d9d"],["voice","PersonaPlex 7B","broca","#ffd34d"],["music","ACE-Step music","auditory","#ff9a1a"],["vision","Qwen2.5-VL","occipital","#7bf0c4"]];
/* each hop: starts at t (seconds), who takes it, in which lobe, how hard it is, how long it is expected to take; "fail" = its check fails and the work is handed back */
const DEMO_HOPS=[
 {t:4,kind:"plan",agent:"claude-code",lobe:"frontal",why:"planning needs the strongest reasoner",difficulty:.5,expected_s:8},
 {t:12,kind:"step",agent:"gemini-cli",lobe:"temporal",why:"research and writing: long context, free tier",difficulty:.55,expected_s:9},
 {t:21,kind:"step",agent:"ollama-local",lobe:"cerebellum",why:"a tiny wording fix: the local model is free and instant",difficulty:.18,expected_s:5},
 {t:27,kind:"step",agent:"video-gen",lobe:"occipital",why:"needs video: only the Wan window can make it",difficulty:.88,expected_s:15},
 {t:43,kind:"step",agent:"voice",lobe:"broca",why:"speech: PersonaPlex 7B, the model you chose",difficulty:.5,expected_s:8},
 {t:52,kind:"step",agent:"music",lobe:"auditory",why:"needs a soundtrack: ACE-Step",difficulty:.65,expected_s:8},
 {t:61,kind:"check",agent:"vision",lobe:"occipital",why:"look at the rendered frames: vision model",difficulty:.4,expected_s:6,fail:true},
 {t:68,kind:"step",agent:"video-gen",lobe:"occipital",why:"the check failed: back to the video model with the reviewer's note",difficulty:.72,expected_s:8,rework:true},
 {t:77,kind:"review",agent:"codex-cli",lobe:"frontal",why:"a different model than the author reviews the work",difficulty:.45,expected_s:6},
];
const DEMO_END=88,DEMO_LOOP=96;
const DEMO_CAPTIONS=[[0,"A job arrives at the broker: \"make a 15 second trailer with a voiceover and music\"."],[2,"The broker (brainstem) wakes up and decides who should do each part."],
 [4,"Hand-off 1: PLAN goes to Claude Code in the frontal lobe. Planning is hard work, so the lobe lights up widely."],[12,"Hand-off 2: writing the script needs long context, so Gemini CLI takes it in the temporal lobe."],
 [21,"Hand-off 3: a tiny wording fix. The free local model in the cerebellum handles it with a short burst of firing."],[27,"Hand-off 4: the clip needs video. The Wan video window takes the task in the occipital lobe. Hard task: a wide, long burn."],
 [43,"Hand-off 5: the voiceover goes to PersonaPlex 7B in the speech lobe, the model you pinned on the models page."],[52,"Hand-off 6: the soundtrack goes to the music model in the audio lobe."],
 [61,"Check: a vision model looks at the rendered frames. It finds a problem and the check FAILS."],[68,"Hand-off 8: the work goes back to the video model with the reviewer's note (marked with a star on the strand)."],
 [77,"Review: a different model (Codex CLI) reviews the result in the frontal lobe."],[84,"Review passes. The strand glows: the job is ready for you to approve."],[90,"The brain returns to rest. The demo starts again."]];
function demo(){const cards=DEMO_AGENTS.map(([id,name,region,color])=>({id,name,region,color,kind:"cli",cost:"plan",modality:[region],state:"idle",why:"demo",runs_today:3,daily_cap:20,processes:0}));
 let t0=performance.now()/1000,capI=-1;window.DEMO_LOG=[];window.demoT=()=>performance.now()/1000-t0;
 window.demoRestart=()=>{t0=performance.now()/1000;capI=-1;window.DEMO_LOG=[]};
 function snap(){const now=demoT()%DEMO_LOOP,tenants=[],chain=[],segs=[{kind:"plan",label:"plan",state:now>=12?"done":"running",color:"#e6edf3"}];
  let cur=null,seq=0;DEMO_HOPS.forEach((h,i)=>{if(h.t>now)return;seq++;const end=(DEMO_HOPS[i+1]||{t:DEMO_END}).t,running=now<end,c=cards.find(x=>x.id===h.agent);if(running)cur=h;
   chain.push({seq,kind:h.kind,step:h.kind==="step"?seq-1:0,agent:h.agent,lobe:h.lobe,model:c.name,why:h.why,difficulty:h.difficulty,outcome:running?null:h.fail?"failed":"ok",secs:running?null:end-h.t});
   if(h.kind==="step")segs.push({kind:"step",label:"step "+(segs.filter(s=>s.kind==="step").length+1),state:running?"running":"done",color:c.color,agent:h.agent,objective:h.why,rework:!!h.rework});
   if(h.kind==="check"){segs.push({kind:"check",label:"check",state:running?"running":"failed",color:"#19f0c8"})}});
  if(!segs.some(s=>s.kind==="check"))segs.push({kind:"check",label:"check",state:"todo",color:"#19f0c8"});
  const rev=DEMO_HOPS[8],revT=now>=rev.t,done=now>=84;segs.push({kind:"review",label:"review",state:done?"done":revT?"running":"todo",color:"#ffd34d"},{kind:"deliver",label:"deliver",state:done?"waiting":"todo",color:"#7bf0c4"});
  const chk=segs.find(s=>s.kind==="check");if(done&&chk&&chk.state==="failed")chk.state="done";
  if(cur&&now<DEMO_END)tenants.push({kind:"task",agent:cur.agent,job:"DEMO-1",step:seq,task_kind:cur.kind,lobe:cur.lobe,model:cards.find(x=>x.id===cur.agent).name,elapsed_s:now-cur.t,expected_s:cur.expected_s,difficulty:cur.difficulty,via:"push",why:cur.why});
  tenants.push({kind:"model",name:"qwen2.5-coder:14b",lobe:"parietal",source:"ollama"},{kind:"model",name:"gemma3:4b",lobe:"cerebellum",source:"ollama"});
  const cs=cards.map(c=>Object.assign({},c,{state:cur&&now<DEMO_END&&cur.agent===c.id?"active":c.id==="pull-agent"?"online":"idle"}));
  return {ok:true,agents:cs,tenants,pins:[{function:"speech-duplex",name:"nvidia/personaplex-7b-v1",by:"user",lobe:"broca",size_gb:15.95},{function:"vision",name:"Qwen2.5-VL 7B",by:"ai",lobe:"occipital",size_gb:6}],chain,pulses:[],cursor:{event:0,ts:0},
   counts:{running:now<84?1:0,ready:now>=84?1:0},auto_merge:false,killed:false,strand:{job:{id:"DEMO-1",title:"15 s trailer with voiceover and music",state:now>=84?"ready":"running",why:""},segments:segs,complete:false}}}
 let lastNow=0;function tick(){const now=demoT()%DEMO_LOOP;if(now<lastNow)capI=-1;lastNow=now;while(capI+1<DEMO_CAPTIONS.length&&now>=DEMO_CAPTIONS[capI+1][0]){capI++;caption(DEMO_CAPTIONS[capI][1],DEMO_CAPTIONS[capI][0])}apply(snap())}
 state.strand={complete:false,segments:[],job:{id:"DEMO-1"}};tick();setInterval(tick,250)}
function caption(text,at){const c=$("#cap");c.textContent=text;c.style.opacity=1;(window.DEMO_LOG=window.DEMO_LOG||[]).push({t:Math.round((at===undefined?0:at)*10)/10,text});state.pulses.push({agent:"broker",kind:"state",text});state.pulses=state.pulses.slice(-40);ticker()}
/* ================= record the demo as a video: the brain, the strand, the routing panel and the captions are drawn into one 1280x720 picture and saved (WebM; the broker's tools convert it to MP4) ================= */
function wrapText(r,text,x,y,w,lh){const words=text.split(" ");let line="";for(const wd of words){const t=line?line+" "+wd:wd;if(r.measureText(t).width>w&&line){r.fillText(line,x,y);y+=lh;line=wd}else line=t}if(line){r.fillText(line,x,y);y+=lh}return y}
function composeFrame(r,secs){r.fillStyle="#04070c";r.fillRect(0,0,1280,720);const gr=r.createRadialGradient(660,330,40,660,330,620);gr.addColorStop(0,"#0b1a2a");gr.addColorStop(1,"#02040a");r.fillStyle=gr;r.fillRect(0,0,1280,720);
 const ar=cv.width/cv.height,bw=880,bh=bw/ar;r.drawImage(cv,205+(880-bw)/2,64+(560-bh)/2,bw,bh);r.drawImage(dv,1130,70,146,540);
 r.textBaseline="top";r.font="600 18px Segoe UI";r.fillStyle="#e8b556";r.fillText("N E U R A L   E N G I N E",20,16);r.font="13px Segoe UI";r.fillStyle="#9fc4e0";r.textAlign="right";
 const m=Math.floor(secs/60),s=Math.floor(secs%60);r.fillText("demo  T+"+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0"),1260,18);r.textAlign="left";
 /* the routing panel */
 let y=60;const now=performance.now()/1000;r.font="600 11px Segoe UI";r.fillStyle="#e8b556";r.fillText("RUNNING NOW",20,y);y+=18;r.font="13px Segoe UI";
 const tasks=state.tenants.filter(t=>t.kind==="task");if(!tasks.length){r.fillStyle="#4f6b83";r.fillText("at rest",20,y);y+=22}
 tasks.forEach(t=>{r.fillStyle=t.color;r.fillRect(20,y,3,52);r.fillStyle="#fff";r.font="600 13px Segoe UI";r.fillText(t.agentName+(t.model&&t.model!==t.agentName?" → "+t.model:""),30,y);r.font="11px Segoe UI";r.fillStyle="#7f9bb3";r.fillText(t.lobe+" lobe · "+t.task_kind+" · "+Math.round(Math.max(0,now-t.t0))+"s of ~"+Math.round(t.expected_s)+"s",30,y+16);
  r.fillStyle="#12283b";r.fillRect(30,y+33,230,4);r.fillStyle=t.color;r.fillRect(30,y+33,230*Math.min(1,t._p||0),4);r.fillStyle="#7f9bb3";r.fillText("firing "+Math.round((t._p||0)*100)+"% · difficulty "+Math.round((t.difficulty||0)*100)+"%",30,y+40);y+=64});
 y+=6;r.font="600 11px Segoe UI";r.fillStyle="#e8b556";r.fillText("HAND-OFF CHAIN",20,y);y+=18;r.font="12px Segoe UI";
 state.chain.slice(-9).forEach(h=>{const c=colorOf(h);r.fillStyle=c;r.beginPath();r.arc(28,y+7,8,0,7);r.fill();r.fillStyle="#04070c";r.font="bold 10px Segoe UI";r.textAlign="center";r.fillText(String(h.seq),28,y+2);r.textAlign="left";
  r.font="12px Segoe UI";r.fillStyle=h.outcome==="failed"?"#ff8a7e":"#cfe3f3";r.fillText((h.kind==="step"?"step":h.kind)+" → "+h.model+(h.outcome?" ("+h.outcome+")":""),42,y);y+=19});
 y+=6;r.font="600 11px Segoe UI";r.fillStyle="#e8b556";r.fillText("CHOSEN MODELS",20,y);y+=18;r.font="12px Segoe UI";r.fillStyle="#9fd8ff";state.pins.forEach(p=>{r.fillText(p.function+": "+p.name.replace("nvidia/",""),20,y);y+=17});
 /* strand labels */
 const segs=state.strand?state.strand.segments:[],n=Math.max(segs.length,6),top=70+18,h=(540-36)/n;r.font="11px Segoe UI";r.textAlign="right";segs.forEach((sg,i)=>{r.fillStyle=sg.color;r.fillText(sg.kind==="step"?sg.label+(sg.rework?" ✱":""):sg.label,1124,top+h*(i+.5)-6)});r.textAlign="left";
 /* caption */
 const cap=$("#cap").textContent;if(cap){r.font="16px Segoe UI";const lines=[];let w=1000;r.fillStyle="#06101bdd";r.strokeStyle="#2b5a85";const bx=140,by=640;r.fillRect(bx,by,1000,64);r.strokeRect(bx,by,1000,64);r.fillStyle="#fff";r.textAlign="center";
  const words=cap.split(" ");let line="",ly=by+10;for(const wd of words){const t=line?line+" "+wd:wd;if(r.measureText(t).width>960&&line){r.fillText(line,640,ly);ly+=22;line=wd}else line=t}r.fillText(line,640,ly);r.textAlign="left"}}
function recordDemo(){const R=document.createElement("canvas");R.width=1280;R.height=720;const r=R.getContext("2d"),stream=R.captureStream(30),chunks=[];
 let mt="video/webm;codecs=vp9";if(!MediaRecorder.isTypeSupported(mt))mt="video/webm";const mr=new MediaRecorder(stream,{mimeType:mt,videoBitsPerSecond:6e6});mr.ondataavailable=e=>e.data.size&&chunks.push(e.data);
 const btn=$("#rec");btn.disabled=true;demoRestart();yaw=.7;spin=true;const z0=zoom;zoom=1.12;$("#spin").classList.add("on");$("#panel").style.display="none";const t0=demoT();
 driven=true;const iv=setInterval(()=>{const s=demoT()-t0;frame(performance.now());composeFrame(r,s);btn.textContent="Recording "+Math.round(s)+"s";if(s>=DEMO_LOOP-1){clearInterval(iv);mr.stop()}},33);
 mr.onstop=()=>{zoom=z0;driven=false;requestAnimationFrame(frame);const blob=new Blob(chunks,{type:"video/webm"});btn.disabled=false;btn.textContent="Record";$("#panel").style.display="";if(window.onRecorded)window.onRecorded(blob,window.DEMO_LOG);else{const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="neural-engine-demo.webm";a.click()}};
 mr.start(1000)}
if(DEMO){$("#rec").style.display="";$("#rec").onclick=recordDemo}
/* ================= go ================= */
let last=performance.now(),driven=false;
function frame(nowMs){const dt=Math.min(.1,(nowMs-last)/1000);last=nowMs;if(spin&&!dragging)yaw+=dt*.22;sim(dt);stepTokens(dt);draw(nowMs,dt);drawDna(nowMs);if(!driven)requestAnimationFrame(frame)}
addEventListener("resize",()=>{size();sizeDna()});size();sizeDna();
if(DEMO)demo();else{poll();jobs();setInterval(poll,1500);setInterval(jobs,10000);setInterval(panel,1000)}
requestAnimationFrame(frame);
</script></body></html>
"""
