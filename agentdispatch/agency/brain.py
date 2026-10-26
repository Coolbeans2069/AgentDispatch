"""the data behind the /brain page: every agent in the registry with where it sits in the brain, its colour and live state; the models that are actually loaded right now (Ollama) and the ComfyUI
windows (image / video / speech / music) as tenants of the lobe their work belongs to; a stream of pulses (what just happened); the ROUTE of a job - which lobe, which model and why, hop by hop - and
the 'DNA strand' of one job, a segment per task that fills in as the pipe completes. Nothing here is hard-coded to particular agents: add an entry to the registry and it appears.
Lobes are FUNCTIONS (planning, code, language, vision, speech, audio, fast reflexes, world knowledge); models are tenants that move into the lobe their current task needs."""
import json, os, re, time, urllib.request

from . import policy
from .jobs import TERMINAL

ACTIVE_STATES = ("planning", "running", "reviewing", "blocked", "ready", "queued")
LOBE_LABEL = dict(frontal="planning · reasoning", parietal="code · structure", temporal="language · research", occipital="vision · video · image", cerebellum="fast · local reflexes",
                  brainstem="broker", broca="speech", auditory="music · sound", limbic="world · social")
# where each catalog function lives in the brain (the models page's "which model does what")
FUNC_LOBE = {"text": "temporal", "code": "parietal", "reasoning": "frontal", "vision": "occipital", "image": "occipital", "image-edit": "occipital", "video": "occipital", "faces": "occipital",
             "objects": "occipital", "depth": "occipital", "segmentation": "occipital", "music": "auditory", "embedding": "limbic", "speech-stt": "broca", "speech-tts": "broca", "speech-duplex": "broca"}
DEFAULT_EXPECTED_S =dict(local=40, free=40, cheap=70, plan=50, premium=30)


# ---------------------------------------------------------------- which lobe does a piece of work belong to
def lobe_for(needs, kind="step"):
    """the function a task needs decides its lobe: planning and review -> frontal, code -> parietal, video / image / vision -> occipital, speech -> broca, audio -> auditory, research / writing /
    long context -> temporal, fast -> cerebellum"""
    n = set(needs or [])
    if kind in ("plan", "review"):
        return "frontal"
    if "tool" in n:
        return "parietal"
    if n & {"video", "image", "vision"}:
        return "occipital"
    if "speech" in n:
        return "broca"
    if "audio" in n:
        return "auditory"
    if "code" in n:
        return "parietal"
    if n & {"plan", "review"}:
        return "frontal"
    if n & {"research", "long-context", "write"}:
        return "temporal"
    if "fast" in n:
        return "cerebellum"
    return "temporal"


def model_lobe(name, strengths=None):
    """where a model sits: vision -> occipital, code -> parietal, reasoning/plan -> frontal, speech -> broca, music/audio -> auditory,
    tiny/fast -> cerebellum, social/safety -> limbic, write/research -> temporal. Optional strengths (agency tags) win over the name."""
    st = set(strengths or [])
    if st & {"video", "image", "vision"}:
        return "occipital"
    if "speech" in st:
        return "broca"
    if "audio" in st or "music" in st:
        return "auditory"
    if "code" in st and "reasoning" not in st:
        return "parietal"                                                          # coding models stay parietal even when they also plan/write
    if "reasoning" in st or (st & {"plan", "review"} and "fast" not in st and "write" not in st):
        return "frontal"
    if st & {"plan", "review"} and "write" not in st and "research" not in st:
        return "frontal"
    if "fast" in st and not st & {"long-context", "research"}:
        return "cerebellum"
    n = (name or "").lower()
    if re.search(r"(^|[-:_.+/])(vl|vision|llava|minicpm-v|moondream|wan|i2v|s2v)|qwen2\.5vl|-vl|super-vl|image|video", n):
        return "occipital"
    if re.search(r"speech|tts|stt|whisper|fish|persona|voice|duplex", n):
        return "broca"
    if re.search(r"music|ace-step|audio|sound", n):
        return "auditory"
    if re.search(r"coder|code|starcoder|codestral|north-mini-code|laguna", n):
        return "parietal"
    if re.search(r"\b(haiku|flash-lite|lightning|inkling-small|laguna-xs|lfm|2\.6b|1\.5b|3b|4b)\b|build-fast", n):
        return "cerebellum"
    if re.search(r"\b(opus|ultra|astra|r1|reason|thinking|cascade|o1|qwq|gpt-oss|nemotron-3-ultra)\b", n):
        return "frontal"
    if re.search(r"content-safety|moderat|guard|grok-4", n):
        return "limbic"
    m = re.search(r"[-:_](\d+(?:\.\d+)?)b\b", n)
    if m and float(m.group(1)) <= 4.5:
        return "cerebellum"
    if re.search(r"gemini|gemma|sonnet|flash|inkling|xing|dots|nemotron|openrouter", n):
        return "temporal"
    return "temporal"


