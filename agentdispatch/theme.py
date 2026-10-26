"""the look shared by every AgentDispatch page: a Tron-style theme (dark glass, neon cyan lines and glow, orange accents) and one navigation bar on top of each page, with the owner key, the connection light
and a "New job" button. `apply(html, active)` puts both into a page that has its own styles: the theme comes after the page's CSS so it wins, and the page keeps only its own layout."""

TABS = [  # key, label, link, hint, icon
    ("home", "Home", "/", "command centre: talk through a job start to finish, track ops, see projects", "&#9670;"),
    ("board", "Board", "/board", "the notice board: problems the AIs are stuck on, and what fixed them", "&#9672;"),
    ("agency", "Agency", "/agency", "jobs: submit work, watch it move, read the result, approve it", "&#9654;"),
    ("campaign", "Campaign", "/campaign", "the campaign gates: what is closed, open or locked, and what needs your decision", "&#9776;"),
    ("brain", "Brain", "/brain", "the live 3D brain: which model is working in which lobe", "&#10040;"),
    ("models", "Models", "/models", "every AI model on this computer, and which one is used for what", "&#9638;"),
    ("tools", "Tools", "/tools", "what this computer can run for the models: calculations, Lean, tests; install what is missing", "&#9881;"),
    ("library", "Library", "/library", "every finished workflow, sorted, with its failure points: what worked and what did not", "&#9783;"),
    ("engine", "Engine room", "ENGINE", "gauges for the GPUs, limits and the connected AI agents", "&#9678;"),
]

