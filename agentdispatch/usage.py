"""keeping an eye on usage. Every strong-model call the architect makes is written to <data>/usage.jsonl (who, how long, an estimate of the tokens: characters / 4); the agency's own runs are in its
runs table and the plan windows come from the engine room. `Autonomy` holds the owner's overnight limits (how many strong calls per plan and per day, the plan-window percentage at which a model is left
alone, how many repairs a plan may attempt) and `summary()` is the one picture: strong versus local work, per agent against its cap, plan windows."""
import json, os, threading, time
from . import paths

DEFAULTS = dict(
    enabled=False,                         # autonomous mode: a ready plan is approved and created by itself, jobs are repaired by the supervisor
    projects_root=paths.PROJECTS_ROOT,      # autonomous projects live under here; the folder is allowed and auto-merge is on for it (safe_trees)
    strong_calls_per_session=30,           # strong-model calls (architect passes) one intake may use
    strong_calls_per_day=90,
    plan_pct_stop=85,                      # a model whose plan window is this full is left alone (the engine room's reading)
    max_repairs_per_plan=3,
    supervise_every_s=300,
    auto_install=True,
)


def midnight(now):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


class Autonomy:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "autonomy.json")

    def load(self):
        cfg = dict(DEFAULTS)
        try:
            cfg.update({k: v for k, v in json.load(open(self.path, encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return cfg

    def save(self, **kv):
        cfg = self.load()
        for k, v in kv.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                cfg[k] = v
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=1)
        os.replace(tmp, self.path)
        return cfg


class Usage:
    def __init__(self, app, now=time.time):
        self.app, self.now = app, now
        self.path = os.path.join(app.data, "usage.jsonl")
        self.lock = threading.Lock()
        self.autonomy = Autonomy(app.data)

    def record(self, kind, who, secs=0.0, chars_in=0, chars_out=0, session=None, ok=True, note=""):
        rec = dict(t=self.now(), kind=kind, who=who, secs=round(secs, 1), tokens_est=int((chars_in + chars_out) / 4), session=session, ok=ok, note=note[:120])
        with self.lock, open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")

    def rows(self, since=0.0):
        out = []
        try:
            with open(self.path, "rb") as f:
                f.seek(max(0, os.path.getsize(self.path) - 600000))
                for l in f.read().decode("utf-8", "replace").splitlines():
                    try:
                        r = json.loads(l)
                    except ValueError:
                        continue
                    if r["t"] >= since:
                        out.append(r)
        except OSError:
            pass
        return out

    def allow_strong(self, agent_id, session=None):
        """-> (ok, why): may the architect spend a strong-model call on this agent now?"""
        cfg = self.autonomy.load()
        today = [r for r in self.rows(midnight(self.now())) if r["kind"] == "strong"]
        if len(today) >= cfg["strong_calls_per_day"]:
            return False, "today's budget of %d strong-model calls is used" % cfg["strong_calls_per_day"]
        if session and len([r for r in today if r.get("session") == session]) >= cfg["strong_calls_per_session"]:
            return False, "this plan has used its %d strong-model calls" % cfg["strong_calls_per_session"]
        a = self.app.registry.agents.get(agent_id)
        if a:
            pct, stale = self.app.availability.limit_pct(a)
            if pct is not None and not stale and pct >= cfg["plan_pct_stop"]:
                return False, "%s's plan window is %d%% used (the limit for autonomous work is %d%%)" % (agent_id, pct, cfg["plan_pct_stop"])
        return True, ""

    def summary(self):
        now = self.now()
        start = midnight(now)
        rows = self.rows(start)
        strong = [r for r in rows if r["kind"] == "strong"]
        by = {}
        for r in strong:
            d = by.setdefault(r["who"], dict(calls=0, tokens_est=0, secs=0.0))
            d["calls"] += 1
            d["tokens_est"] += r["tokens_est"]
            d["secs"] += r["secs"]
        local_llm = sum(1 for r in rows if r["kind"] == "local")
        tool_runs = sum(1 for t in self.app.localtools.history(2000) if t.get("t", 0) >= start)
        agents = []
        for aid, a in self.app.registry.agents.items():
            if not a.get("enabled", True) or aid == "local-tools":
                continue
            pct, stale = self.app.availability.limit_pct(a)
            agents.append(dict(id=aid, name=a.get("name", aid), cost=a["cost"], runs_today=self.app.jobs.runs_today(aid), daily_cap=a["daily_cap"], plan_pct=pct, stale=stale,
                               cooling=bool(self.app.jobs.cooldown_until(aid)), architect_calls=by.get(aid, {}).get("calls", 0), architect_tokens_est=by.get(aid, {}).get("tokens_est", 0)))
        runs = self.app.jobs.db.execute("SELECT agent, COUNT(*) n FROM runs WHERE started>=? GROUP BY agent", (start,)).fetchall()
        local_runs = sum(r["n"] for r in runs if r["agent"] in ("ollama-local", "local-tools"))
        strong_runs = sum(r["n"] for r in runs) - local_runs
        cfg = self.autonomy.load()
        return dict(ok=True, since=start, limits=cfg, strong_calls_today=len(strong), strong_tokens_est=sum(r["tokens_est"] for r in strong), local_model_calls=local_llm, local_tool_runs=tool_runs,
                    agency_runs_local=local_runs, agency_runs_strong=strong_runs, local_share=round(100.0 * (local_llm + tool_runs + local_runs) / max(1, local_llm + tool_runs + local_runs + len(strong) + strong_runs)),
                    agents=agents, strong_by_agent=by)
