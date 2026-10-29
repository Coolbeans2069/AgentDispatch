"""the conductor: a live strong-model session that watches the whole machine and changes WHICH MODEL does WHAT as the work goes on. Every few minutes it is shown one compact snapshot (agents and their
availability, every model ladder, the Ollama models installed and loaded, the cloud quotas, per-agent success rates by task kind, what is queued, how the last runs went) and answers with a small set of
swaps as JSON. The broker VALIDATES each swap (real model names only, skill 0..1, bounded count, never leaves an agent with no rung) and writes the accepted ones to routing_overrides.json, which the
router in the broker and in the agency daemon both pick up on their next decision. Every accepted and rejected swap is logged on the board and in conductor_log.jsonl. The deterministic router keeps
working when the conductor is off, down or over its call budget; `conductor_off` (a file in the data folder) or enabled=false stops it."""
import json, os, re, threading, time, urllib.request
from ..paths import OLLAMA_URL as _OLLAMA
from ..textfit import fit as _fit

from . import routing as R

# backend=ollama: the hot local model answers (no Claude). agent is only used when backend=agent.
# apply: "auto" applies valid swaps, "log" only logs what it would do, None = "log" for the small local backend (a 4B model's routing ops were mostly invalid on the stage DB) and "auto" for backend=agent.
# require_valid: one rejected op refuses the whole answer (a half-valid answer usually means the model misread the snapshot). Rounds that fail or are refused back off: interval x2 per bad round, at most max_backoff_s.
DEFAULTS = dict(enabled=True, interval_s=1800, max_changes=4, max_calls_per_day=24, backend="ollama",
                agent="ollama-local", model="local-qwen3-4b:latest", difficulty=0.4, ollama=_OLLAMA,
                keep_alive="24h", num_ctx=8192, apply=None, require_valid=True, max_backoff_s=6 * 3600)
EFFORTS = (None, "low", "medium", "high", "xhigh", "max")
SYSTEM = """You are the CONDUCTOR of a multi-agent machine: local Ollama, OpenRouter FREE-tier models, and subscription CLIs (Claude, Codex, Grok, Gemini). Your job is to route work so jobs finish with the fewest paid tokens and no stalls.
This machine is FREE-ZONE first: OpenRouter :free models and local Ollama are the main capacity. Claude/Codex/Gemini are last resorts when free and local keep failing.
Principles: (1) local + OpenRouter free first; never push work onto Claude to "be safe" when free/local can take it; (2) keep a small hot local (~4B) for drafts and orchestration; large local models only when VRAM fits and the step is hard; (3) on OpenRouter, prefer mid free (lightning, gemma, inkling, north-mini-code, nemotron nano/super) and put ultra last — free accounts rate-limit hard on ultra; (4) after OpenRouter 429/403, hold that model for hours and use another free id; (5) do not churn: change a ladder only when the data says it is wrong, at most a few changes at a time. You may answer {"swaps": []}.
Reply with ONLY a JSON object: {"swaps": [ ... ], "notes": "one or two sentences"}. Each swap is one of:
 {"op": "set_ladder", "agent": "<agent id>", "rungs": [{"model": "<exact model id or null>", "effort": "low|medium|high|xhigh|max|null", "skill": 0..1, "weight": relative token cost}], "why": "..."}   (weakest/cheapest first; replaces that agent's whole ladder)
 {"op": "set_local", "kind": "code", "model": "<an installed Ollama model>", "why": "..."}   (the local model used for that kind of task by the plain local agent)
 {"op": "hold_model", "agent": "<agent id>", "model": "<model>", "hours": 1..24, "why": "..."}   (stop using a model for a while)
Only use model ids that appear in the snapshot (including each agent's known model list). The snapshot's vram section is the GPU memory free for local models right now; a local model whose needs_gb_at_64k exceeds it will not be offered until room appears."""