# short display name + colour for a known cloud / local model id (family tint so the brain is readable)
def model_style(name, agent_id=None):
    n = (name or "")
    low = n.lower()
    if agent_id == "claude-code" or low in ("haiku", "sonnet", "opus"):
        label = {"haiku": "Claude Haiku", "sonnet": "Claude Sonnet", "opus": "Claude Opus"}.get(low, n)
        return label, "#e8793a"
    if agent_id == "codex-cli" or low.startswith("gpt-6"):
        return n.replace("gpt-6.1-", "Codex ").replace("gpt-6-", "Codex "), "#3ddc84"
    if agent_id == "grok-cli" or low.startswith("grok-"):
        return n, "#e6edf3"
    if low.startswith("gemini-") or low.startswith("gemma"):
        return n.replace("gemini-", "Gemini "), "#4da3ff"
    try:
        from .. import openrouter as OR
        if OR.is_openrouter_id(n):
            return OR.short_label(n), OR.color_of(n)
    except Exception:
        pass
    if "/" in n and not low.startswith("local-"):
        return n.split("/")[-1][:32], "#8aa0b4"
    # keep the ollama tag (qwen3:4b vs qwen3:30b) so tokens stay distinct
    short = n if len(n) <= 32 else (n.split(":")[0][-20:] + (":" + n.split(":", 1)[1] if ":" in n else ""))[:32]
    return short, "#b57bff"


def difficulty(job, step, attempts=0, tier_rank=0, kind="step"):
    """a 0..1 estimate of how hard a task is, from how many times it already failed, how far it had to escalate, how many abilities it needs and how much it asks"""
    needs = (step or {}).get("needs") or []
    obj = (step or {}).get("objective") or job.get("goal") or ""
    d = 0.22 + 0.2 * min(3, attempts) + 0.12 * tier_rank + 0.08 * max(0, len(needs) - 1) + 0.2 * min(len(obj), 400) / 400.0
    if kind in ("plan", "review"):
        d += 0.1
    if job.get("kind") == "rebuild":
        d += 0.15
    return round(max(0.05, min(1.0, d)), 2)


def expected_seconds(db, reg, agent_id):
    """how long this agent usually takes: the average of its finished runs, else a default by cost class"""
    r = db.execute("SELECT AVG(secs) a, COUNT(*) n FROM runs WHERE agent=? AND outcome='ok' AND secs IS NOT NULL", (agent_id,)).fetchone()
    if r and r["n"] and r["a"]:
        return max(8.0, float(r["a"]))
    return float(DEFAULT_EXPECTED_S.get(reg.agents[agent_id]["cost"], 45))


def match_agent(reg, actor):
    """the registry agent an actor name belongs to ('codex-mcp-client' -> codex-cli, 'grok-shell-agentdispatch' -> grok-cli / pull-agent); None for the broker's own actors"""
    a = (actor or "").lower()
    if not a or a in ("agency", "owner", "unknown"):
        return None
    for aid, ag in reg.agents.items():
        if a == aid.lower():
            return aid
    best = None
    for aid, ag in reg.agents.items():
        toks = {aid.lower().split("-")[0], str(ag.get("er_id") or "").lower()} - {""}
        if any(t in a for t in toks):
            best = best or aid
            if ag["kind"] != "app":                                            # a CLI session beats the desktop app of the same family
                return aid
    return best


# ---------------------------------------------------------------- what is really running (cached a few seconds)
_svc = dict(t=0.0, data=None)


def _get_json(url, timeout=1.2):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def comfy_role(graph):
    """what a ComfyUI prompt is making (from its node classes) and which model files it loads -> (role, [model names])"""
    cls, models = [], []
    for node in (graph or {}).values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type", ""))
        cls.append(ct)
        for k, v in (node.get("inputs") or {}).items():
            if k in ("unet_name", "ckpt_name", "clip_name", "model_name") and isinstance(v, str):
                models.append(v)
    j = " ".join(cls)
    if re.search(r"Wan|Video|LTX|Hunyuan|I2V|S2V|AnimateDiff", j):
        role = "video"
    elif re.search(r"ACE|AudioEncode|StableAudio|MusicGen", j, re.I):
        role = "audio"
    elif re.search(r"Fish|TTS|Speech|Voice|Whisper", j, re.I):
        role = "speech"
    elif re.search(r"Sampler|SaveImage|VAEDecode|ImageEdit|QwenImage", j):
        role = "image"
    else:
        role = "image"
    return role, sorted(set(models))[:4]


def services(app, ports=(8188, 8189, 8190, 8191, 8192), force=False):
    """Ollama's loaded models, the ComfyUI windows that answer (and what each is making), and whether MediaStudio's Studio (VisionCore's recognition models) is up"""
    now = time.time()
    if not force and _svc["data"] is not None and now - _svc["t"] < 3:
        return _svc["data"]
    out = dict(ollama=dict(up=False, installed=0, loaded=[]), comfy=[], studio=False)
    try:
        ps = _get_json("http://127.0.0.1:11434/api/ps").get("models", [])
        out["ollama"] = dict(up=True, installed=len(_get_json("http://127.0.0.1:11434/api/tags").get("models", [])),
                             loaded=[dict(name=m["name"], size_gb=round(m.get("size", 0) / 2**30, 1), vram_gb=round(m.get("size_vram", 0) / 2**30, 1), lobe=model_lobe(m["name"])) for m in ps])
    except Exception:
        pass
    for p in ports:
        try:
            q = _get_json("http://127.0.0.1:%d/queue" % p, 0.6)
        except Exception:
            continue
        running = q.get("queue_running") or []
        role, models = (None, [])
        if running:
            try:
                role, models = comfy_role(running[0][2])
            except Exception:
                role, models = ("image", [])
        out["comfy"].append(dict(port=p, busy=bool(running), pending=len(q.get("queue_pending") or []), role=role, models=models))
    try:
        _get_json("http://127.0.0.1:8804/api/telemetry", 0.8)
        out["studio"] = True
    except Exception:
        pass
    _svc.update(t=now, data=out)
    return out


