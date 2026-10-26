"""which model of an agent to use right now. An agent like the Gemini CLI has several models with different skill and different quotas: when the default one runs out (a daily quota can say "retry in 20 h"),
the others usually still have plenty. The book keeps the models in order of SKILL (strongest first, as the owner or the registry lists them), remembers per model when it is cooling down (learned from the
error text) or does not exist (404), and reads the engine room's per-model usage so a model that is nearly used up is skipped before it fails. The agent as a whole is unavailable only when every model is."""
import re, time

from .adapters import parse_reset

INVALID_S = 24 * 3600
DEFAULT_COOL_S = 30 * 60
NOT_FOUND = re.compile(r"\b404\b|is not found|not supported for generateContent|unknown model|model .{0,40} not found", re.I)


class ModelBook:
    def __init__(self, jobs, engine_room=None, now=time.time):
        self.jobs, self.er, self.now = jobs, engine_room, now
        jobs.db.execute("CREATE TABLE IF NOT EXISTS model_cooldowns (agent TEXT, model TEXT, until REAL, why TEXT, kind TEXT, PRIMARY KEY (agent, model))")

    fit = None                                                                         # fit(agent, model) -> reason or None: a local model that will not fit in the free GPU memory is not offered
    router = None                                                                      # set by the Router: the ladders say which models an agent has

    def models(self, agent):
        if self.router:
            seen = []
            for r in self.router.ladder(agent):
                if r.get("model") and r["model"] not in seen:
                    seen.append(r["model"])
            return seen
        return [m for m in ((agent.get("run") or {}).get("models") or []) if isinstance(m, str)]

    def _row(self, agent_id, model):
        return self.jobs.db.execute("SELECT until, why, kind FROM model_cooldowns WHERE agent=? AND model=?", (agent_id, model)).fetchone()

    def _frac(self, model):
        """the engine room's share of this model's daily limit already used (0..1), or None when it has no reading"""
        try:
            snap = self.er() if self.er else None
        except Exception:
            snap = None
        for g in (snap or {}).get("gemini", []) or []:
            if g.get("key") == model:
                return float(g.get("frac") or 0)
        for g in (snap or {}).get("openrouter", []) or []:
            if g.get("key") == model or g.get("id") == model:
                return float(g.get("frac") or 0)
        # OpenRouter free quota is account-wide: any openrouter/* model shares the same day bar
        or_bar = (snap or {}).get("openrouter_account") or {}
        if or_bar and ("/" in (model or "") or (model or "").endswith(":free") or (model or "").startswith("openrouter/")):
            if or_bar.get("frac") is not None:
                return float(or_bar["frac"])
        return None

    def why_not(self, agent, model):
        """-> None when the model may be used now, else the reason"""
        r = self._row(agent["id"], model)
        if r and r["until"] > self.now():
            return "%s until %s (%s)" % ("does not exist" if r["kind"] == "invalid" else "cooling down", time.strftime("%H:%M", time.localtime(r["until"])), (r["why"] or "")[:60])
        f = self._frac(model)
        if f is not None and f >= 0.97:
            return "its daily limit is %d%% used (engine room)" % round(f * 100)
        if self.fit:
            try:
                why = self.fit(agent, model)
            except Exception:
                why = None
            if why:
                return why
        return None

    def usable(self, agent):
        """the models that can be tried now, best first"""
        return [m for m in self.models(agent) if not self.why_not(agent, m)]

    def reasons(self, agent):
        return ["%s: %s" % (m, self.why_not(agent, m)) for m in self.models(agent) if self.why_not(agent, m)]

    def cool(self, agent, model, text):
        until = parse_reset(text or "", self.now())
        until = until + 60 if until and until > self.now() else self.now() + DEFAULT_COOL_S
        until = min(until, self.now() + 25 * 3600)
        with self.jobs.lock:
            self.jobs.db.execute("INSERT OR REPLACE INTO model_cooldowns(agent,model,until,why,kind) VALUES(?,?,?,?,'limit')", (agent["id"], model, until, (text or "")[-160:]))
        return until

    def hold(self, agent, model, until, why=""):
        """stop using a model until a time (the conductor does this to a model that is failing or being saved for later)"""
        with self.jobs.lock:
            self.jobs.db.execute("INSERT OR REPLACE INTO model_cooldowns(agent,model,until,why,kind) VALUES(?,?,?,?,'hold')", (agent["id"], model, until, (why or "")[:160]))

    def invalid(self, agent, model, text=""):
        with self.jobs.lock:
            self.jobs.db.execute("INSERT OR REPLACE INTO model_cooldowns(agent,model,until,why,kind) VALUES(?,?,?,?,'invalid')", (agent["id"], model, self.now() + INVALID_S, (text or "")[-160:]))

    def clear(self, agent_id=None):
        with self.jobs.lock:
            self.jobs.db.execute("DELETE FROM model_cooldowns" + (" WHERE agent=?" if agent_id else ""), (agent_id,) if agent_id else ())

    def status(self, agent):
        return [dict(model=m, usable=not self.why_not(agent, m), why=self.why_not(agent, m) or "", used=self._frac(m)) for m in self.models(agent)]