def http_json(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Conductor:
    def __init__(self, app, ask, fetch=http_json, now=time.time):
        """ask(prompt) -> text (a strong model's answer); fetch(url) -> json (Ollama)"""
        self.app, self.ask, self.fetch, self.now = app, ask, fetch, now
        self.data = app.data
        self.last = 0.0
        self.bad = 0                                                                         # failed/refused rounds in a row (backoff)
        self.stop = threading.Event()

    # ---------------------------------------------------------------- config / switches
    def cfg(self):
        try:
            with open(os.path.join(self.data, "conductor.json"), encoding="utf-8") as f:
                return dict(DEFAULTS, **json.load(f))
        except (OSError, ValueError):
            return dict(DEFAULTS)

    def mode(self, cfg=None):
        cfg = cfg or self.cfg()
        m = cfg.get("apply")
        if m in ("auto", "log"):
            return m
        return "log" if cfg.get("backend", "ollama") == "ollama" else "auto"

    def interval(self, cfg=None):
        cfg = cfg or self.cfg()
        return min(float(cfg["interval_s"]) * (2 ** min(self.bad, 6)), max(float(cfg["interval_s"]), float(cfg.get("max_backoff_s") or 0)))

    def off(self):
        return os.path.exists(os.path.join(self.data, "conductor_off")) or not self.cfg()["enabled"]

    def calls_today(self):
        n, day = 0, time.strftime("%Y-%m-%d", time.localtime(self.now()))
        for e in self._log_lines():
            if e.get("kind") == "call" and e.get("day") == day:
                n += 1
        return n

    def _log_lines(self):
        try:
            with open(os.path.join(self.data, "conductor_log.jsonl"), encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except (OSError, ValueError):
            return []

    def _log(self, kind, **kw):
        with open(os.path.join(self.data, "conductor_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(t=self.now(), day=time.strftime("%Y-%m-%d", time.localtime(self.now())), kind=kind, **kw), default=str) + "\n")

    # ---------------------------------------------------------------- what it sees
    def ollama(self):
        base = self.cfg()["ollama"]
        try:
            tags = self.fetch(base + "/api/tags").get("models", [])
            ps = {m["name"]: m for m in self.fetch(base + "/api/ps").get("models", [])}
        except Exception:
            return []
        out = []
        for m in tags:
            n = m["name"]
            traits = [t for t, rx in (("code", r"coder|code"), ("vision", r"vl\b|vision|llava"), ("reasoning", r"r1|reason|magistral|cascade|think"), ("tiny", r"0\.6b|1b|1\.7b|360m|0\.5b|4b")) if re.search(rx, n, re.I)]
            vw = getattr(self.app.availability, "vram", None)
            need = vw.need_gb(n, 65536) if vw else None
            out.append(dict(model=n, gb=round(m.get("size", 0) / 1e9, 1), loaded=n in ps, traits=traits, needs_gb_at_64k=round(need, 1) if need else None))
        return out

    def snapshot(self):
        app, av = self.app, self.app.availability
        router = av.router
        agents = []
        for aid, a in app.registry.agents.items():
            if not a.get("enabled", True) or (a.get("run") or {}).get("via") == "pull" and aid != "pull-agent":
                continue
            ok, why = av.check(a) if (a.get("run") or {}).get("via") != "pull" else (None, "pull agent")
            ladder = router.ladder(a)
            agents.append(dict(id=aid, strengths=a["strengths"], cost=a["cost"], available=ok, why=(why or "")[:100], ladder=[dict(model=r.get("model"), effort=r.get("effort"), skill=r["skill"], weight=r["weight"]) for r in ladder],
                               held=[x for x in av.book.reasons(a)][:6]))
        db = app.jobs.db
        stats = [dict(agent=r["agent"], kind=r["kind"], ok=r["ok"], n=r["n"]) for r in db.execute("SELECT * FROM route_stats")] if db.execute("SELECT 1 FROM sqlite_master WHERE name='route_stats'").fetchone() else []
        queue = {}
        for r in db.execute("SELECT needs FROM steps WHERE state='todo'"):
            queue[r["needs"]] = queue.get(r["needs"], 0) + 1
        recent = [dict(agent=r["agent"], outcome=r["outcome"], secs=round(r["secs"] or 0)) for r in db.execute("SELECT agent, outcome, secs FROM runs WHERE outcome IS NOT NULL ORDER BY id DESC LIMIT 40")]
        er = av.engine_room() or {}
        vram = getattr(av, "vram", None)
        return dict(vram=vram.status() if vram else None, agents=agents, ollama=self.ollama(), quotas=dict(limits=er.get("limits"), gemini=[dict(key=g.get("key"), frac=g.get("frac"), cap=g.get("cap")) for g in er.get("gemini", [])],
                    gpus=[g.get("util") for g in er.get("gpus", [])]), success=stats, queued_steps_by_need=queue, recent_runs=recent, local_models_by_kind=router.cfg.get("local"))

    # ---------------------------------------------------------------- what it may do
    def known_models(self, snap):
        known = {a["id"]: {r["model"] for r in a["ladder"] if r.get("model")} for a in snap["agents"]}
        olla = {m["model"] for m in snap["ollama"]}
        known["ollama-agent"] = olla | known.get("ollama-agent", set())
        known["ollama-local"] = olla
        # registry run.models (OpenRouter free catalog, Gemini list, …) so the conductor can restore a full free ladder after a bad override
        for aid, a in self.app.registry.agents.items():
            extras = set((a.get("run") or {}).get("models") or [])
            if extras:
                known[aid] = known.get(aid, set()) | extras
            if (a.get("run") or {}).get("model"):
                known.setdefault(aid, set()).add(a["run"]["model"])
        return known, olla

    def validate(self, decision, snap):
        """-> (accepted swaps, [rejection reasons])"""
        known, olla = self.known_models(snap)
        ids = {a["id"] for a in snap["agents"]}
        ok, bad = [], []
        swaps = decision.get("swaps") if isinstance(decision, dict) else None
        if not isinstance(swaps, list):
            return [], ["the answer had no swaps list"]
        for sw in swaps[:20]:
            try:
                op = sw.get("op")
                if op == "set_ladder":
                    if sw.get("agent") not in ids or sw["agent"] not in known:
                        raise ValueError("unknown agent %r" % sw.get("agent"))
                    rungs = sw.get("rungs")
                    if not isinstance(rungs, list) or not 1 <= len(rungs) <= 10:
                        raise ValueError("rungs must be 1 to 10")
                    clean = []
                    for r in rungs:
                        m, e = r.get("model"), r.get("effort")
                        if m is not None and m not in known[sw["agent"]]:
                            raise ValueError("%s is not a known model of %s" % (m, sw["agent"]))
                        if e not in EFFORTS:
                            raise ValueError("bad effort %r" % e)
                        sk, w = float(r["skill"]), float(r["weight"])
                        if not (0 <= sk <= 1 and 0.05 <= w <= 100):
                            raise ValueError("skill must be 0..1 and weight 0.05..100")
                        clean.append(dict(model=m, effort=e, skill=round(sk, 2), weight=round(w, 2)))
                    if not any(c["skill"] >= 0.9 for c in clean) and any(x["skill"] >= 0.9 for a in snap["agents"] if a["id"] == sw["agent"] for x in a["ladder"]):
                        raise ValueError("the new ladder would have nothing strong enough for the hardest work")
                    ok.append(dict(op=op, agent=sw["agent"], rungs=clean, why=str(sw.get("why", ""))[:200]))
                elif op == "set_local":
                    if sw.get("model") not in olla or sw.get("kind") not in R.KINDS + ("write", "research"):
                        raise ValueError("set_local needs an installed Ollama model and a task kind")
                    ok.append(dict(op=op, kind=sw["kind"], model=sw["model"], why=str(sw.get("why", ""))[:200]))
                elif op == "hold_model":
                    if sw.get("agent") not in known or sw.get("model") not in known[sw["agent"]]:
                        raise ValueError("unknown agent or model")
                    h = float(sw.get("hours", 1))
                    ok.append(dict(op=op, agent=sw["agent"], model=sw["model"], hours=max(0.25, min(24.0, h)), why=str(sw.get("why", ""))[:200]))
                else:
                    raise ValueError("unknown op %r" % op)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                bad.append("%s: %s" % (json.dumps(sw, default=str)[:90], e))
        cap = self.cfg()["max_changes"]
        if len(ok) > cap:
            bad += ["over the limit of %d changes per round: %d ignored" % (cap, len(ok) - cap)]
            ok = ok[:cap]
        return ok, bad

    def apply(self, swaps):
        path = os.path.join(self.data, "routing_overrides.json")
        cur = R._read(path)
        cur.setdefault("ladders", {}), cur.setdefault("local", {})
        for sw in swaps:
            if sw["op"] == "set_ladder":
                cur["ladders"][sw["agent"]] = sw["rungs"]
            elif sw["op"] == "set_local":
                cur["local"][sw["kind"]] = sw["model"]
            elif sw["op"] == "hold_model":
                a = self.app.registry.agents[sw["agent"]]
                self.app.availability.book.hold(a, sw["model"], self.now() + sw["hours"] * 3600, "conductor: " + sw["why"])
        cur["updated"] = self.now()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1)
        os.replace(tmp, path)
        self.app.availability.router.reload()

    # ---------------------------------------------------------------- one round
    def step(self, force=False):
        cfg = self.cfg()
        if self.off():
            return dict(action="off")
        if not force and self.now() - self.last < self.interval(cfg):
            return dict(action="not_due")
        if self.calls_today() >= cfg["max_calls_per_day"]:
            return dict(action="budget", why="%d conductor calls today" % cfg["max_calls_per_day"])
        self.last = self.now()
        snap = self.snapshot()
        prompt = SYSTEM + "\n\nSNAPSHOT:\n" + _fit(json.dumps(snap, default=str), int(self.cfg().get("snapshot_chars") or 60000), "snapshot") + "\n\nAnswer with the JSON object only."   # never a silent mid-JSON cut: a marker says what was left out
        self._log("call", chars=len(prompt))
        try:
            text = self.ask(prompt)
        except Exception as e:
            self._log("error", error="%s: %s" % (e.__class__.__name__, str(e)[:200]))
            self.bad += 1
            return dict(action="error", why=str(e)[:200])
        from ..jsonx import extract_json
        decision = extract_json(text, "swaps") if text else None
        swaps, rejected = self.validate(decision, snap)
        mode, refused = self.mode(cfg), bool(rejected and cfg.get("require_valid", True))
        self.bad = self.bad + 1 if rejected else 0
        if refused or mode != "auto":
            self._log("decision", accepted=[], would_apply=swaps, rejected=rejected, mode=mode, refused=refused,
                      notes=str((decision or {}).get("notes", ""))[:300] if isinstance(decision, dict) else "")
            return dict(action="refused" if refused else "logged", accepted=0, would_apply=len(swaps), rejected=len(rejected))
        if swaps:
            self.apply(swaps)
        self._log("decision", accepted=swaps, rejected=rejected, mode=mode, notes=str((decision or {}).get("notes", ""))[:300] if isinstance(decision, dict) else "")
        if swaps:                                                                            # the board hears about real changes only
            try:
                self.app.board.post("conductor", "status", "[conductor] %d swap(s) applied" % len(swaps),
                                    "\n".join(["%s %s %s: %s" % (s["op"], s.get("agent", s.get("kind", "")), s.get("model", ""), s.get("why", "")) for s in swaps] + ["rejected: " + r for r in rejected])[:1500], tags=["conductor"], severity=0)
            except Exception:
                pass
        return dict(action="decided", accepted=len(swaps), rejected=len(rejected))

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.step()
                except Exception as e:
                    self._log("error", error="%s: %s" % (e.__class__.__name__, str(e)[:200]))
                self.stop.wait(30)
        threading.Thread(target=loop, daemon=True, name="conductor").start()