# ---------------------------------------------------------------- the agents
def agent_cards(app, svc=None):
    reg, av, jobs = app.registry, app.availability, app.jobs
    er = av.engine_room() or {}
    er_agents = {a["id"]: a for a in er.get("agents", [])}
    svc = svc if svc is not None else services(app)
    cards = []
    for aid, a in reg.agents.items():
        ok, why = av.check(a)
        b = reg.brain(aid)
        e = er_agents.get(a.get("er_id")) or {}
        running_runs = jobs.runs_active(aid)
        cd = jobs.cooldown_until(aid)
        sv = a.get("service") or {}
        procs, models = e.get("n", 0), []
        if sv.get("kind") == "comfyui":
            wins = [w for w in svc["comfy"] if w["busy"] and w["role"] == sv.get("role")]
            up = bool(svc["comfy"])
            state = "active" if wins else "online" if up else "off"
            procs, models = len(svc["comfy"]), sorted({m for w in wins for m in w["models"]})[:3]
        elif sv.get("kind") == "studio":
            state = "online" if svc["studio"] else "off"
        elif running_runs:
            state = "active"
        elif cd:
            state = "cooldown"
        elif e.get("busy"):
            state = "busy"
        elif (e.get("n", 0) > 0) or (float(e.get("cpu") or 0) >= 8.0):
            # live host processes for this agent (Claude / Gemini / Grok / Codex / …) — animate even when agency has no open task
            state = "active"
            n = int(e.get("n") or 0)
            cpu = float(e.get("cpu") or 0)
            bits = []
            if n:
                bits.append("%d process%s on this machine" % (n, "" if n == 1 else "es"))
            if cpu >= 8.0:
                bits.append("%.0f%% CPU" % cpu)
            why = ("external: " + ", ".join(bits)) if bits else why
        elif e.get("running") or jobs.heartbeat_age(aid) < 120:
            state = "online"                                                       # a process is running, or an agent that asks for work asked within the last two minutes
        elif not a.get("enabled", True):
            state = "off"
        elif (a.get("run") or {}).get("via") == "pull":
            state = "off"
        else:
            state = "idle" if ok else "off"
        # OpenRouter has no separate CLI process: online when a key is present and the agent is enabled
        if aid == "openrouter" and a.get("enabled", True) and state in ("idle", "off"):
            try:
                from .. import openrouter as OR
                if OR.api_key(app.data):
                    state, ok, why = "online", True, "ok"
            except Exception:
                pass
        if a["kind"] == "local" and (a.get("run") or {}).get("via") == "router" and svc["ollama"]["loaded"] and state != "cooldown":
            models = [m["name"] for m in svc["ollama"]["loaded"]]
            # a model sitting in VRAM is live work — pulse it even without an agency run row
            state = "active" if (running_runs or svc["ollama"]["loaded"]) else "online"
        # surface the agent's sub-models on the card (ladder / registry list) so the legend and tooltips name them
        if not models:
            models = list(((a.get("run") or {}).get("models") or []))[:8]
            if not models:
                try:
                    book = getattr(av, "book", None)
                    if book:
                        models = list(book.models(a))[:8]
                except Exception:
                    pass
        cards.append(dict(id=aid, name=b["label"], region=b["region"], color=b["color"], kind=a["kind"], cost=a["cost"], modality=a["strengths"], state=state, why=why, available=ok,
                          runs_today=jobs.runs_today(aid), daily_cap=a["daily_cap"], active_runs=running_runs, cooldown_s=max(0, int(cd - time.time())) if cd else 0,
                          processes=procs, cpu=e.get("cpu", 0.0), pull_only=(a.get("run") or {}).get("via") == "pull", enabled=a.get("enabled", True), models=models, service=bool(sv)))
    cards += tool_cards(app)
    return cards


HIDE_TOOL_KINDS = ("lib", "runtime")                                   # libraries live inside the calculator; the interpreter is not a tool of its own


def tool_cards(app):
    """every tool this computer has (and the ones that can be installed) as a node of the brain, in the lobe it belongs to; the list grows by itself when a tool is installed or an agent builds one"""
    box = getattr(app, "localtools", None)
    if box is None:
        return []
    try:
        listing = box.reg.listing()
    except Exception:
        return []
    running = {r["tool"] for r in list(box.active.values())}
    usage = box.usage()
    out = []
    for t in listing:
        if t["kind"] in HIDE_TOOL_KINDS:
            continue
        on = t["id"] in running
        state = "active" if on else "idle" if t["available"] else "off"
        out.append(dict(id="tool:" + t["id"], name=t["name"], region=t["lobe"], color=t["color"], kind="tool", cost="free", modality=[t["kind"]], state=state,
                        why=t["desc"] if t["available"] else "not installed" + (": it can be installed (the owner approves)" if t["installable"] else ""), available=t["available"], runs_today=usage.get(t["id"], 0),
                        daily_cap=0, active_runs=1 if on else 0, cooldown_s=0, processes=0, cpu=0.0, pull_only=False, enabled=True, models=[], service=True, custom=t["custom"]))
    return out


