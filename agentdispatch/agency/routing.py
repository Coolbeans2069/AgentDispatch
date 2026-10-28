"""token-efficient, quota-aware routing inside an agent. The agent (Claude, Codex, Grok, Gemini) is chosen by the dispatcher; this picks WHICH MODEL and HOW MUCH THINKING it gets.
A ladder lists the rungs of an agent: {model, effort, skill 0..1, weight}. `weight` is the relative token cost of a call at that rung, `skill` how hard a task it is trusted with. A task has a needed
skill (its difficulty, nudged up by its kind and by earlier failures); the router takes the rung that is good enough and has the lowest score = weight x scarcity, where scarcity grows as the model's
own quota (engine room per-model usage) or the agent's plan window fills up. A reading that is stale counts as fuller than it says. Everything is in <data>/routing.json (merged over DEFAULTS),
so the ladders, weights and cut-offs are tuned without code."""
import copy, json, os, time

from .modelbook import ModelBook

DEFAULTS = {
    "scarcity": 4.0,                  # score = weight * (1 + scarcity * used_fraction^2)
    "stale_extra": 0.25,              # a stale limit reading counts this much fuller than it says
    "kind_bump": {"plan": 0.05, "review": 0.0, "code": 0.05, "step": 0.0},
    "retry_bump": 0.12,               # per earlier failed attempt: a retry asks for a stronger rung
    "max_need": 0.97,
    "max_tokens": {"default": 32768, "local": 0},                  # reply budget for models called through the broker router (reasoning models spend part of it thinking)
    "architect": {"interview": 0.4, "study": 0.55, "design": 0.7, "plan": 0.75, "critique": 0.8, "revise": 0.75, "repair": 0.9, "fixtures": 0.6, "default": 0.75},     # how hard each architect call is (picks the rung: haiku ... opus)
    "agent_ctx": {"default": 65536, "models": {}},      # the context window (tokens) a local agent's model is pinned to: Ollama's num_ctx and Claude Code's idea of the window
    "local": {"code": "local-qwen3-4b:latest", "step": "local-qwen3-4b:latest", "write": "local-qwen3-4b:latest", "plan": "local-qwen3-4b:latest"},  # hot 4B stays loaded; conductor/overrides may raise for hard code
    "min_samples": 5, "bad_rate": 0.4,                  # an agent that succeeds less often than this at a kind of task (after this many tries) is tried after the others
    "ladders": {
        "claude-code": [
            dict(model="haiku", effort="low", skill=0.35, weight=1),
            dict(model="sonnet", effort="low", skill=0.55, weight=3),
            dict(model="sonnet", effort="medium", skill=0.7, weight=5),
            dict(model="sonnet", effort="high", skill=0.8, weight=8),
            dict(model="opus", effort="high", skill=0.97, weight=20),
        ],
        "codex-cli": [                    # ids from ~/.codex/models_cache.json (what codex itself lists)
            dict(model="gpt-6-luna", effort="low", skill=0.45, weight=1),
            dict(model="gpt-6-luna", effort="medium", skill=0.55, weight=1.5),
            dict(model="gpt-6-sol", effort="medium", skill=0.7, weight=3),
            dict(model="gpt-6.1-sol", effort="medium", skill=0.8, weight=4),
            dict(model="gpt-6.1-sol", effort="high", skill=0.9, weight=6),
            dict(model="gpt-6-astra", effort="high", skill=0.97, weight=12),
        ],
        "ollama-agent": [                 # hot 4B first (always fits); bigger coders when VRAM allows — conductor may reorder
            dict(model="local-qwen3-4b:latest", effort=None, skill=0.45, weight=0.5),
            dict(model="qwen3-coder:30b", effort=None, skill=0.65, weight=1.5),
            dict(model="local-qwen2.5-coder-32b:latest", effort=None, skill=0.7, weight=2),
            dict(model="nemotron-cascade-2:latest", effort=None, skill=0.75, weight=2.5),
        ],
        "grok-cli": [                     # ids from `grok models`
            dict(model="grok-4.5", effort=None, skill=0.5, weight=1),
            dict(model="grok-4.6", effort=None, skill=0.65, weight=2),
            dict(model="grok-4.7-build-fast", effort=None, skill=0.75, weight=2.5),
            dict(model="grok-4.7", effort=None, skill=0.9, weight=4),
        ],
        "gemini-cli": [
            dict(model="gemini-3.1-flash-lite", effort=None, skill=0.4, weight=0.7),
            dict(model="gemini-3.5-flash-lite", effort=None, skill=0.5, weight=1),
            dict(model="gemini-3-flash-preview", effort=None, skill=0.75, weight=2),
            dict(model="gemini-3.8-flash", effort=None, skill=0.9, weight=3),
        ],
        "openrouter": [                   # FREE tier only; mid first, ultra last (RPD/RPM friendly)
            dict(model="openrouter/free", effort=None, skill=0.4, weight=0.5),
            dict(model="liquid/lfm-2.5-2.6b:free", effort=None, skill=0.35, weight=0.4),
            dict(model="thinkingmachines/inkling-small:free", effort=None, skill=0.45, weight=0.6),
            dict(model="nvidia/nemotron-3.5-lightning:free", effort=None, skill=0.5, weight=0.7),
            dict(model="poolside/laguna-xs-2.1:free", effort=None, skill=0.5, weight=0.7),
            dict(model="cohere/north-mini-code:free", effort=None, skill=0.58, weight=0.9),
            dict(model="google/gemma-4-26b-a4b-it:free", effort=None, skill=0.62, weight=1.1),
            dict(model="thinkingmachines/inkling:free", effort=None, skill=0.68, weight=1.2),
            dict(model="poolside/laguna-s-2.1:free", effort=None, skill=0.7, weight=1.3),
            dict(model="google/gemma-4-31b-it:free", effort=None, skill=0.75, weight=1.5),
            dict(model="xingchen-agi/xing4.0-29b-a4b:free", effort=None, skill=0.72, weight=1.4),
            dict(model="dots-studio/dots-3-note-preview:free", effort=None, skill=0.65, weight=1.2),
            dict(model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", effort=None, skill=0.82, weight=2.0),
            dict(model="nvidia/nemotron-3.5-super-vl:free", effort=None, skill=0.78, weight=2.0),
            dict(model="nvidia/nemotron-3.5-content-safety:free", effort=None, skill=0.6, weight=1.0),
            dict(model="nvidia/nemotron-3-super-120b-a12b:free", effort=None, skill=0.9, weight=3.0),
            dict(model="nvidia/nemotron-3-ultra-550b-a55b:free", effort=None, skill=0.97, weight=5.0),
        ],
    },
    # how each CLI is told: argv appended for a model / an effort
    "flags": {
        "claude-code": dict(model=["--model", "{model}"], effort=["--effort", "{effort}"]),
        "codex-cli": dict(model=["-m", "{model}"], effort=["-c", "model_reasoning_effort=\"{effort}\""]),
        "grok-cli": dict(model=["-m", "{model}"], effort=["--reasoning-effort", "{effort}"]),
        "gemini-cli": dict(model=["-m", "{model}"], effort=None),
        "ollama-agent": dict(model=["--model", "{model}"], effort=None),
        "openrouter": dict(model=None, effort=None),                  # via the broker router: model is chosen in the ladder, not argv
    },
}

BANDS = (("easy", 0.3), ("medium", 0.55), ("hard", 0.8), ("hardest", 0.97))
KINDS = ("step", "code", "plan", "review")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load(data_dir):
    """DEFAULTS < routing.json (the owner's) < routing_overrides.json (the conductor's: whole ladders it replaced, a local model per task kind)"""
    cfg = _merge(DEFAULTS, _read(os.path.join(data_dir, "routing.json")))
    ov = _read(os.path.join(data_dir, "routing_overrides.json"))
    for agent, rungs in (ov.get("ladders") or {}).items():
        if isinstance(rungs, list) and rungs:
            cfg["ladders"][agent] = rungs
    cfg["local"] = dict(cfg.get("local") or {}, **(ov.get("local") or {}))
    return cfg


def _mtime(data_dir):
    t = 0.0
    for n in ("routing.json", "routing_overrides.json"):
        try:
            t = max(t, os.path.getmtime(os.path.join(data_dir, n)))
        except OSError:
            pass
    return t


def key_of(rung):
    return rung.get("model") or "default"


class Router:
    def __init__(self, data_dir, book, engine_room=None, plan_pct=None, now=time.time):
        """book: the ModelBook (cooldowns); engine_room: callable -> /now snapshot; plan_pct: callable(agent) -> (pct, stale) for the agent's plan window"""
        self.data, self.book, self.er, self.plan_pct, self.now = data_dir, book, engine_room, plan_pct, now
        self.cfg = load(data_dir) if data_dir else copy.deepcopy(DEFAULTS)
        self._seen = _mtime(data_dir) if data_dir else 0
        book.router = self

    def _fresh(self):
        """the broker and the agency daemon are separate processes: a change the conductor wrote to routing_overrides.json is picked up by both on their next decision"""
        if self.data and _mtime(self.data) != self._seen:
            self.reload()

    def local_model(self, kind):
        self._fresh()
        return (self.cfg.get("local") or {}).get(kind)

    # ---------------------------------------------------------------- how often an agent really delivers, per kind of task
    def record(self, agent_id, kind, ok):
        db = self.book.jobs.db
        db.execute("CREATE TABLE IF NOT EXISTS route_stats (agent TEXT, kind TEXT, ok INTEGER, n INTEGER, PRIMARY KEY (agent, kind))")
        with self.book.jobs.lock:
            db.execute("INSERT OR IGNORE INTO route_stats(agent,kind,ok,n) VALUES(?,?,0,0)", (agent_id, kind))
            db.execute("UPDATE route_stats SET ok=ok+?, n=n+1 WHERE agent=? AND kind=?", (1 if ok else 0, agent_id, kind))

    def success_rate(self, agent_id, kind):
        """-> (rate, samples) or (None, n) while there are too few samples to say"""
        try:
            r = self.book.jobs.db.execute("SELECT ok, n FROM route_stats WHERE agent=? AND kind=?", (agent_id, kind)).fetchone()
        except Exception:
            return None, 0
        if not r or r["n"] < self.cfg["min_samples"]:
            return None, r["n"] if r else 0
        return r["ok"] / float(r["n"]), r["n"]

    def unreliable(self, agent_id, kind):
        rate, _ = self.success_rate(agent_id, kind)
        return rate is not None and rate < self.cfg["bad_rate"]

    def reload(self):
        self.cfg = load(self.data) if self.data else copy.deepcopy(DEFAULTS)
        self._seen = _mtime(self.data) if self.data else 0

    # ---------------------------------------------------------------- ladders
    def ladder(self, agent):
        self._fresh()
        rungs = self.cfg["ladders"].get(agent["id"])
        if rungs:
            return [dict(r) for r in rungs]
        models = (agent.get("run") or {}).get("models") or []              # an owner-defined agent: its models, best first
        n = len(models)
        return [dict(model=m, effort=None, skill=round(0.95 - 0.5 * i / max(1, n - 1), 2), weight=1.0 + 2 * (n - 1 - i) / max(1, n - 1)) for i, m in enumerate(models)]

    def flags(self, agent):
        f = self.cfg["flags"].get(agent["id"])
        if f:
            return f
        run = agent.get("run") or {}
        return dict(model=run.get("model_flag") or ["-m", "{model}"], effort=None)

    def argv_for(self, agent, rung):
        """the extra command-line words that select this rung"""
        f, out = self.flags(agent), []
        if rung.get("model") and f.get("model"):
            out += [a.replace("{model}", rung["model"]) for a in f["model"]]
        if rung.get("effort") and f.get("effort"):
            out += [a.replace("{effort}", rung["effort"]) for a in f["effort"]]
        return out

    # ---------------------------------------------------------------- how full is a rung
    def _model_frac(self, model):
        try:
            return self.book._frac(model) if model else None
        except Exception:
            return None

    def used(self, agent, rung):
        """-> (fraction 0..1 of the quota this rung draws on, stale?). The model's own daily share (engine room) and the agent's plan window; the fuller one counts."""
        fr, stale = [], False
        m = self._model_frac(rung.get("model"))
        if m is not None:
            fr.append(m)
        if self.plan_pct:
            try:
                pct, st = self.plan_pct(agent)
            except Exception:
                pct, st = None, None
            if pct is not None:
                fr.append(pct / 100.0 + (self.cfg["stale_extra"] if st else 0))
                stale = bool(st)
        return min(1.0, max(fr)) if fr else 0.0, stale

    def score(self, agent, rung):
        u, _ = self.used(agent, rung)
        return rung["weight"] * (1 + self.cfg["scarcity"] * u * u)

    # ---------------------------------------------------------------- the decision
    def need(self, difficulty, kind="step", attempts=0):
        n = float(difficulty) + self.cfg["kind_bump"].get(kind, 0) + self.cfg["retry_bump"] * max(0, attempts)
        return round(max(0.05, min(self.cfg["max_need"], n)), 2)

    def order(self, agent, difficulty=0.5, kind="step", attempts=0):
        """-> the rungs to try, in order: good enough ones by lowest score, then stronger ones; rungs whose model is cooling down, missing or used up are left out.
        Each rung comes with a `why`."""
        need = self.need(difficulty, kind, attempts)
        usable = []
        for r in self.ladder(agent):
            if r.get("model") and self.book.why_not(agent, r["model"]):
                continue
            usable.append(r)
        enough = sorted((r for r in usable if r["skill"] >= need), key=lambda r: (self.score(agent, r), r["skill"]))
        weaker = sorted((r for r in usable if r["skill"] < need), key=lambda r: -r["skill"])
        out = []
        for i, r in enumerate(enough + weaker):
            u, stale = self.used(agent, r)
            why = ("needs skill %.2f: %s %s is the cheapest rung that is good enough (cost %.1f x scarcity)" if r["skill"] >= need else
                   "needs skill %.2f but nothing stronger is usable: %s %s is the best left (cost %.1f)") % (need, r.get("model") or "default model", ("effort " + r["effort"]) if r.get("effort") else "", self.score(agent, r))
            if u >= 0.5:
                why += "; quota %d%% used%s" % (round(u * 100), " (reading is stale, counted fuller)" if stale else "")
            out.append(dict(r, key=key_of(r), need=need, why=why.replace("  ", " ")))
        return out

    def pick(self, agent, difficulty=0.5, kind="step", attempts=0):
        o = self.order(agent, difficulty, kind, attempts)
        return o[0] if o else None

    # ---------------------------------------------------------------- the table the owner reads
    def table(self, agents):
        """task kind x difficulty -> agent / model / effort for the cheapest agent that has a ladder (what the router would choose with nothing used up)"""
        rows = []
        for aid, a in agents.items():
            if not self.cfg["ladders"].get(aid) and not (a.get("run") or {}).get("models"):
                continue
            for kind in KINDS:
                for band, d in BANDS:
                    r = (self.order(a, d - 0.01, kind) or [None])[0]
                    rows.append(dict(agent=aid, kind=kind, difficulty=band, model=(r or {}).get("model") or "default", effort=(r or {}).get("effort") or "-"))
        return rows
