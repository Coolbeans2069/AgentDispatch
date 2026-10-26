"""autonomous mode: the owner's switch that lets a plan that passes every gate be created and run without a person approving it, inside ONE folder (the autonomous projects folder). Turning it on allows that
folder for agents, marks it a safe tree (so accepted changes inside it are applied automatically, each with a backup and a check on the real folder) and records the limits; turning it off puts auto-merge
back. Nothing outside the projects folder is ever merged automatically. The supervisor repairs failed jobs within the budget."""
import json, os, threading, time

from ..agency import policy as POLICY
from ..nightshift import load_config, write_config

TRIALS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "trials")


def _agency_json(app):
    p = os.path.join(app.data, "agency.json")
    try:
        return p, json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return p, {}


def _write(p, d):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def enable(app, projects_root=None, **limits):
    cfg = app.usage.autonomy.load()
    root = os.path.normpath(projects_root or cfg["projects_root"]).replace("\\", "/").rstrip("/")
    os.makedirs(root, exist_ok=True)
    write_config(app.data, allowed_roots=[root])
    p, ag = _agency_json(app)
    trees = list(ag.get("safe_trees") or [])
    if root not in trees:
        trees.append(root)
    ag.update(safe_trees=trees, auto_merge=True, max_merges_per_day=max(int(ag.get("max_merges_per_day", 5)), 60), max_diff_lines=max(int(ag.get("max_diff_lines", 800)), 3000))
    _write(p, ag)
    out = app.usage.autonomy.save(enabled=True, projects_root=root, **{k: v for k, v in limits.items() if v is not None})
    app.localtools.allowed_roots = list(load_config(app.data).get("allowed_roots", []))
    app._disp = None                                                                       # the dispatcher is rebuilt with the new policy
    return out


def disable(app):
    p, ag = _agency_json(app)
    ag["auto_merge"] = False
    _write(p, ag)
    app._disp = None
    return app.usage.autonomy.save(enabled=False)


def trial_text(name):
    p = os.path.join(TRIALS, "%s.md" % "".join(c for c in name if c.isalnum() or c in "_-"))
    if not os.path.isfile(p):
        return None
    return open(p, encoding="utf-8").read()