def tool_tenants(app):
    box = getattr(app, "localtools", None)
    if box is None:
        return []
    out = []
    now = time.time()
    names = {t["id"]: t for t in box.reg.listing()}
    for r in list(box.active.values()):
        t = names.get(r["tool"])
        if t:
            out.append(dict(kind="task", agent="tool:" + t["id"], job="", step=0, task_kind="tool", lobe=t["lobe"], model=t["name"], started=r["started"], elapsed_s=round(now - r["started"], 1), expected_s=20,
                            difficulty=0.45, via="local", why="a model asked this computer to run it"))
    return out


def _recent_model_use(app, within_s=900):
    """model ids seen answering in the last within_s seconds (router log + open tasks)"""
    now, used = time.time(), {}
    for d in _tail_json(os.path.join(app.data, "router.jsonl"), now - within_s, limit=400):
        for tr in d.get("tried") or []:
            m = tr.get("model")
            if m:
                used[m] = max(used.get(m, 0), d.get("ts", now))
    try:
        for e in app.board.events_since(0, 200):
            if e["ts"] < now - within_s:
                continue
            if e["type"] in ("job.route", "job.model"):
                d = e["data"] if isinstance(e["data"], dict) else json.loads(e.get("data") or "{}")
                m = d.get("model")
                if m:
                    used[m] = max(used.get(m, 0), e["ts"])
    except Exception:
        pass
    return used


def _er_warm_models(app):
    """models the engine room has counted today (Gemini bars + OpenRouter bars)"""
    warm = set()
    try:
        er = app.availability.engine_room() or {}
    except Exception:
        er = {}
    for row in er.get("gemini") or []:
        if (row.get("used") or 0) > 0 or (row.get("bots") or 0) > 0:
            warm.add(row.get("key") or row.get("id") or "")
    for row in er.get("openrouter") or []:
        if (row.get("used") or 0) > 0 or (row.get("bots") or 0) > 0:
            warm.add(row.get("key") or row.get("id") or "")
    return {m for m in warm if m}


