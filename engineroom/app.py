#!/usr/bin/env python3
"""engineroom/app.py - the AgentDispatch ENGINE ROOM: a steam-gauge dashboard (browser window) plus a small local HTTP service for the agents that share one machine

    python -m engineroom                         open the window (a second copy just exits: one window only)
    python -m engineroom --no-window             service only (open http://127.0.0.1:8898/ yourself)
    python -m engineroom --watch 1234            close itself when process 1234 ends
    python -m engineroom --pid 1234 --pid 5678   count these process trees as "the bots"

Every dial has FOUR glowing needles:   BLUE + CYAN = the machine     YELLOW + AMBER = the bots' own share
  GPU n    blue = load   cyan = VRAM        yellow = bots' load on that card   amber = bots' VRAM
  CPU/RAM  blue = now    cyan = 1-min avg   yellow = bots now                  amber = bots 1-min avg
  GEMINI   blue = requests/min vs cap   cyan = tokens/min vs cap   yellow = today's budget used   amber = share of calls served LOCALLY
The dial's red zone starts at the bots' headroom limit; the lamp turns red and the header says OVER LIMIT when the bots are over it.

Bots register by writing a PID (one per line) to  $ENGINEROOM_DATA/pids.txt   or  POST /register {"pid": 123}
  GET  http://127.0.0.1:8898/now      one JSON snapshot            GET  /stream   Server-Sent Events, one a second
  GET  /budget?model=gemini-3.5-flash-lite[&backend=gemini|openrouter]   -> {ok_to_send, wait_s, rpm_left, tpm_left, rpd_left}   (ask BEFORE every cloud call)
  POST /llm   {"backend": "gemini"|"openrouter"|"ollama", "model": "...", "tokens": 1234}   for Ollama also "prompt_tokens", "gen_tokens", "ms" (eval_duration/1e6) and "who": the OLLAMA dial then shows tokens/s   (tell it AFTER every model call, so the GEMINI / OPENROUTER dials and the budget are right)
  POST /limit {"name": "claude"|"codex"|"grokcli"|"pullagent", "windows": [{"label": "5h", "pct": 28, "resets_at": "2030-01-01T12:00:00Z"}, {"label": "week", ...}]}
  POST /say   {"text": "what I am doing"}   shown on the bottom line
Needs Python 3.11+ (psutil and nvidia-smi make it exact; both optional) and Edge or Chrome for the window (it falls back to the default browser).
Configuration comes from environment variables only (see .env.example): ENGINEROOM_HOST, ENGINEROOM_PORT, ENGINEROOM_DATA, ENGINEROOM_LIMITS_FILE,
ENGINEROOM_AUTO, ENGINEROOM_PULL_AGENT_PATTERNS, AGENTDISPATCH_URL, OLLAMA_URL, OLLAMA_LOG.
"""
import collections, datetime, json, os, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from engineroom import ollamamon as OM                          # what the local Ollama is doing (log + /api/ps + reported calls)
except Exception:
    OM = None
try:
    from engineroom import limits as LIM                            # plan limits of the agent CLIs (one shared file)
except Exception:
    LIM = None
try:
    import psutil
except Exception:
    psutil = None

HOST = os.environ.get("ENGINEROOM_HOST", "127.0.0.1")
PORT = int(os.environ.get("ENGINEROOM_PORT", "8898"))
WORK = os.path.expanduser(os.environ.get("ENGINEROOM_DATA") or os.path.join("~", ".agentdispatch", "engineroom"))
PIDFILE = os.path.join(WORK, "pids.txt")
USAGE = os.path.join(WORK, "gemini_usage.json")
OR_USAGE = os.path.join(WORK, "openrouter_usage.json")
LIMITS = dict(cpu=90, ram=80, gpu=80, vram=90)                      # headroom rules: the bots' own share must stay under these %
BUDGET_SHARE = 0.5                                                  # the bots may use half of each model's free daily requests; the other half is left for interactive use
FREE = {"gemini-3.5-flash-lite": (15, 250000, 500), "gemini-3.1-flash-lite": (15, 250000, 500), "gemini-2.5-flash-lite": (10, 250000, 20),
        "gemini-3.8-flash": (5, 250000, 20), "gemini-3-flash-preview": (5, 250000, 20), "gemini-2.5-flash": (5, 250000, 20), "gemma": (30, 16000, 14400)}