THEME_CSS = r"""
:root{--bg:#02070d;--cy:#00e5ff;--cy2:#19f0c8;--or:#ff9a1a;--tx:#d6f6ff;--dim:#6aa9bd;--line:rgba(0,229,255,.28);--glow:0 0 10px rgba(0,229,255,.35);--nav:44px}
html{color-scheme:dark}
body{background:#02070d;background-image:radial-gradient(ellipse at 50% -25%,#073650 0,transparent 62%),linear-gradient(rgba(0,229,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,255,.045) 1px,transparent 1px);background-size:auto,48px 48px,48px 48px;background-attachment:fixed;color:var(--tx);padding-top:var(--nav)}
a{color:#4fd8ff}a:hover{color:#fff;text-shadow:0 0 8px var(--cy)}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:rgba(0,229,255,.3);border-radius:0}::-webkit-scrollbar-thumb:hover{background:rgba(0,229,255,.6)}
header{background:linear-gradient(180deg,rgba(0,46,68,.82),rgba(0,18,30,.82))!important;border-bottom:1px solid var(--line)!important;box-shadow:0 1px 14px rgba(0,229,255,.12)}
header h1{color:var(--cy)!important;text-shadow:0 0 12px rgba(0,229,255,.7)!important;text-transform:uppercase;letter-spacing:.32em!important;font-weight:600}
.chip{background:rgba(0,229,255,.06)!important;border:1px solid var(--line)!important;color:#9be9ff!important;border-radius:2px!important;letter-spacing:.04em}
.chip.warn{background:rgba(255,154,26,.1)!important;border-color:rgba(255,154,26,.6)!important;color:#ffc27a!important}
.chip.ok{background:rgba(25,240,200,.08)!important;border-color:rgba(25,240,200,.5)!important;color:#7ff5dc!important}
input,select,textarea{background:rgba(0,10,18,.85)!important;border:1px solid var(--line)!important;border-radius:2px!important;color:var(--tx)!important}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--cy)!important;box-shadow:var(--glow)}
button{background:rgba(0,229,255,.07)!important;border:1px solid rgba(0,229,255,.5)!important;border-radius:2px!important;color:#aeefff!important;text-transform:uppercase;letter-spacing:.08em;font-size:11px;font-weight:600;transition:box-shadow .15s,background .15s}
button:hover:not(:disabled){background:rgba(0,229,255,.2)!important;box-shadow:0 0 12px rgba(0,229,255,.55);color:#fff!important}
button.on{background:rgba(0,229,255,.28)!important;box-shadow:var(--glow);color:#fff!important}
button.go{border-color:var(--cy2)!important;color:var(--cy2)!important;background:rgba(25,240,200,.08)!important}button.go:hover:not(:disabled){box-shadow:0 0 12px rgba(25,240,200,.6);background:rgba(25,240,200,.22)!important}
button.bad{border-color:rgba(255,120,80,.75)!important;color:#ff9b7a!important;background:rgba(255,100,60,.08)!important}
button:disabled{opacity:.35}
h2{color:var(--cy);text-shadow:0 0 10px rgba(0,229,255,.4);letter-spacing:.06em}
h3,#panel h4{color:var(--or)!important;letter-spacing:.18em}
table th{color:var(--dim)!important;text-transform:uppercase;letter-spacing:.08em;font-size:10px!important}td,th{border-bottom:1px solid rgba(0,229,255,.12)!important}
tr:hover td{background:rgba(0,229,255,.06)!important}
pre,#detail{background:#010a12!important;border:1px solid var(--line)!important;border-radius:2px!important}
.job,.ag,.slot,.item,#form,#panel{background:rgba(0,16,26,.78)!important;border:1px solid var(--line)!important;border-radius:2px!important}
.job:hover,.item:hover,.slot:hover{border-color:var(--cy)!important;box-shadow:var(--glow)}
.job.sel{border-color:var(--or)!important;box-shadow:0 0 12px rgba(255,154,26,.35)}
.slot.set{border-color:var(--cy2)!important;background:rgba(25,240,200,.07)!important;box-shadow:0 0 10px rgba(25,240,200,.2)}
#left{border-right:1px solid var(--line)!important}
#toast{background:rgba(0,26,40,.95)!important;border:1px solid var(--cy)!important;box-shadow:var(--glow);border-radius:2px!important}
.job .st,#detail .st,#right .st{border:1px solid currentColor}.st{border-radius:2px!important}
header h1{white-space:nowrap}
.muted,.dim{color:var(--dim)!important}
#tok,#savetok{display:none!important}
/* ---- the navigation bar ---- */
#sbnav{position:fixed;top:0;left:0;right:0;height:var(--nav);z-index:60;display:flex;align-items:stretch;gap:2px;padding:0 10px 0 0;background:linear-gradient(180deg,#031a28,#020e17);border-bottom:1px solid rgba(0,229,255,.55);box-shadow:0 0 18px rgba(0,229,255,.25);font:600 12px/1 Segoe UI,system-ui,sans-serif;user-select:none}
#sbnav .logo{display:flex;align-items:center;gap:8px;padding:0 18px 0 16px;color:var(--cy);text-decoration:none;letter-spacing:.34em;font-size:13px;text-shadow:0 0 10px rgba(0,229,255,.8);border-right:1px solid var(--line);white-space:nowrap}
#sbnav .logo b{font-size:15px;letter-spacing:0}
#sbnav .tabs{display:flex;align-items:stretch;overflow-x:auto;scrollbar-width:none}
#sbnav .tabs a{display:flex;align-items:center;gap:7px;padding:0 15px;color:#7fbfd4;text-decoration:none;text-transform:uppercase;letter-spacing:.14em;font-size:11px;white-space:nowrap;border-bottom:2px solid transparent;transition:color .15s,background .15s}
#sbnav .tabs a i{font-style:normal;font-size:13px;opacity:.8}
#sbnav .tabs a:hover{color:#fff;background:rgba(0,229,255,.08);text-shadow:0 0 8px var(--cy)}
#sbnav .tabs a.act{color:var(--cy);border-bottom-color:var(--cy);background:linear-gradient(180deg,transparent,rgba(0,229,255,.14));text-shadow:0 0 10px rgba(0,229,255,.8);box-shadow:inset 0 -10px 14px -10px rgba(0,229,255,.7)}
#sbnav .sp{flex:1}
#sbnav .cta{white-space:nowrap;align-self:center;margin:0 10px;padding:6px 13px;border:1px solid var(--or);color:var(--or)!important;text-decoration:none;text-transform:uppercase;letter-spacing:.12em;font-size:11px;border-radius:2px;box-shadow:0 0 8px rgba(255,154,26,.25)}
#sbnav .cta:hover{background:rgba(255,154,26,.2);box-shadow:0 0 14px rgba(255,154,26,.6);color:#fff!important;text-shadow:none}
#sbnav .st2{align-self:center;display:flex;align-items:center;gap:7px;color:var(--dim);font-size:10px;letter-spacing:.1em;margin-right:8px;white-space:nowrap}
#sbled{width:8px;height:8px;border-radius:50%;background:#ff9a1a;box-shadow:0 0 8px #ff9a1a}#sbled.up{background:var(--cy2);box-shadow:0 0 9px var(--cy2);animation:sbp 2.4s ease-in-out infinite}
@keyframes sbp{50%{opacity:.45}}
#sbkey{white-space:nowrap;align-self:center;padding:5px 10px!important}#sbkey.set{border-color:var(--cy2)!important;color:var(--cy2)!important}
#sbkeybox{position:fixed;top:var(--nav);right:10px;width:290px;padding:12px;background:rgba(2,14,23,.97);border:1px solid var(--cy);box-shadow:0 0 18px rgba(0,229,255,.35);display:none;z-index:70;font:12px/1.45 Segoe UI,sans-serif;color:var(--tx)}
#sbkeybox p{margin:0 0 8px;color:var(--dim)}#sbkeybox input{width:100%;margin-bottom:8px}#sbkeybox .r{display:flex;gap:6px}
@media (max-width:1100px){#sbnav .logo span,#sbnav .st2 span{display:none}#sbnav .logo{padding:0 12px}#sbnav .tabs a{padding:0 10px;letter-spacing:.06em}#sbnav .tabs a i{display:none}}
@media (max-width:640px){#sbnav .cta{padding:6px 8px;margin:0 4px}#sbnav .tabs a{padding:0 7px;font-size:10px}}
@media (prefers-reduced-motion:reduce){#sbled.up{animation:none}}
"""