def roster_models(app, svc=None, active_models=None, warm=None):
    """every known Gemini / OpenRouter / Claude / Grok / Codex / Ollama / speech / video model as a brain tenant in the lobe that fits its abilities.
    working=True when it is answering right now; warm=True when it has been used today (engine room) or recently (router)."""
    svc = svc if svc is not None else services(app)
    active_models = set(active_models or ())
    warm = set(warm or ())
    seen, out = {}, []

    def add(mid, label, lobe, color, source, agent=None, size_gb=0, strengths=None):
        if not mid or mid in ("auto", "auto-local", "auto-cloud"):
            return
        key = mid
        # Ollama /api/ps loaded models are hot in VRAM — treat as working so the orb pulses
        working = source == "ollama-loaded" or mid in active_models or any(mid in a or a in mid for a in active_models)
        is_warm = mid in warm or working or source == "ollama-loaded"
        row = dict(kind="model", name=label or mid, id=mid, lobe=lobe, color=color, source=source, agent=agent,
                   size_gb=size_gb or 0, strengths=list(strengths or []), working=working, warm=is_warm,
                   state="active" if working else "online" if is_warm else "standby")
        old = seen.get(key)
        if old is None or (working and not old.get("working")) or (is_warm and old.get("state") == "standby"):
            seen[key] = row

    # 1) routing ladders: Claude / Codex / Grok / Gemini / OpenRouter / local-agent sub-models
    try:
        from . import routing as R
        cfg = R.load(app.data)
        for aid, rungs in (cfg.get("ladders") or {}).items():
            for r in rungs or []:
                mid = r.get("model")
                if not mid:
                    continue
                label, color = model_style(mid, aid)
                strengths = None
                if aid == "claude-code":
                    strengths = ["plan", "review", "reasoning"] if mid == "opus" else ["code", "write"] if mid == "sonnet" else ["fast", "write"]
                elif aid == "codex-cli":
                    strengths = ["reasoning", "plan", "code"] if "astra" in mid else ["code"]
                elif aid == "grok-cli":
                    strengths = ["research", "write", "plan"] if mid.endswith("4.7") else ["fast", "write"] if "fast" in mid else ["research", "write"]
                elif aid == "gemini-cli":
                    strengths = ["plan", "review", "long-context"] if "3.8" in mid or "preview" in mid else ["fast", "write", "research"]
                elif aid == "openrouter":
                    try:
                        from .. import openrouter as OR
                        strengths = OR.strengths_of(mid)
                    except Exception:
                        strengths = ["write"]
                elif aid == "ollama-agent":
                    strengths = ["code"] if "coder" in mid else ["reasoning"] if "cascade" in mid or "reason" in mid else ["write"]
                add(mid, label, model_lobe(mid, strengths), color, "ladder:" + aid, agent=aid, strengths=strengths)
    except Exception:
        pass

    # 2) every OpenRouter free model (even if not on a rung yet)
    try:
        from .. import openrouter as OR
        for mid, label, _mod, _sub, strengths, color in OR.FREE_MODELS:
            add(mid, label, model_lobe(mid, strengths), color, "openrouter", agent="openrouter", strengths=strengths)
    except Exception:
        pass

    # 3) Gemini registry list (covers any not already on the ladder)
    gem = (app.registry.agents.get("gemini-cli") or {}).get("run", {}).get("models") or []
    for mid in gem:
        label, color = model_style(mid, "gemini-cli")
        st = ["plan", "long-context"] if "3.8" in mid or "preview" in mid else ["fast", "write", "research"]
        add(mid, label, model_lobe(mid, st), color, "gemini", agent="gemini-cli", strengths=st)

    # 4) loaded Ollama models (live VRAM tenants)
    for m in svc["ollama"]["loaded"]:
        label, color = model_style(m["name"], "ollama-local")
        add(m["name"], label, m.get("lobe") or model_lobe(m["name"]), color, "ollama-loaded", agent="ollama-local", size_gb=m.get("size_gb") or 0)

    # 5) installed Ollama tags that look useful (coder / vision / reason / speech) — standby, not every tiny tag
    try:
        tags = _get_json("http://127.0.0.1:11434/api/tags").get("models", [])
        for m in tags:
            name = m.get("name") or ""
            low = name.lower()
            if not re.search(r"coder|code|vl|vision|reason|r1|cascade|qwq|speech|whisper|tts|persona|gemma|qwen3|nemotron|gpt-oss|llama", low):
                continue
            label, color = model_style(name, "ollama-local")
            add(name, label, model_lobe(name), color, "ollama", agent="ollama-local",
                size_gb=round(m.get("size", 0) / 2**30, 1) if m.get("size") else 0)
    except Exception:
        pass

    # 6) catalog pins + speech / video / image / music models (Comfy / Studio / local assets)
    cat = getattr(app, "catalog", None)
    if cat is not None:
        for mid, m in list((cat.d.get("models") or {}).items()):
            mod = (m.get("modality") or "")
            sub = (m.get("subtype") or "")
            fn = None
            if mod == "vision" or sub in ("vlm", "vision"):
                fn, lobe, st = "vision", "occipital", ["vision"]
            elif mod == "video" or sub == "video":
                fn, lobe, st = "video", "occipital", ["video"]
            elif mod in ("speech", "audio") or sub in ("stt", "tts", "duplex", "speech", "music"):
                if sub == "music" or mod == "music":
                    fn, lobe, st = "music", "auditory", ["audio"]
                else:
                    fn, lobe, st = "speech", "broca", ["speech"]
            elif mod == "image" or sub in ("image", "image-edit"):
                fn, lobe, st = "image", "occipital", ["image"]
            else:
                continue
            label = m.get("label") or m.get("name") or mid
            raw = (m.get("name") or mid or "").strip()
            if len(raw) < 4 or raw.lower() in ("model", "codec", "tiny", "base", "small", "medium", "large"):
                continue                                                                   # skip nameless / generic Comfy leftovers
            color = {"vision": "#7bf0c4", "video": "#d14dff", "speech": "#ffd34d", "music": "#ff9a1a", "image": "#ff6bb5"}.get(fn, "#8aa0b4")
            add(raw, label, lobe, color, "catalog:" + fn, size_gb=m.get("size_gb") or 0, strengths=st)

    out.extend(seen.values())
    return out


def live_process_tenants(app, svc=None):
    """Engine Room host processes (Claude / Gemini / Codex / Grok / …) and Ollama VRAM loads that are NOT agency task rows.
    These are often work from other apps on this machine — still show and animate them on the brain."""
    svc = svc if svc is not None else services(app)
    out, now = [], time.time()
    er = (app.availability.engine_room() or {})
    er_agents = {a["id"]: a for a in er.get("agents", [])}
    for aid, a in app.registry.agents.items():
        if aid.startswith("tool:") or aid.startswith("comfy-"):
            continue
        e = er_agents.get(a.get("er_id")) or {}
        n = int(e.get("n") or 0)
        cpu = float(e.get("cpu") or 0)
        if n <= 0 and cpu < 8.0 and not e.get("busy"):
            continue
        if app.jobs.runs_active(aid):
            continue                                                                       # real agency runs already become task tenants
        b = app.registry.brain(aid)
        bits = []
        if n:
            bits.append("%d process%s" % (n, "" if n == 1 else "es"))
        if cpu >= 8.0:
            bits.append("%.0f%% CPU" % cpu)
        if e.get("busy"):
            bits.append("busy")
        models = list((a.get("run") or {}).get("models") or [])
        out.append(dict(kind="task", agent=aid, job="", step=0, task_kind="process", lobe=b["region"],
                        model=(models[0] if models else b["label"]), started=now, elapsed_s=0,
                        expected_s=60, difficulty=0.5, via="external", external=True,
                        why="live on this machine (not an agency task): " + (", ".join(bits) or "running"),
                        processes=n, cpu=cpu))
    for m in svc["ollama"]["loaded"]:
        out.append(dict(kind="task", agent="ollama-local", job="", step=0, task_kind="process", lobe=m.get("lobe") or model_lobe(m["name"]),
                        model=m["name"], started=now, elapsed_s=0, expected_s=90, difficulty=0.4, via="external", external=True,
                        why="loaded in VRAM (%.1f GB) - may be another app or the conductor" % (m.get("vram_gb") or m.get("size_gb") or 0)))
    if svc.get("studio"):
        out.append(dict(kind="window", name="MediaStudio", lobe="occipital", role="vision", source="studio",
                        working=True, state="active", color="#7bf0c4", why="MediaStudio Studio is up"))
    return out