class Supervisor:
    """every few minutes: for each autonomous plan that is running, look at its jobs; a failed (or stuck for a real reason) job gets one budgeted repair by the architect; when everything is accepted the
    plan is marked done and a summary goes on the board. Infrastructure problems (no reviewer free, a limit, Docker down) are waited out, not repaired."""
    WAIT_WORDS = ("independent reviewer", "no isolation", "cooling", "limit", "waiting for", "the reviewer failed", "no agent available",
                  "held by failed dependency", "needs you:")   # a child parked behind a failed dependency is not itself broken: the dependency is repaired (or re-planned by the watchdog), the child is not rewritten over and over

    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.thread = None
        self.last = {}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            cfg = self.app.usage.autonomy.load()
            try:
                if cfg["enabled"]:
                    due = time.time() - getattr(self, "_last_jobs", 0) >= max(60, int(cfg.get("supervise_every_s", 300)))
                    if due:
                        self._last_jobs = time.time()
                    self.tick(jobs=due)
            except Exception as e:
                try:
                    self.app.board.post("agency", "status", "[supervisor] error: %s" % str(e)[:100], "%s: %s" % (e.__class__.__name__, str(e)[:300]), tags=["supervisor"])
                except Exception:
                    pass
            self.stop.wait(20)                                                                   # queued sessions start within 20 s; the jobs are looked at every supervise_every_s

    def sessions(self):
        from . import Intake
        it = self.app._intake if getattr(self.app, "_intake", None) else Intake(self.app)
        out = []
        for name in sorted(os.listdir(it.dir)) if os.path.isdir(it.dir) else []:
            if name.startswith("S-"):
                try:
                    s = it.get(name)
                except Exception:
                    continue
                if s.get("autonomous") and s["status"] == "submitted" and s.get("phase") not in ("done",):
                    out.append(s)
        return out

    def queued(self):
        """sessions another process (the command line) queued: the broker process starts them"""
        from . import Intake
        it = self.app._intake if getattr(self.app, "_intake", None) else Intake(self.app)
        out = []
        for name in sorted(os.listdir(it.dir)) if os.path.isdir(it.dir) else []:
            if name.startswith("S-"):
                try:
                    s = it.get(name)
                except Exception:
                    continue
                st = (s.get("work") or {}).get("state")
                if st == "queued":
                    out.append(s)
                elif st == "running" and s["status"] == "open" and s.get("autonomous"):                    # the broker restarted while it was thinking: pick the work up again
                    from .web_api import architect_of
                    t = architect_of(self.app).threads.get(s["id"])
                    if not (t and t.is_alive()):
                        s = dict(s, work=dict(s["work"], action="think" if (s.get("blueprint") or {}).get("summary") else "auto"))
                        s["resumed"] = True
                        out.append(s)
        return out

    STALL_S = 2 * 3600

    def watch_stall(self, s, key, j):
        """a job that has sat in running / reviewing for hours without anything changing (an agent that never answers, a pull task nobody took, a dependency nobody can finish) is said out loud
        on the board, once per six hours, with what it is waiting for: a morning summary should never have to discover it"""
        if j["state"] not in ("running", "reviewing") or time.time() - (j.get("updated") or time.time()) < self.STALL_S:
            return
        k = ("stall", s["id"], key)
        if time.time() - self.last.get(k, 0) < 6 * 3600:
            return
        self.last[k] = time.time()
        ok, waiting = self.app.campaign.governor.ready(j)
        text = "waiting: %s" % waiting if not ok else "no agent has moved it for %d h (%s)" % ((time.time() - j["updated"]) / 3600, (j.get("why") or "")[:120])
        try:
            from .web_api import architect_of
            architect_of(self.app).log(s["id"], "stalled", "%s (%s) %s" % (j["id"], key, text))
            self.app.board.post("agency", "status", "[stalled] %s %s" % (j["id"], j["title"][:60]), text[:600], tags=["stalled", "plan"], severity=1)
        except Exception:
            pass

    def waiting_review(self):
        from . import Intake
        it = self.app._intake if getattr(self.app, "_intake", None) else Intake(self.app)
        out = []
        for name in sorted(os.listdir(it.dir)) if os.path.isdir(it.dir) else []:
            if name.startswith("S-"):
                try:
                    s = it.get(name)
                except Exception:
                    continue
                if s.get("phase") == "waiting_review" and s.get("autonomous") and s["status"] == "open" and time.time() >= s.get("review_at", 0) and (s.get("work") or {}).get("state") != "running":
                    out.append(s)
        return out

    def tick(self, jobs=True):
        from .web_api import architect_of, intake_of
        arch, it = architect_of(self.app), intake_of(self.app)
        for s in self.queued():
            text = next((m["text"] for m in s["messages"] if m["role"] == "user"), "")
            if text:
                try:
                    if s.get("resumed"):
                        it.update(s["id"], lambda se: se.update(work=dict(state="idle")))             # clear the stale "running" so the work can start again
                    arch.start(s["id"], s["work"].get("action", "auto"), text)
                except Exception:
                    pass
        for s in self.waiting_review():
            try:
                arch.start(s["id"], "review", "")
            except Exception:
                pass
        for s in (self.sessions() if jobs else []):
            made = s.get("made") or {}
            states = {}
            for key, jid in made.items():
                try:
                    j = self.app.jobs.get(jid)
                except Exception:
                    continue
                states[key] = j
            if not states:
                continue
            for key, j in states.items():
                self.watch_stall(s, key, j)
                why = (j.get("why") or "").lower()
                broken = j["state"] == "failed" or (j["state"] == "blocked" and not any(w in why for w in self.WAIT_WORDS))
                if broken and time.time() - self.last.get((s["id"], key), 0) > 600:
                    self.last[(s["id"], key)] = time.time()
                    arch.repair(s["id"], key)
            if s.get("phase") == "waiting_review":
                pass
            if all(j["state"] in ("merged", "delivered") for j in states.values()):
                it.update(s["id"], lambda se: se.update(phase="done"))
                arch.log(s["id"], "done", "Every job of the plan is accepted.")
                try:
                    self.app.board.post("agency", "status", "[plan done] %s" % (s["blueprint"].get("title") or s["id"]), "All %d job(s) of the plan were accepted. Project folder(s): %s" % (
                        len(states), ", ".join(sorted({x["target"] for x in states.values() if x.get("target")})) or "-"), tags=["plan", "done"])
                except Exception:
                    pass
