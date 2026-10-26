"""is an agent usable right now? enabled, installed, not cooling down, under its concurrency and daily cap, its plan limit not used up (engine room, when it can be read), and - for local
models - the machine has headroom (the engine room's GPU / RAM figures). Unknown information never blocks by itself: it is reported in the reason so the dispatcher can log it."""
import json, os, shutil, time, urllib.request

ER_URL = os.environ.get("ENGINEROOM_URL", "http://127.0.0.1:8898")
CAP_WITH_READING = 4         # the static daily cap is multiplied by this while the live quota / plan reading shows room
LIMIT_FULL = 95.0              # % of a plan window at which an agent counts as used up
HEAVY_GPU_PCT = 60.0           # a local model is not started while a GPU is this busy (a production render holds it)
MIN_FREE_RAM_PCT = 15.0


def http_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(url, body, timeout=2):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Availability:
    def __init__(self, jobs, er_url=ER_URL, fetch=http_json, which=shutil.which, now=time.time, data_dir=None, poster=None, vram=None):
        self.jobs, self.er_url, self.fetch, self.which, self.now = jobs, er_url, fetch, which, now
        self._snap, self._t = None, 0.0
        self.poster = poster or post_json
        from .modelbook import ModelBook
        from .routing import Router
        self.book = ModelBook(jobs, self.engine_room, now)                              # per-model limits for agents that have several models (the Gemini CLI)
        self.router = Router(data_dir, self.book, self.engine_room, self.limit_pct, now)  # which model / how much thinking, by difficulty and quota
        from .vram import VramWatch
        self.vram = vram or VramWatch(now=now)                                          # polls Ollama + the GPUs: does a local model fit right now?
        self.book.fit = self._vram_reason

    def engine_room(self):
        """the engine room's /now snapshot, cached for 5 s; None when it is not answering"""
        if self.now() - self._t < 5:
            return self._snap
        try:
            self._snap = self.fetch(self.er_url + "/now")
        except Exception:
            self._snap = None
        self._t = self.now()
        return self._snap

    def _vram_reason(self, agent, model):
        """a model run by an agentic agent needs room for its weights and its pinned context; None = fits (or cannot be told)"""
        if (agent.get("run") or {}).get("via") != "agentic":
            return None
        want = int((self.router.cfg.get("agent_ctx") or {}).get("default") or 65536)
        ok, ctx, need, avail, why = self.vram.plan(model, want)
        return None if ok else why

    def report(self, backend, model, tokens, **extra):
        """tell the engine room one call happened (POST /llm), so its per-model gauges move: it is the one place that knows the shared quota. Never raises, never blocks the run."""
        body = dict(backend=backend, model=model, tokens=int(tokens), **extra)
        try:
            self.poster(self.er_url + "/llm", body)
            self._t = 0.0                                                                # re-read /now next time: the numbers just changed
            return True
        except Exception:
            return False

    def say(self, text):
        try:
            self.poster(self.er_url + "/say", dict(text=str(text)[:140]))
        except Exception:
            pass

    def machine_busy(self):
        """-> reason or None: a GPU is busy or RAM is short (the engine room says so)"""
        s = self.engine_room()
        if not s:
            return None
        gpus = [g.get("util", 0) for g in s.get("gpus", [])]
        if gpus and max(gpus) >= HEAVY_GPU_PCT:
            return "a GPU is %d%% busy (a render may be running)" % max(gpus)
        total, used = s.get("ram_total_gb"), s.get("ram")
        if total and used is not None and (100.0 - float(used)) < MIN_FREE_RAM_PCT:
            return "only %d%% of RAM is free" % (100 - float(used))
        return None

    def limit_pct(self, agent):
        """-> (highest plan window % used, stale?) from the engine room, or (None, None) when it has nothing for this agent"""
        src = (agent.get("limits_source") or "").split(":")
        if src[0] != "engineroom" or len(src) < 2:
            return None, None
        s = self.engine_room()
        rec = ((s or {}).get("limits") or {}).get(src[1])
        if not rec or not rec.get("windows"):
            return None, None
        age = float(rec.get("age_min") or 0)
        live = [w for w in rec["windows"] if not (age and w.get("resets_in_min") is not None and float(w["resets_in_min"]) < age)]   # a window that reset since the reading is empty again
        if not live:
            return 0.0, bool(rec.get("stale"))
        return max(float(w.get("pct") or 0) for w in live), bool(rec.get("stale"))

    def check_pull(self, agent):
        """may an agent that ASKS for work be given some? enabled, not cooling down, under its concurrency and daily cap (no binary / machine checks: it runs itself)"""
        if not agent.get("enabled", True):
            return False, "disabled"
        until = self.jobs.cooldown_until(agent["id"])
        if until:
            return False, "cooling down for %d more min after hitting a limit" % max(1, int((until - self.now()) / 60))
        if self.jobs.runs_active(agent["id"]) >= agent["concurrency"]:
            return False, "already holding a task"
        if self.jobs.runs_today(agent["id"]) >= agent["daily_cap"]:
            return False, "daily cap of %d tasks reached" % agent["daily_cap"]
        return True, "ok"

    def check(self, agent):
        """-> (ok, reason)"""
        aid = agent["id"]
        if not agent.get("enabled", True):
            return False, "disabled" + (" (%s)" % agent["note"] if agent.get("note") else "")
        if (agent.get("run") or {}).get("via") == "pull":
            return False, "pull-only: it takes jobs from the board itself"
        if agent.get("binary") and not self.which(agent["binary"]):
            return False, "%s is not installed" % agent["binary"]
        until = self.jobs.cooldown_until(aid)
        if until:
            return False, "cooling down for %d more min after hitting a limit" % max(1, int((until - self.now()) / 60))
        if (agent.get("run") or {}).get("via") == "agentic":                              # a model working in a container needs Docker and the agent image
            from .agentic import IMAGE
            from ..nightshift import docker_ready, DOCKER_WHY
            if not docker_ready(IMAGE):
                return False, "Docker or the %s image is not ready (%s)" % (IMAGE, (DOCKER_WHY.get(IMAGE) or "unknown")[:120])
        if self.book.models(agent) and not self.book.usable(agent):
            return False, "every model is out: " + "; ".join(self.book.reasons(agent))[:240]
        if self.jobs.runs_active(aid) >= agent["concurrency"]:
            return False, "already at its concurrency limit (%d)" % agent["concurrency"]
        pct, stale = self.limit_pct(agent)
        have_reading = (pct is not None and not stale) or any(self.book._frac(m) is not None for m in self.book.models(agent))      # only a reading the engine room really reports counts (a plan window, or per-model gauges)     # a live plan-window reading (or per-model quotas) is the real governor: the static run count is only a fallback
        cap = agent["daily_cap"] * (CAP_WITH_READING if have_reading and (pct is None or pct < 80) else 1)     # a live reading with room left stretches the static cap; it never removes it
        if self.jobs.runs_today(aid) >= cap:
            return False, "daily cap of %d runs reached%s" % (cap, " (stretched by the live reading)" if cap != agent["daily_cap"] else "")
        if pct is not None and pct >= LIMIT_FULL:                                       # a stale reading of a full window still holds: usage only grows until the window resets
            return False, "plan limit %d%% used%s" % (pct, " (last reading, now stale)" if stale else "")
        if agent["kind"] == "local":
            why = self.machine_busy()
            if why:
                return False, why
            last = self.jobs.db.execute("SELECT outcome, started FROM runs WHERE agent=? AND outcome IS NOT NULL ORDER BY id DESC LIMIT 6", (aid,)).fetchall()
            if len(last) >= 6 and all(r["outcome"] == "failed" for r in last) and self.now() - last[0]["started"] < 1800:
                return False, "its last 6 runs all failed (it is left alone for 30 minutes, then tried again)"
        return True, "ok" if pct is None else "ok (plan %d%%%s)" % (pct, ", reading is stale" if stale else "")