def running_job_tenants(app, covered_jobs):
    """Jobs marked running/ready/planning/reviewing with no open agency task — still show them (stuck, held, or work owned elsewhere)."""
    out, now = [], time.time()
    for j in app.jobs.list(limit=80):
        st = j.get("state")
        if st not in ("running", "ready", "planning", "reviewing", "queued"):
            continue
        if j["id"] in covered_jobs:
            continue
        meta = j.get("meta") or {}
        agent = meta.get("preferred_agent") or ""
        if agent and agent not in app.registry.agents:
            agent = ""
        lobe = app.registry.brain(agent)["region"] if agent else "parietal"
        age_h = max(0.0, (now - float(j.get("updated") or j.get("created") or now)) / 3600.0)
        stale = age_h >= 1.0 and st == "running"
        why = j.get("why") or st
        if stale:
            why = "running %.0fh with no open agency task - may be external or stuck on deps - %s" % (age_h, why)
        elif not agent:
            why = "job %s (no agent claimed yet) - %s" % (st, why)
        out.append(dict(kind="task", agent=agent or "local-tools", job=j["id"], step=0, task_kind="job", lobe=lobe,
                        model=(j.get("title") or j["id"])[:48], started=float(j.get("updated") or j.get("created") or now),
                        elapsed_s=round(now - float(j.get("updated") or j.get("created") or now), 1),
                        expected_s=120, difficulty=0.55, via="job", external=True, job_state=st, stale=stale, why=why))
    return out


def tenants(app, svc=None):
    """models in their lobes (roster + loaded + speech/video), ComfyUI windows, agency tasks, and live external host processes"""
    svc = svc if svc is not None else services(app)
    out = []
    now = time.time()
    # tasks first so we know which models are answering right now
    for t in app.jobs.db.execute("SELECT t.*, r.started FROM tasks t LEFT JOIN runs r ON r.id=t.run_id WHERE t.state='open' AND t.expires>?", (now,)):
        t = dict(t)                                                                      # a row is read with .get: a column an older database lacks is a default, not a KeyError
        t.setdefault("job_id", ""), t.setdefault("step_n", 0), t.setdefault("kind", "step"), t.setdefault("via", "push")
        route = last_route(app, t["job_id"], t["kind"], t["step_n"]) or {}
        started = t.get("started") or t.get("issued") or now
        out.append(dict(kind="task", agent=t["agent"], job=t["job_id"], step=t["step_n"], task_kind=t["kind"], lobe=route.get("lobe") or app.registry.brain(t["agent"])["region"] if t["agent"] in app.registry.agents else "temporal",
                        model=route.get("model") or (app.registry.agents.get(t["agent"], {}).get("name", t["agent"])), started=started, elapsed_s=round(now - started, 1),
                        expected_s=route.get("expected_s") or 45, difficulty=route.get("difficulty", 0.4), via=t["via"], why=route.get("why", "")))
    pulled = {(t.get("job"), t.get("step"), t.get("agent")) for t in out if t.get("kind") == "task"}
    for r in app.jobs.db.execute("SELECT * FROM runs WHERE outcome IS NULL AND started>?", (now - 3 * 3600,)):
        r = dict(r)
        r.setdefault("job_id", ""), r.setdefault("step_n", 0)
        if (r["job_id"], r["step_n"], r["agent"]) in pulled or r["agent"] not in app.registry.agents:
            continue
        route = last_route(app, r["job_id"], "step", r["step_n"]) or last_route(app, r["job_id"], "plan", 0) or last_route(app, r["job_id"], "review", 0) or {}
        out.append(dict(kind="task", agent=r["agent"], job=r["job_id"], step=r["step_n"], task_kind=route.get("kind", "step"), lobe=route.get("lobe") or app.registry.brain(r["agent"])["region"],
                        model=route.get("model") or app.registry.agents[r["agent"]].get("name", r["agent"]), started=r["started"], elapsed_s=round(now - r["started"], 1),
                        expected_s=route.get("expected_s") or expected_seconds(app.jobs.db, app.registry, r["agent"]), difficulty=route.get("difficulty", 0.4), via="push", why=route.get("why", "")))
    out += tool_tenants(app)
    out += live_process_tenants(app, svc)
    covered = {t.get("job") for t in out if t.get("job")}
    out += running_job_tenants(app, covered)

    active = {t.get("model") for t in out if t.get("kind") == "task" and t.get("model")}
    active |= {m["name"] for m in svc["ollama"]["loaded"]}
    active |= set(_recent_model_use(app))
    warm = _er_warm_models(app)
    out.extend(roster_models(app, svc, active_models=active, warm=warm))

    for w in svc["comfy"]:
        out.append(dict(kind="window", name=":%d %s" % (w["port"], (w["models"][0] if w["models"] else "ComfyUI")), lobe={"video": "occipital", "image": "occipital", "speech": "broca", "audio": "auditory"}.get(w["role"], "occipital"),
                        role=w["role"], source="comfyui", working=w["busy"], state="active" if w["busy"] else "online",
                        color={"video": "#d14dff", "image": "#ff6bb5", "speech": "#ffd34d", "audio": "#ff9a1a"}.get(w["role"], "#ff6bb5")))
    return out