# OpenRouter free tier is account-wide (docs 2026): 20 RPM, soft TPM, 50 RPD (1000 after a one-time >= $10 credit purchase)
OR_FREE = (20, 200000, 50)
OR_MODELS = [                                                       # id, short label, colour (same list the broker catalogs)
    ("openrouter/free", "OR free auto", "#00d4aa"),
    ("nvidia/nemotron-3.5-lightning:free", "Nemotron Lightning", "#a4e509"),
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron Super 120B", "#5dff9a"),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron Ultra 550B", "#3ddc84"),
    ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron Nano reason", "#19f0c8"),
    ("nvidia/nemotron-3.5-super-vl:free", "Nemotron Super VL", "#76b900"),
    ("nvidia/nemotron-3.5-content-safety:free", "Nemotron Safety", "#7fb5c8"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B", "#5dff9a"),
    ("google/gemma-4-31b-it:free", "Gemma 4 31B", "#4ee9ff"),
    ("cohere/north-mini-code:free", "North Mini Code", "#4da3ff"),
    ("xingchen-agi/xing4.0-29b-a4b:free", "Xing4.0 29B", "#a98bff"),
    ("thinkingmachines/inkling:free", "Inkling", "#ff9a1a"),
    ("thinkingmachines/inkling-small:free", "Inkling Small", "#ffd34d"),
    ("poolside/laguna-s-2.1:free", "Laguna S 2.1", "#ff6bb5"),
    ("poolside/laguna-xs-2.1:free", "Laguna XS 2.1", "#ff5ad1"),
    ("liquid/lfm-2.5-2.6b:free", "LFM 2.5 2.6B", "#e0ae52"),
    ("dots-studio/dots-3-note-preview:free", "Dots3 Note", "#c28bff"),
]
NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

LOCK = threading.Lock()
STATE = dict(snap={}, say="", say_t=0, pids=set())
HIST = {k: collections.deque(maxlen=60) for k in ("cpu", "ram", "bcpu", "bram")}
CALLS = collections.deque()                                         # (time, backend, model, tokens) for the last 24 h
DAY = dict(date="", count={})                                       # Gemini requests per model since midnight Pacific
OR_DAY = dict(date="", count={}, total=0)                           # OpenRouter: per-model + account total since midnight Pacific


def pacific_date():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


def load_usage():
    try:
        d = json.load(open(USAGE))
        if d.get("date") == pacific_date():
            DAY.update(date=d["date"], count=d.get("count", {}))
    except Exception:
        pass
    try:
        d = json.load(open(OR_USAGE))
        if d.get("date") == pacific_date():
            OR_DAY.update(date=d["date"], count=d.get("count", {}), total=int(d.get("total", 0)))
    except Exception:
        pass


def save_usage():
    try:
        os.makedirs(WORK, exist_ok=True)
        json.dump(DAY, open(USAGE, "w"))
    except Exception:
        pass
    try:
        os.makedirs(WORK, exist_ok=True)
        json.dump(OR_DAY, open(OR_USAGE, "w"))
    except Exception:
        pass


def caps(model):
    m = (model or "").lower()
    for k, v in FREE.items():
        if m.startswith(k):
            return v
    return (5, 250000, 20)


def llm_note(backend, model, tokens):
    with LOCK:
        t = time.time()
        today = pacific_date()
        if DAY["date"] != today:
            DAY.update(date=today, count={})
        if OR_DAY["date"] != today:
            OR_DAY.update(date=today, count={}, total=0)
        CALLS.append((t, backend, model, int(tokens or 0)))
        while CALLS and CALLS[0][0] < t - 86400:
            CALLS.popleft()
        if backend == "gemini":
            DAY["count"][model] = DAY["count"].get(model, 0) + 1
        elif backend == "openrouter":
            OR_DAY["count"][model] = OR_DAY["count"].get(model, 0) + 1
            OR_DAY["total"] = int(OR_DAY.get("total", 0)) + 1
    save_usage()


def budget(model, backend="gemini"):
    backend = (backend or "gemini").lower()
    if backend == "openrouter":
        rpm, tpm, rpd = OR_FREE
        now = time.time()
        with LOCK:
            recent = [c for c in CALLS if c[1] == "openrouter" and c[0] > now - 60]
            used_day = int(OR_DAY.get("total", 0)) if OR_DAY.get("date") == pacific_date() else 0
        day_cap = max(1, int(rpd * BUDGET_SHARE))
        rpm_left = max(0, int(rpm * 0.8) - len(recent))
        tpm_left = max(0, int(tpm * 0.8) - sum(c[3] for c in recent))
        wait = 0 if recent == [] else max(0, 61 - (now - recent[0][0])) if (rpm_left == 0 or tpm_left == 0) else 0
        ok = rpm_left > 0 and tpm_left > 0 and used_day < day_cap
        return dict(model=model, backend="openrouter", ok_to_send=ok, wait_s=round(wait, 1) if used_day < day_cap else None,
                    rpm_left=rpm_left, tpm_left=tpm_left, rpd_left=max(0, day_cap - used_day), day_budget=day_cap,
                    note="" if used_day < day_cap else "today's OpenRouter free budget is spent: use a local model or wait until Pacific midnight")
    rpm, tpm, rpd = caps(model)
    now = time.time()
    with LOCK:
        recent = [c for c in CALLS if c[1] == "gemini" and c[2] == model and c[0] > now - 60]
        used_day = DAY["count"].get(model, 0) if DAY["date"] == pacific_date() else 0
    day_cap = max(1, int(rpd * BUDGET_SHARE))
    rpm_left = max(0, int(rpm * 0.8) - len(recent))                 # 80% of the per-minute cap: never ride the limit
    tpm_left = max(0, int(tpm * 0.8) - sum(c[3] for c in recent))
    wait = 0 if recent == [] else max(0, 61 - (now - recent[0][0])) if (rpm_left == 0 or tpm_left == 0) else 0
    ok = rpm_left > 0 and tpm_left > 0 and used_day < day_cap
    return dict(model=model, backend="gemini", ok_to_send=ok, wait_s=round(wait, 1) if used_day < day_cap else None, rpm_left=rpm_left, tpm_left=tpm_left,
                rpd_left=max(0, day_cap - used_day), day_budget=day_cap, note="" if used_day < day_cap else "today's budget for this model is spent: use the next model or a local one")


GEM_MODELS = [("gemini-3.5-flash-lite", "3.5 Flash-Lite", "#4da3ff", 15, 500), ("gemini-3.1-flash-lite", "3.1 Flash-Lite", "#4ee9ff", 15, 500), ("gemma", "Gemma 4", "#5dff9a", 30, 14400),
               ("gemini-3.8-flash", "3.8 Flash", "#ffd34d", 5, 20), ("gemini-3-flash-preview", "3 Flash preview", "#ff9a1a", 5, 20),
               ("gemini-2.5-flash-lite", "2.5 Flash-Lite", "#ff5ad1", 10, 20), ("gemini-2.5-flash", "2.5 Flash", "#a98bff", 5, 20)]


def gemini_models():
    """every free Gemini model with today's use: an optional external count (<data>/gemini_limits.json, written by other tools) plus what the bots reported, against the model's daily cap (a learned cap in that file wins)"""
    external = {}
    try:
        st = json.load(open(os.path.join(WORK, "gemini_limits.json"), encoding="utf-8"))
        today = pacific_date()
        for name, m in st.items():
            if m.get("day") == today:
                external[name] = (int(m.get("used", 0)), m.get("rpd"))
    except Exception:
        pass
    now = time.time()
    with LOCK:
        day = dict(DAY["count"]) if DAY["date"] == pacific_date() else {}
        recent = [c for c in CALLS if c[1] == "gemini" and c[0] > now - 60]
    out = []
    for key, label, color, rpm, cap in GEM_MODELS:
        def mine(name):
            n = name.lower().replace("models/", "")
            return ("gemma" in n) if key == "gemma" else (n.startswith(key) and not (key == "gemini-2.5-flash" and "lite" in n))
        su = [v for k, v in external.items() if mine(k)]
        s_used = sum(v[0] for v in su)
        learned = next((v[1] for v in su if v[1]), None)
        cap = int(learned) if learned else cap
        b_used = sum(n for k, n in day.items() if mine(k))
        rpm_now = sum(1 for c in recent if mine(c[2]))
        out.append(dict(key=key, label=label, color=color, cap=cap, external=s_used, bots=b_used, used=s_used + b_used, frac=min(1.0, (s_used + b_used) / max(1, cap)), rpm_cap=rpm, rpm_now=rpm_now))
    return out


def openrouter_models():
    """per free OpenRouter model today's bot-reported use; account quota is shared (see openrouter_account)"""
    rpm, _tpm, rpd = OR_FREE
    day_cap = max(1, int(rpd * BUDGET_SHARE))
    now = time.time()
    with LOCK:
        day = dict(OR_DAY.get("count") or {}) if OR_DAY.get("date") == pacific_date() else {}
        total = int(OR_DAY.get("total", 0)) if OR_DAY.get("date") == pacific_date() else 0
        recent = [c for c in CALLS if c[1] == "openrouter" and c[0] > now - 60]
    out = []
    for key, label, color in OR_MODELS:
        used = int(day.get(key, 0))
        rpm_now = sum(1 for c in recent if c[2] == key)
        # each bar shows this model's share of the ACCOUNT day cap (so busy models light up; sum of fracs can exceed 1)
        out.append(dict(key=key, id=key, label=label, color=color, cap=day_cap, external=0, bots=used, used=used,
                        frac=min(1.0, used / max(1, day_cap)), rpm_cap=rpm, rpm_now=rpm_now))
    return out


def openrouter_account():
    """account-wide OpenRouter free quota (shared by every :free model)"""
    rpm, tpm, rpd = OR_FREE
    day_cap = max(1, int(rpd * BUDGET_SHARE))
    now = time.time()
    with LOCK:
        total = int(OR_DAY.get("total", 0)) if OR_DAY.get("date") == pacific_date() else 0
        recent = [c for c in CALLS if c[1] == "openrouter" and c[0] > now - 60]
    return dict(key="openrouter_account", label="OpenRouter free", color="#00d4aa", cap=day_cap, used=total,
                frac=min(1.0, total / max(1, day_cap)), rpm_cap=rpm, rpm_now=len(recent), tpm_cap=tpm,
                tpm_now=sum(c[3] for c in recent), rpd=rpd, note="account-wide free quota (20 RPM / 50 RPD shared)")


def smi(args):
    try:
        r = subprocess.run(["nvidia-smi"] + args + ["--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, **NOWIN)
        return [[c.strip() for c in l.split(",")] for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


PULL_PATTERNS = tuple(x.strip().lower() for x in os.environ.get("ENGINEROOM_PULL_AGENT_PATTERNS", "").split(",") if x.strip())
AGENTS = [("claude", "Claude", ("claude.exe", "claude-code", "@anthropic-ai/claude")), ("codex", "Codex", ("codex.exe", "@openai/codex")), ("gemini", "Gemini CLI", ("gemini-cli",)),
          ("grokcli", "Grok CLI", ("grok.exe",)), ("pullagent", "Pull agent", PULL_PATTERNS)]       # agents found by process name or command line: no registering step needed
                                                                                                    # (a pull agent is any app that polls the board for work; name its process in ENGINEROOM_PULL_AGENT_PATTERNS)
EXTRA_AUTO = [x.strip().lower() for x in os.environ.get("ENGINEROOM_AUTO", "").split(",") if x.strip()]
_auto_cache = dict(t=0.0, pids=set(), counts={}, cpu={})


def auto_registered():
    """PIDs of the agent CLIs / apps in AGENTS (plus anything in ENGINEROOM_AUTO), scanned at most every 5 s; also keeps how many processes each agent has for the 'connected' strip"""
    now = time.time()
    if now - _auto_cache["t"] < 5 or not psutil:
        return _auto_cache["pids"]
    found, counts, cpu = set(), {a[0]: 0 for a in AGENTS}, {a[0]: 0.0 for a in AGENTS}
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            hay = ((p.info.get("name") or "") + " " + " ".join(p.info.get("cmdline") or [])).lower()
        except Exception:
            continue
        if "engineroom" in hay and ("python" in hay or "app.py" in hay):          # the engine room itself
            continue
        hit = False
        for key, _label, pats in AGENTS:
            if any(a in hay for a in pats):
                counts[key] += 1
                hit = True
                try:
                    cpu[key] += p.cpu_percent(None)                       # psutil keeps the Process object between scans, so this is the load since the last scan
                except Exception:
                    pass
        if hit or any(a in hay for a in EXTRA_AUTO):
            found.add(p.info["pid"])
    _auto_cache.update(t=now, pids=found, counts=counts, cpu=cpu)
    return found


BROKER = os.environ.get("AGENTDISPATCH_URL", "http://127.0.0.1:8870")
_agency_cache = dict(t=0.0, data=None)
LIMIT_NAME = dict(claude="claude", codex="codex", grokcli="grokcli", pullagent="pullagent", gemini=None)


def agency_snapshot():
    """the broker's /api/agency (jobs by state, what needs the owner, which agents could run), cached 5 s; None when the broker does not answer. READ ONLY: the engine room holds no owner token,
    so a program on this machine cannot approve its own work through it."""
    now = time.time()
    if now - _agency_cache["t"] < 5:
        return _agency_cache["data"]
    try:
        import urllib.request
        with urllib.request.urlopen(BROKER + "/api/agency", timeout=2) as r:
            _agency_cache["data"] = json.loads(r.read())
    except Exception:
        _agency_cache["data"] = None
    _agency_cache["t"] = now
    return _agency_cache["data"]


def agents_feed():
    """GET /agents: what the agency's dispatcher reads - per agent: running (process count), how old its plan-limit reading is, the highest plan window used (null when unknown), machine load"""
    lim = {}
    try:
        lim = LIM.snapshot(time.time()) if LIM else {}
    except Exception:
        pass
    snap = STATE.get("snap") or {}
    load = dict(cpu=snap.get("cpu"), ram=snap.get("ram"), gpu_max=max([g.get("util", 0) for g in snap.get("gpus", [])] or [0]))
    out = []
    for a in agents_snapshot():
        rec = lim.get(LIMIT_NAME.get(a["id"]) or "", {}) or {}
        wins = rec.get("windows") or []
        out.append(dict(id=a["id"], name=a["name"], running=a["n"] > 0, n=a["n"], cpu=a.get("cpu", 0.0), busy=a.get("cpu", 0.0) > 4.0, limit_age_min=rec.get("age_min"), limit_stale=rec.get("stale"),
                        limit_pct=max([float(w.get("pct") or 0) for w in wins]) if wins else None, load=load))
    return out


def agents_snapshot():
    auto_registered()
    return [dict(id=k, name=label, n=_auto_cache["counts"].get(k, 0), cpu=round(_auto_cache.get("cpu", {}).get(k, 0.0), 1)) for k, label, _ in AGENTS]


def registered():
    s = set(STATE["pids"]) | auto_registered()
    try:
        for l in open(PIDFILE):
            if l.strip().isdigit():
                s.add(int(l.strip()))
    except Exception:
        pass
    return s


def tree(pids):
    out = set()
    if not psutil:
        return out
    for p in pids:
        try:
            out.add(p)
            out.update(c.pid for c in psutil.Process(p).children(recursive=True))
        except Exception:
            pass
    return out


_procs = {}
_gpu_last = dict(t=0, gpus=[])


def avg(k):
    h = HIST[k]
    return sum(h) / len(h) if h else 0.0


def sample():
    snap = dict(t=time.time())
    ncpu = (psutil.cpu_count() if psutil else None) or os.cpu_count() or 1
    snap["cpu"] = psutil.cpu_percent(None) if psutil else 0
    vm = psutil.virtual_memory() if psutil else None
    snap["ram"] = vm.percent if vm else 0
    snap["ram_total_gb"] = round(vm.total / 2**30) if vm else 0
    mine = tree(registered())
    c = r = 0.0
    for pid in mine:
        try:
            p = _procs.get(pid) or _procs.setdefault(pid, psutil.Process(pid))
            c += p.cpu_percent(None); r += p.memory_info().rss
        except Exception:
            _procs.pop(pid, None)
    for pid in [k for k in _procs if k not in mine]:
        _procs.pop(pid, None)
    b = dict(procs=len(mine), cpu=min(100.0, c / ncpu), ram=(100.0 * r / vm.total) if vm else 0, ram_gb=round(r / 2**30, 1))
    gq, apps = smi(["--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,uuid"]), smi(["--query-compute-apps=pid,gpu_uuid,used_memory"])
    gp = [g[:6] for g in gq]
    u2i = {g[6]: int(g[0]) for g in gq if len(g) > 6 and g[0].isdigit()}
    botmem = collections.defaultdict(float)
    for a in apps:
        try:
            if int(a[0]) in mine:
                botmem[u2i.get(a[1], -1)] += float(a[2])
        except Exception:
            pass
    if not gp and time.time() - _gpu_last["t"] < 60:               # nvidia-smi is slow or times out while the cards are busy: keep the last good reading instead of dropping the dials
        gp = _gpu_last["gp"]
    elif gp:
        _gpu_last.update(t=time.time(), gp=gp)
    gpus = []
    for g in gp:
        try:
            i = int(g[0])
            gpus.append(dict(index=i, name=g[1], util=float(g[2]), used=float(g[3]), total=float(g[4]), temp=float(g[5]), bot_vram=botmem.get(i, 0.0), bot_on=botmem.get(i, 0.0) > 0))
        except Exception:
            pass
    b["gpu"] = (sum(g["util"] for g in gpus if g["bot_on"]) / len(gpus)) if gpus else 0
    b["vram"] = (100.0 * sum(botmem.values()) / sum(g["total"] for g in gpus)) if gpus else 0
    snap["gpus"], snap["bots"] = gpus, b
    HIST["cpu"].append(snap["cpu"]); HIST["ram"].append(snap["ram"]); HIST["bcpu"].append(b["cpu"]); HIST["bram"].append(b["ram"])
    snap["avg"] = {k: round(avg(k), 1) for k in HIST}
    snap["gemini"] = gemini_models()
    snap["openrouter"] = openrouter_models()
    snap["openrouter_account"] = openrouter_account()
    snap["agents"] = agents_snapshot()
    snap["agency"] = agency_snapshot()
    snap["limits"] = LIM.snapshot() if LIM else {}
    try:
        snap["ollama"] = OM.snapshot() if OM else {}
    except Exception:
        snap["ollama"] = {}
    snap["over"] = [k for k in ("cpu", "ram", "gpu", "vram") if b.get(k, 0) > LIMITS[k]]
    now = time.time()
    with LOCK:
        recent = [x for x in CALLS if x[0] > now - 60]
        gem = [x for x in recent if x[1] == "gemini"]
        last = next((x for x in reversed(CALLS) if x[1] == "gemini"), None)
        model = last[2] if last else ""
        rpm, tpm, rpd = caps(model)
        used = DAY["count"].get(model, 0) if DAY["date"] == pacific_date() else 0
        allc = [x for x in CALLS if x[0] > now - 3600]
        local = sum(1 for x in allc if x[1] == "ollama")
        or_acc = snap["openrouter_account"]
        snap["llm"] = dict(model=model, rpm_f=len([x for x in gem if x[2] == model]) / rpm if model else 0, tpm_f=sum(x[3] for x in gem if x[2] == model) / tpm if model else 0,
                           day_f=used / max(1, rpd * BUDGET_SHARE) if model else 0, rpd=used, budget=int(rpd * BUDGET_SHARE), local_f=(local / len(allc)) if allc else 0,
                           openrouter_rpm_f=(or_acc.get("rpm_now", 0) / max(1, or_acc.get("rpm_cap", 20))), openrouter_day_f=or_acc.get("frac", 0))
        snap["say"] = STATE["say"] if now - STATE["say_t"] < 900 else ""
    snap["broker_url"] = BROKER
    return snap


_log_t = [0.0]


def maybe_log(s):
    """one row a minute into $ENGINEROOM_DATA/metrics/engine.csv: the history to tune the machine and the budgets from later"""
    if time.time() - _log_t[0] < 60 or not s.get("t"):
        return
    _log_t[0] = time.time()
    import csv
    p = os.path.join(WORK, "metrics", "engine.csv")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    g = {x["index"]: x for x in s.get("gpus", [])}
    o, b, L = s.get("ollama") or {}, s.get("bots") or {}, s.get("limits") or {}

    def lim(n, i):
        try:
            return L[n]["windows"][i]["pct"]
        except Exception:
            return ""
    head = ["time", "cpu", "ram"] + sum([["gpu%d_util" % i, "gpu%d_vram_mb" % i] for i in range(5)], []) + [
        "bots_procs", "bots_cpu", "bots_ram_gb", "ollama_req_min", "ollama_duty", "ollama_tok_s_now", "ollama_tok_s_1min", "ollama_gen_today", "ollama_vram_gb",
        "claude_5h", "claude_week", "codex_5h", "codex_week", "pullagent_week", "grokcli_week", "gemini_rpm_f", "gemini_day_f"]
    row = [time.strftime("%Y-%m-%d %H:%M:%S"), s.get("cpu"), s.get("ram")] + sum([[g.get(i, {}).get("util", ""), g.get(i, {}).get("used", "")] for i in range(5)], []) + [
        b.get("procs"), round(b.get("cpu", 0), 1), b.get("ram_gb"), o.get("req_min"), o.get("duty"), o.get("now_tok_s"), o.get("thru_tok_s"), o.get("gen_today"), o.get("vram_gb"),
        lim("claude", 0), lim("claude", 1), lim("codex", 0), lim("codex", 1), lim("pullagent", 0) if len((L.get("pullagent") or {}).get("windows", [])) < 2 else lim("pullagent", 1), lim("grokcli", 0) if len((L.get("grokcli") or {}).get("windows", [])) < 2 else lim("grokcli", 1),
        round((s.get("llm") or {}).get("rpm_f", 0), 2), round((s.get("llm") or {}).get("day_f", 0), 2)]
    new = not os.path.isfile(p)
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(head)
        w.writerow(row)


def sampler():
    if psutil:
        psutil.cpu_percent(None)
    while True:
        try:
            s = sample()
            with LOCK:
                STATE["snap"] = s
            try:
                maybe_log(s)
            except Exception:
                pass
        except Exception as e:
            with LOCK:
                STATE["snap"] = dict(t=0, error=str(e)[:100])
        time.sleep(1.0)


class Server(ThreadingHTTPServer):
    allow_reuse_address = False                                     # on Windows SO_REUSEADDR lets a SECOND copy bind the same port and steal half the requests


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(open(os.path.join(HERE, "ui.html"), "rb").read(), "text/html; charset=utf-8")
        if u.path == "/stream":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
            try:
                while True:
                    with LOCK:
                        s = dict(STATE["snap"])
                    self.wfile.write(("data: " + json.dumps(s) + "\n\n").encode()); self.wfile.flush(); time.sleep(1)
            except Exception:
                return
        if u.path == "/agents":
            return self._send(agents_feed())
        if u.path == "/budget":
            q = parse_qs(u.query)
            return self._send(budget((q.get("model") or ["gemini-3.5-flash-lite"])[0], (q.get("backend") or ["gemini"])[0]))
        with LOCK:
            self._send(STATE["snap"])

    def do_POST(self):
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception:
            return self._send(dict(error="bad json"), code=400)
        p = urlparse(self.path).path
        if p == "/limit":
            try:
                LIM.set_limit(str(d.get("name", "")).lower(), d.get("windows") or [], source=str(d.get("source", "pushed")))
            except Exception as e:
                return self._send(dict(error=str(e)), code=400)
        if p == "/llm":
            if str(d.get("backend", "")).lower() == "ollama" and OM:
                OM.record_call(str(d.get("who", "bot")), str(d.get("model", "")), d.get("prompt_tokens", 0), d.get("gen_tokens") or d.get("tokens", 0), d.get("ms", 0))
            llm_note(str(d.get("backend", "gemini")).lower(), str(d.get("model", "")), d.get("tokens", 0))
        with LOCK:
            if p == "/say":
                STATE["say"], STATE["say_t"] = str(d.get("text", ""))[:200], time.time()
            elif p == "/register" and str(d.get("pid", "")).isdigit():
                STATE["pids"].add(int(d["pid"]))
                try:                                                # kept on disk too, so a restart of the engine room does not forget what the bots registered
                    os.makedirs(WORK, exist_ok=True)
                    open(PIDFILE, "a").write("%d\n" % int(d["pid"]))
                except Exception:
                    pass
        self._send(dict(ok=True))


def open_window(url):
    """a chrome-less app window (Edge or Chrome) with its own profile, so it is a window of its own; the process, or None"""
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe", r"Programs\Microsoft\Edge\Application\msedge.exe"):
        for base in pf:
            exe = os.path.join(base, rel)
            if os.path.isfile(exe):
                os.makedirs(os.path.join(WORK, "profile"), exist_ok=True)
                return subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + os.path.join(WORK, "profile"), "--window-size=700,800", "--window-position=40,40", "--no-first-run", "--disable-extensions"])
    webbrowser.open(url)
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--pid", type=int, action="append", default=[]); ap.add_argument("--watch", type=int, default=0); ap.add_argument("--no-window", action="store_true")
    a = ap.parse_args()
    try:
        srv = Server((HOST, PORT), H)
    except OSError:
        print("the engine room is already open"); return
    os.makedirs(WORK, exist_ok=True)
    load_usage()
    try:                                                              # PIDs registered before the last boot now belong to unrelated processes: forget them
        if psutil and os.path.getmtime(PIDFILE) < psutil.boot_time():
            open(PIDFILE, "w").close()
    except OSError:
        pass
    STATE["pids"].update(a.pid)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    win = None if a.no_window else open_window("http://%s:%d/" % ("127.0.0.1" if HOST in ("0.0.0.0", "") else HOST, PORT))
    born = time.time()
    gone = 0                                                         # consecutive samples with no Edge profile window (Edge exits its launcher PID immediately)
    while True:
        time.sleep(1.5)
        if a.watch and psutil and not psutil.pid_exists(a.watch):
            break
        if win is None:
            continue
        if win.poll() is None:                                       # launcher still alive
            gone = 0
            continue
        if time.time() - born < 15:                                  # give Edge time to spawn the real --app window under the profile dir
            continue
        try:
            needle = os.path.join(WORK, "profile").lower().replace("/", "\\")
            still = False
            if psutil:
                for p in psutil.process_iter(["cmdline", "name"]):
                    cl = " ".join(p.info.get("cmdline") or []).lower().replace("/", "\\")
                    if needle in cl or ("msedge" in (p.info.get("name") or "").lower() and "engineroom" in cl and "profile" in cl):
                        still = True
                        break
        except Exception:
            still = True                                             # if we cannot scan, keep serving
        if still:
            gone = 0
        else:
            gone += 1
            if gone >= 4:                                            # ~6 s with no profile window: user closed it
                break
    if win is not None and win.poll() is None:
        win.terminate()


if __name__ == "__main__":
    main()