NAV_HTML = r"""<nav id="sbnav"><a class="logo" href="/" title="AgentDispatch home"><b>&#9698;&#9699;</b><span>AGENTDISPATCH</span></a><div class="tabs">%TABS%</div><span class="sp"></span>
<a class="cta" href="/" title="command centre: describe a job and work it start to finish">+ New job</a><span class="st2"><i id="sbled"></i><span id="sbver">connecting</span></span><button id="sbkey" title="your owner key: needed to approve, choose and change things">Key</button></nav>
<div id="sbkeybox"><p>Your owner key lets you approve jobs and choose models. It is kept in this browser only. Create one with <code>python -m agentdispatch token --owner you</code>.</p>
<input id="sbtok" type="password" placeholder="paste owner key" autocomplete="off"><div class="r"><button id="sbsave" class="go">Save</button><button id="sbclear">Clear</button></div></div>"""

NAV_JS = r"""<script>
(()=>{const $=s=>document.querySelector(s),KEY="sb_owner_token";
 const key=()=>localStorage.getItem(KEY)||"";
 function paint(){const b=$("#sbkey");b.classList.toggle("set",!!key());b.textContent=key()?"Owner key on":"Key"}
 $("#sbkey").onclick=()=>{const k=$("#sbkeybox");k.style.display=k.style.display==="block"?"none":"block";$("#sbtok").value=key()};
 $("#sbsave").onclick=()=>{localStorage.setItem(KEY,$("#sbtok").value.trim());$("#sbkeybox").style.display="none";paint();dispatchEvent(new Event("sb-token"))};
 $("#sbclear").onclick=()=>{localStorage.removeItem(KEY);$("#sbtok").value="";$("#sbkeybox").style.display="none";paint();dispatchEvent(new Event("sb-token"))};
 document.querySelectorAll("#sbnav .tabs a[data-engine]").forEach(a=>a.href=location.protocol+"//"+location.hostname+":8898/");
 async function ping(){try{const s=await (await fetch("/api/stats")).json();$("#sbled").className="up";$("#sbver").textContent="broker v"+s.version+(s.pending_approvals?" · "+s.pending_approvals+" waiting":"")}catch(e){$("#sbled").className="";$("#sbver").textContent="broker not answering"}}
 paint();ping();setInterval(ping,15000);})();
</script>"""


def _tabs(active):
    out = []
    for k, label, href, hint, icon in TABS:
        eng = ' data-engine="1" target="_blank" rel="noopener"' if href == "ENGINE" else ""
        out.append('<a href="%s" class="%s" title="%s"%s><i>%s</i>%s</a>' % ("#" if href == "ENGINE" else href, "act" if k == active else "", hint, eng, icon, label))
    return "".join(out)


def apply(html, active):
    """the page with the Tron theme and the navigation bar; `active` is the tab key ('board', 'agency', 'brain', 'models')"""
    nav = NAV_HTML.replace("%TABS%", _tabs(active))
    style = "<style>" + THEME_CSS + "</style>"
    if "</head>" in html:
        html = html.replace("</head>", style + "</head>", 1)
    else:
        i = html.index("</style>") + len("</style>")
        html = html[:i] + style + html[i:]
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + nav, 1)
    else:
        i = html.index("<header")
        html = html[:i] + nav + html[i:]
    if "</body>" in html:
        return html.replace("</body>", NAV_JS + "</body>", 1)
    return html + NAV_JS