# ---------------------------------------------------------------- the route of a job
def last_route(app, job_id, kind, step_n):
    rows = app.board.db.execute("SELECT data FROM events WHERE post_id=? AND type='job.route' ORDER BY id DESC LIMIT 40", (job_id,)).fetchall()
    for r in rows:
        d = json.loads(r["data"] or "{}")
        if d.get("kind") == kind and int(d.get("step") or 0) == int(step_n or 0):
            return d
    return None


def route_chain(app, job_id):
    """the hand-offs of a job in order: each hop is a unit of work (plan, a step, check, review) with the lobe it went to, the agent and model that took it and why that one"""
    if not job_id:
        return []
    hops, models = [], {}
    for r in app.board.db.execute("SELECT id, ts, type, actor, data FROM events WHERE post_id=? AND type IN ('job.route','job.model') ORDER BY id", (job_id,)):
        d = json.loads(r["data"] or "{}")
        key = (d.get("kind"), int(d.get("step") or 0))
        if r["type"] == "job.model":
            models[key] = d
            for h in reversed(hops):
                if (h["kind"], h["step"]) == key:
                    h["model"], h["secs"], h["outcome"] = d.get("model") or h["model"], d.get("secs"), d.get("outcome")
                    break
        else:
            hops.append(dict(seq=len(hops) + 1, t=r["ts"], kind=d.get("kind"), step=int(d.get("step") or 0), agent=d.get("agent"), lobe=d.get("lobe"), model=d.get("model") or d.get("agent"),
                             why=d.get("why", ""), considered=d.get("considered", []), via=d.get("via", "push"), difficulty=d.get("difficulty"), tier=d.get("tier"), secs=None, outcome=None))
    return hops[-24:]


# ---------------------------------------------------------------- pulses and strand
def agent_cards_by_id(cards):
    return {c["id"]: c for c in cards}


def _tail_json(path, since_ts, limit=200):
    out = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 60000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError:
        return out
    for l in lines[-limit:]:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("ts", 0) > since_ts:
            out.append(d)
    return out


def pulses(app, since_event=0, since_ts=0.0, limit=60):
    """-> (pulses, new_event_cursor, new_ts_cursor). A pulse: {t, agent (registry id or 'broker'), kind, text}."""
    reg = app.registry
    out, last_ev, last_ts = [], since_event, since_ts
    for e in app.board.events_since(since_event, 300):
        last_ev = max(last_ev, e["id"])
        t, d, actor = e["type"], e["data"], e["actor"]
        agent = match_agent(reg, actor)
        if t == "job.state":
            out.append(dict(t=e["ts"], agent="broker", kind="state", text="%s %s -> %s" % (e["post"], d.get("frm", ""), d.get("to", "")), job=e["post"]))
        elif t == "job.route":
            out.append(dict(t=e["ts"], agent=match_agent(reg, d.get("agent")) or "broker", kind="route", text="%s -> %s (%s)" % (d.get("kind"), d.get("model") or d.get("agent"), d.get("lobe")), job=e["post"],
                            lobe=d.get("lobe"), model=d.get("model")))
        elif t == "job.step":
            out.append(dict(t=e["ts"], agent=match_agent(reg, d.get("agent")) or "broker", kind="step_" + str(d.get("state")), text="%s step %s %s" % (e["post"], d.get("n"), d.get("state")), job=e["post"]))
        elif t == "job.created":
            out.append(dict(t=e["ts"], agent="broker", kind="job", text="new job %s: %s" % (e["post"], d.get("title", "")), job=e["post"]))
        elif t == "job.rework":
            out.append(dict(t=e["ts"], agent="broker", kind="mutation", text="%s rework step %s" % (e["post"], d.get("step")), job=e["post"]))
        elif t == "agency.cooldown":
            out.append(dict(t=e["ts"], agent=match_agent(reg, actor) or "broker", kind="limit", text="%s hit a limit" % actor))
        elif t in ("attempt", "result", "post") and agent:
            out.append(dict(t=e["ts"], agent=agent, kind=t, text="%s %s" % (actor, t)))
    for d in _tail_json(os.path.join(app.data, "audit.jsonl"), since_ts):
        last_ts = max(last_ts, d["ts"])
        agent = match_agent(reg, d.get("actor"))
        if agent:
            out.append(dict(t=d["ts"], agent=agent, kind="tool", text="%s called %s" % (d.get("actor"), d.get("tool"))))
    for d in _tail_json(os.path.join(app.data, "router.jsonl"), since_ts):
        last_ts = max(last_ts, d["ts"])
        for tr in (d.get("tried") or [])[-1:]:
            be = tr.get("backend")
            if be == "ollama":
                agent = "ollama-local"
            elif be == "openrouter":
                agent = "openrouter"
            elif be == "gemini":
                agent = next((aid for aid, a in reg.agents.items() if a.get("er_id") == "gemini"), "gemini-cli")
            else:
                agent = None
            if agent in reg.agents:
                out.append(dict(t=d["ts"], agent=agent, kind="model", text="%s answered (%s tokens)" % (tr.get("model"), d.get("tokens", 0)),
                                model=tr.get("model"), lobe=model_lobe(tr.get("model"))))
    out.sort(key=lambda p: p["t"])
    return out[-limit:], last_ev, last_ts


def strand(app, job_id=None):
    """the DNA strand of one job: segments plan -> step 1..N -> check -> review -> deliver, each with a state (todo / running / done / failed / waiting) and the colour of the agent that did it"""
    jobs, reg = app.jobs, app.registry
    j = None
    if job_id:
        try:
            j = jobs.get(job_id)
        except Exception:
            j = None
    if not j:
        pick = jobs.list(limit=50)
        pick = [x for x in pick if x["state"] in ACTIVE_STATES] or pick
        pick.sort(key=lambda x: x.get("updated") or 0, reverse=True)
        j = jobs.get(pick[0]["id"]) if pick else None
    if not j:
        return None
    st, res = j["state"], j.get("result") or {}
    held_why = ""
    try:
        hooks = getattr(getattr(app, "campaign", None), "governor", None)
        if hooks and st in ("running", "queued", "planning", "blocked"):
            ok, held_why = hooks.ready(j)
            if ok:
                held_why = ""
    except Exception:
        held_why = ""
    segs = [dict(kind="plan", label="plan", state="done" if j["steps"] else "running" if st in ("queued", "planning") else "todo", color="#e6edf3")]
    for s in j["steps"]:
        b = reg.brain(s["agent"]) if s.get("agent") in reg.agents else None
        state = {"done": "done", "failed": "failed", "running": "running", "assigned": "running", "todo": "todo", "skipped": "failed"}.get(s["state"], "todo")
        if state == "todo" and (st == "blocked" or held_why):
            state = "waiting"
        segs.append(dict(kind="step", label="step %d" % s["n"], state=state, color=b["color"] if b else "#8aa0b4", agent=s.get("agent"), region=b["region"] if b else None,
                         objective=(s.get("objective") or "")[:90], attempts=s.get("attempts", 0), rework=str(s.get("objective", "")).startswith("Address the review")))
    check_known = "check_ok" in res
    acc = (j.get("acceptance") or {}).get("check")
    segs.append(dict(kind="check", label="check", color="#19f0c8", state=("done" if res.get("check_ok") else "failed") if check_known else ("running" if st == "reviewing" and acc else "todo") if acc else "done"))
    segs.append(dict(kind="review", label="review", color="#ffd34d", state="done" if st in ("ready", "merged", "delivered") else "running" if st == "reviewing" else "failed" if st == "failed" and check_known else "todo"))
    segs.append(dict(kind="deliver", label="deliver", color="#7bf0c4", state="done" if st in ("merged", "delivered") else "waiting" if st == "ready" else "failed" if st in ("failed", "cancelled") else "todo"))
    return dict(job=dict(id=j["id"], title=j["title"], state=st, why=(held_why or j.get("why") or ""), kind=j["kind"], held=bool(held_why) or st == "blocked"),
                segments=segs, complete=st in ("merged", "delivered"), terminal=st in TERMINAL)


def pins(app):
    """the models chosen (by the user or an AI) for each function, each in its lobe: what is on standby there"""
    cat = getattr(app, "catalog", None)
    if cat is None:
        return []
    out = []
    for f, sel in cat.d["selection"].items():
        m = cat.get(sel["model_id"])
        if m:
            out.append(dict(function=f, name=m.get("label") or m["name"], by=sel.get("by"), lobe=FUNC_LOBE.get(f, "temporal"), size_gb=m["size_gb"]))
    return out


def snapshot(app, since_event=0, since_ts=0.0, job=None):
    cfg = policy.load(app.data)
    svc = services(app)
    p, ev, ts = pulses(app, since_event, since_ts)
    st = strand(app, job)
    try:
        from .watchdog import activity_log
        alog = activity_log(app, 36)
    except Exception:
        alog = []
    return dict(ok=True, agents=agent_cards(app, svc), tenants=tenants(app, svc), services=dict(ollama_installed=svc["ollama"]["installed"], ollama_up=svc["ollama"]["up"], comfy_windows=len(svc["comfy"])),
                strand=st, pins=pins(app), chain=route_chain(app, st["job"]["id"] if st else None), pulses=p, cursor=dict(event=ev, ts=ts), now=time.time(), killed=policy.killed(app.data),
                auto_merge=bool(cfg["auto_merge"]), counts=app.jobs.counts(), lobes=LOBE_LABEL, activity_log=alog)
