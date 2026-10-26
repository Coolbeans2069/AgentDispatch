"""jobs and steps: event-sourced on the notice board (events of type job.*), projected into the tables `jobs`, `steps`, `runs` and `cooldowns` of the same SQLite file.
Every job also has a board post (tag `job`) so it shows on the bulletin page, is searchable, and carries the attempt ledger for its steps."""
import json, os, re, shlex, time

from ..similarity import similarity
from ..nightshift import command_ok
from ..store import BoardError

KINDS = ("code", "production", "general")
STATES = ("queued", "planning", "running", "reviewing", "ready", "merged", "delivered", "failed", "cancelled", "blocked")
TERMINAL = ("merged", "delivered", "failed", "cancelled")
STEP_STATES = ("todo", "assigned", "running", "done", "failed", "skipped")
NEEDS = ("code", "review", "long-context", "vision", "fast", "plan", "research", "write", "video", "speech", "audio", "image", "tool", "reasoning")
LEGAL = {"queued": ("planning", "cancelled", "failed"), "planning": ("running", "failed", "cancelled", "blocked"),
         "running": ("reviewing", "failed", "cancelled", "blocked"), "blocked": ("running", "reviewing", "cancelled", "failed"),
         "reviewing": ("ready", "merged", "delivered", "running", "failed", "cancelled", "blocked"), "ready": ("merged", "running", "cancelled", "failed")}
CAPS = dict(title=120, goal=100000, objective=20000, steps=12, target=300, why=4000, rubric=20000, check=2000, step_why=4000)   # goal / objective / rubric are what the agents are told: room for a full brief (they were 4000 / 400 / 1500)
DEFAULT_BUDGET = dict(max_steps=8, max_attempts_per_step=3, wall_min=1440, max_runs=60)
DUP_STEP = 0.85

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, post_id TEXT, title TEXT, goal TEXT, kind TEXT, target TEXT, acceptance TEXT, budget TEXT, priority INTEGER,
  owner TEXT, state TEXT, why TEXT, created REAL, updated REAL, result TEXT);
CREATE TABLE IF NOT EXISTS steps (job_id TEXT, n INTEGER, objective TEXT, needs TEXT, state TEXT, attempts INTEGER DEFAULT 0, agent TEXT, attempt_id TEXT, result_ref TEXT,
  evidence TEXT, updated REAL, post_id TEXT, PRIMARY KEY (job_id, n));
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, job_id TEXT, step_n INTEGER, started REAL, secs REAL, outcome TEXT);
CREATE TABLE IF NOT EXISTS cooldowns (agent TEXT PRIMARY KEY, until REAL, why TEXT, strikes INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, kind TEXT, job_id TEXT, step_n INTEGER, agent TEXT, prompt TEXT, run_id INTEGER, attempt_id TEXT, issued REAL, expires REAL,
  state TEXT, via TEXT);
CREATE TABLE IF NOT EXISTS heartbeats (agent TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS job_meta (job_id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS step_spec (job_id TEXT, n INTEGER, data TEXT, PRIMARY KEY (job_id, n));
CREATE INDEX IF NOT EXISTS i_tasks_open ON tasks(state, job_id, kind, step_n);
CREATE INDEX IF NOT EXISTS i_jobs_state ON jobs(state); CREATE INDEX IF NOT EXISTS i_runs_agent ON runs(agent, started);
"""


def is_code(job):
    """does this job change files in a project folder? Any job of kind code / rebuild, and ANY job that names a target folder and an acceptance check (a `production` job with a project folder and
    `python tools/acceptance.py` is code work too): agents get the project's files and answer with file contents, not prose"""
    return job.get("kind") in ("code", "rebuild") or bool(job.get("target") and (job.get("acceptance") or {}).get("check"))


from ..textfit import fit as _fit

EVIDENCE_CAP = 200000                                                                             # what a step left behind (a tool's output, an answer): kept whole for later steps and reviewers; past this a visible marker, never a silent cut

class Jobs:
    def __init__(self, board, target_ok=None):
        self.b, self.db, self.lock, self.now = board, board.db, board.lock, board.now
        self.target_ok = target_ok                                              # optional: a function(path) -> bool; code jobs may only target folders it accepts
        self.db.executescript(SCHEMA)
        self.listeners = []                                                    # functions(job, from_state, to_state) called after every state change (the governor, the playbook writer)

    # ------------------------------------------------------------ helpers
    def _ev(self, job_id, typ, actor, data=None):
        self.b._event(job_id, "job." + typ, actor, data)

    @staticmethod
    def _row(r):
        d = dict(r)
        for k in ("acceptance", "budget", "needs", "result"):
            if k in d and isinstance(d[k], str) and d[k]:
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    # ------------------------------------------------------------ create / read
    DUP_SIMILARITY = 0.8                    # word overlap (Jaccard) of title+goal at which a new job counts as the same work as an open one

    @staticmethod
    def _words(*texts):
        return set(w for w in re.findall(r"[a-z0-9_]{3,}", " ".join(texts).lower()))

    def find_duplicate(self, title, goal, target="", kind="code"):
        """-> the open job (not finished, failed or cancelled) that already covers this work: the same target and kind, and the same title or nearly the same words; else None"""
        mine, t = self._words(title, goal), (title or "").strip().lower()
        for j in self.db.execute("SELECT id, title, goal, target, kind, state FROM jobs WHERE state NOT IN ('merged','delivered','failed','cancelled') ORDER BY created DESC LIMIT 400"):
            if (j["target"] or "") != (target or "") or j["kind"] != kind:
                continue
            theirs = self._words(j["title"], j["goal"])
            sim = len(mine & theirs) / float(len(mine | theirs) or 1)
            if (j["title"] or "").strip().lower() == t or sim >= self.DUP_SIMILARITY:
                return j["id"]
        return None

    def create(self, owner, title, goal, kind="code", target="", acceptance=None, budget=None, priority=1, meta=None, dedupe=False):
        """dedupe=True (a person or an AI submitting by hand: the web form, the API, the MCP tool) refuses work an open job already covers; the system's own creators (repairs, retries, plans) do not ask"""
        title, goal, target = (title or "").strip(), (goal or "").strip(), (target or "").strip()
        if not title or not goal:
            raise BoardError("empty", "a job needs a title and a goal")
        if kind not in KINDS:
            raise BoardError("bad_kind", "kind must be one of %s" % ", ".join(KINDS))
        if len(title) > CAPS["title"] or len(goal) > CAPS["goal"] or len(target) > CAPS["target"]:
            raise BoardError("too_long", "title <= %d, goal <= %d, target <= %d characters" % (CAPS["title"], CAPS["goal"], CAPS["target"]))
        acc = self._acceptance(acceptance)
        if kind == "code" and not (acc.get("check") or acc.get("rubric")):
            raise BoardError("no_acceptance", "a code job needs an acceptance check (a command that must exit 0) or a rubric a reviewer can score")
        if (kind == "code" or (target and acc.get("check"))) and self.target_ok and not self.target_ok(target):
            raise BoardError("bad_target", "the target folder %r is not one of the allowed project folders: agents may only work on copies of folders you allow (Allow it on the Agency page, or python -m agentdispatch nightshift setup --root FOLDER)" % target)
        if (kind == "code" or (target and acc.get("check"))) and target and self.target_ok and not os.path.isdir(target):
            raise BoardError("bad_target", "the target folder %r does not exist yet. Create it first, and put the acceptance script (%s) in it: agents are not allowed to write the check themselves" %
                             (target, (acc.get("check") if isinstance(acc.get("check"), str) else " ".join(acc.get("check") or [])) or "the test"))
        if kind == "code" and target and self.target_ok and acc.get("check"):         # the acceptance script must already be in the project: an agent may not write the check it is judged by
            argv = shlex.split(acc["check"]) if isinstance(acc["check"], str) else list(acc["check"])
            for a_ in argv:
                if a_.lower().endswith(".py") and not os.path.isfile(a_ if os.path.isabs(a_) else os.path.join(target, a_)):
                    raise BoardError("no_check_script", "the acceptance script %r is not in %s. Put it there first (agents may not write their own check), or point the check at a script that exists" % (a_, target))
        if dedupe:
            dup = self.find_duplicate(title, goal, target, kind)
            if dup:
                raise BoardError("duplicate", "%s is already open for the same work (same target and nearly the same words). Add to it, or cancel it first; pass allow_duplicate to submit anyway" % dup)
        bud = dict(DEFAULT_BUDGET)
        for k, v in (budget or {}).items():
            if k in bud and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000:
                bud[k] = v
        pr = max(0, min(3, int(priority)))
        from ..campaign import meta as MM
        meta = MM.normalize(meta) if meta else None
        with self.lock:
            post = self.b.post("agency", "status", "[job] " + title, goal[:1200], tags=["job", kind], severity=0)
            jid = self.b._next("job", "J")
            now = self.now()
            self.db.execute("INSERT INTO jobs(id,post_id,title,goal,kind,target,acceptance,budget,priority,owner,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (jid, post.get("id"), title, goal, kind, target, json.dumps(acc), json.dumps(bud), pr, owner, "queued", now, now))
            if meta and not MM.is_default(meta):
                self.db.execute("INSERT OR REPLACE INTO job_meta(job_id,data) VALUES(?,?)", (jid, json.dumps(meta)))
            self._ev(jid, "created", owner, dict(title=title, kind=kind, target=target, priority=pr, campaign=(meta or {}).get("campaign", ""), gate=(meta or {}).get("gate", "")))
        return self.get(jid)

    def note(self, jid, text):
        """say why a job is waiting (shown on the Agency page) without changing its state"""
        with self.lock:
            self.db.execute("UPDATE jobs SET why=? WHERE id=? AND (why IS NULL OR why<>?)", ((text or "")[:CAPS["why"]], jid, (text or "")[:CAPS["why"]]))

    def meta(self, jid):
        r = self.db.execute("SELECT data FROM job_meta WHERE job_id=?", (jid,)).fetchone()
        try:
            return json.loads(r["data"]) if r else {}
        except ValueError:
            return {}

    def step_exclude(self, jid, n, agent_id):
        """an agent that honestly said it cannot do this step is not offered it again (a hand-off, not a failure)"""
        spec = self.step_spec(jid, n) or {}
        ex = list(spec.get("exclude") or [])
        if agent_id not in ex:
            ex.append(agent_id)
        spec["exclude"] = ex
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO step_spec(job_id,n,data) VALUES(?,?,?)", (jid, n, json.dumps(spec)))

    def step_spec(self, jid, n):
        r = self.db.execute("SELECT data FROM step_spec WHERE job_id=? AND n=?", (jid, n)).fetchone()
        try:
            return json.loads(r["data"]) if r else {}
        except ValueError:
            return {}

    def _with_spec(self, step):
        step["spec"] = self.step_spec(step["job_id"], step["n"])
        return step

    @staticmethod
    def _acceptance(a):
        a = a or {}
        out = {}
        chk = a.get("check")
        if isinstance(chk, str) and chk.strip():
            try:
                argv = shlex.split(chk.strip())
            except ValueError:
                raise BoardError("bad_check", "the check command could not be parsed")
            out["check"] = chk.strip()[:CAPS["check"]]
        elif isinstance(chk, list) and chk and all(isinstance(x, str) for x in chk):
            argv = [x[:CAPS["check"]] for x in chk][:40]
            out["check"] = argv
        else:
            argv = None
        if argv is not None and not command_ok(argv):                         # the night shift's allow-list: python <script.py> | python -m pytest|unittest - no shell, no -c, no other programs
            raise BoardError("bad_check", "the acceptance check must be `python <script.py>` or `python -m pytest|unittest ...` (no shell, no -c, no other programs)")
        rub = a.get("rubric")
        if isinstance(rub, str) and rub.strip():
            out["rubric"] = _fit(rub.strip(), CAPS["rubric"], "rubric")
        hc = a.get("host_check")                                                      # a second check that runs on THIS computer (the real FreeCAD, ffmpeg, a GPU) after the sandbox check passed
        if isinstance(hc, str) and hc.strip():
            try:
                hargv = shlex.split(hc.strip())
            except ValueError:
                raise BoardError("bad_check", "the host check command could not be parsed")
            if not command_ok(hargv):
                raise BoardError("bad_check", "the host check must be `python <script.py>` (no shell, no -c, no other programs)")
            out["host_check"] = hc.strip()[:CAPS["check"]]
        return out

    def get(self, jid):
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            raise BoardError("not_found", "no such job: %s" % jid)
        d = self._row(r)
        d["steps"] = [self._with_spec(self._row(s)) for s in self.db.execute("SELECT * FROM steps WHERE job_id=? ORDER BY n", (jid,))]
        d["meta"] = self.meta(jid)
        d["shown"] = self.shown_state(d)
        return d

    def shown_state(self, d):
        """what the job page should SAY: a 'running' job none of whose steps has started yet (waiting for a dependency, an agent or the owner) is shown as 'waiting', not 'running'"""
        if d.get("state") != "running":
            return d.get("state")
        st = d.get("steps")
        if isinstance(st, dict):
            started = any(k != "todo" for k in st) if st else False
        else:
            started = any(x.get("state") != "todo" or int(x.get("attempts") or 0) > 0 for x in (st or []))
        if started:
            return "running"
        ran = self.db.execute("SELECT COUNT(*) c FROM runs WHERE job_id=?", (d["id"],)).fetchone()["c"]
        return "running" if ran else "waiting"

    def list(self, state=None, limit=50):
        q, args = "SELECT * FROM jobs", []
        if state:
            q += " WHERE state=?"
            args.append(state)
        q += " ORDER BY priority DESC, created DESC LIMIT ?"
        args.append(max(1, min(200, int(limit))))
        out = []
        for r in self.db.execute(q, args):
            d = self._row(r)
            n = self.db.execute("SELECT state, COUNT(*) c FROM steps WHERE job_id=? GROUP BY state", (d["id"],)).fetchall()
            d["steps"] = {x["state"]: x["c"] for x in n}
            d["shown"] = self.shown_state(d)
            out.append(d)
        return out

    def counts(self):
        return {r["state"]: r["n"] for r in self.db.execute("SELECT state, COUNT(*) n FROM jobs GROUP BY state")}

    def needs_you(self):
        """what only the owner can move: ready-to-merge changes, blocked and failed jobs"""
        return [dict(id=j["id"], title=j["title"], state=j["state"], why=j.get("why") or "", kind=j["kind"]) for j in self.list(limit=200) if j["state"] in ("ready", "blocked", "failed")]

    # ------------------------------------------------------------ state machine
    def set_state(self, jid, state, actor, why=""):
        with self.lock:
            j = self.get(jid)
            if state not in STATES:
                raise BoardError("bad_state", "unknown state %s" % state)
            if j["state"] in TERMINAL:
                raise BoardError("terminal", "the job is already %s" % j["state"])
            if state not in LEGAL.get(j["state"], ()):
                raise BoardError("illegal", "a job cannot go from %s to %s" % (j["state"], state))
            self.db.execute("UPDATE jobs SET state=?, why=?, updated=? WHERE id=?", (state, (why or "")[:CAPS["why"]], self.now(), jid))
            self._ev(jid, "state", actor, dict(frm=j["state"], to=state, why=(why or "")[:CAPS["why"]]))
            if state in TERMINAL and j.get("post_id"):
                try:
                    self.b.reply("agency", j["post_id"], "job %s: %s. %s" % (jid, state, (why or "")[:300]), kind="status")
                except BoardError:
                    pass
        out = self.get(jid)
        for cb in list(self.listeners):
            try:
                cb(out, j["state"], state)
            except Exception as e:                                                  # a listener must never break the state machine
                try:
                    self.b._event(jid, "agency.listener_error", "agency", dict(error="%s: %s" % (e.__class__.__name__, str(e)[:200])))
                except Exception:
                    pass
        return out

    def unblock(self, jid, actor, why="unblocked by the owner"):
        """blocked -> back into the flow: to review when every step is already done, otherwise to running (a job whose steps are all done must not sit in 'running' with nothing to run)"""
        j = self.get(jid)
        if j["state"] != "blocked":
            raise BoardError("illegal", "only a blocked job can be unblocked (this one is %s)" % j["state"])
        return self.set_state(jid, "reviewing" if self.all_steps_done(jid) else "running", actor, why)

    def cancel(self, jid, actor, why="cancelled"):
        j = self.get(jid)
        if j["state"] in TERMINAL:
            return j
        with self.lock:
            self.db.execute("UPDATE steps SET state='skipped', updated=? WHERE job_id=? AND state IN ('todo','assigned','running')", (self.now(), jid))
        return self.set_state(jid, "cancelled", actor, why)

    # ------------------------------------------------------------ steps
    def add_steps(self, jid, steps, actor="planner"):
        j = self.get(jid)
        if j["state"] not in ("queued", "planning"):
            raise BoardError("illegal", "steps can only be added while the job is queued or planning")
        if not isinstance(steps, list) or not steps:
            raise BoardError("bad_steps", "give a non-empty list of steps")
        cap = min(CAPS["steps"], j["budget"]["max_steps"])
        if len(steps) > cap:
            raise BoardError("too_many", "at most %d steps for this job" % cap)
        clean = []
        for i, s in enumerate(steps, 1):
            obj = (s.get("objective") if isinstance(s, dict) else "") or ""
            obj = obj.strip()
            needs = s.get("needs") if isinstance(s, dict) else None
            needs = [needs] if isinstance(needs, str) else list(needs or [])
            if not obj or len(obj) > CAPS["objective"]:
                raise BoardError("bad_steps", "step %d needs an objective of at most %d characters" % (i, CAPS["objective"]))
            bad = [n for n in needs if n not in NEEDS]
            if bad:
                raise BoardError("bad_steps", "step %d: unknown needs %s (known: %s)" % (i, bad, ", ".join(NEEDS)))
            for prev in clean:
                if similarity(obj, prev[0]) >= DUP_STEP:
                    raise BoardError("bad_steps", "step %d repeats an earlier step" % i)
            spec = {}
            if isinstance(s, dict) and isinstance(s.get("tool"), dict):
                t = s["tool"]
                if not isinstance(t.get("tool"), str) or not all(k in ("tool", "args", "code", "file", "expect", "timeout", "project", "cwd") for k in t):
                    raise BoardError("bad_steps", "step %d: tool must be {tool, args | code | file, expect}" % i)
                spec["tool"] = {k: v for k, v in t.items() if k in ("tool", "args", "code", "file", "expect", "timeout", "project", "cwd")}
                needs = needs or ["tool"]
            if isinstance(s, dict) and isinstance(s.get("hint"), str):
                spec["hint"] = _fit(s["hint"], CAPS["step_why"], "hint")      # the planner's hint goes into the step prompt (it was cut to 200 characters)
            who = (s.get("prefer") or s.get("agent")) if isinstance(s, dict) else None              # the planner's pick of agent for this step (honoured by choose() when that agent is healthy)
            if isinstance(who, str) and re.match(r"^[a-z0-9][a-z0-9_.-]{0,39}$", who.strip()):
                spec["prefer"] = who.strip()
            if isinstance(s, dict) and isinstance(s.get("why"), str) and s["why"].strip():
                spec["why"] = _fit(s["why"].strip(), CAPS["step_why"], "why")
            clean.append((obj, needs or ["code" if j["kind"] == "code" else "research"], spec))
        with self.lock:
            if j["state"] == "queued":
                self.set_state(jid, "planning", actor)
            for i, (obj, needs, spec) in enumerate(clean, 1):
                self.db.execute("INSERT INTO steps(job_id,n,objective,needs,state,updated,post_id) VALUES(?,?,?,?,?,?,?)", (jid, i, obj, json.dumps(needs), "todo", self.now(), self._step_post(j, i, obj)))
                if spec:
                    self.db.execute("INSERT OR REPLACE INTO step_spec(job_id,n,data) VALUES(?,?,?)", (jid, i, json.dumps(spec)))
            self._ev(jid, "planned", actor, dict(steps=len(clean)))
            self.set_state(jid, "running", actor, "planned %d step(s)" % len(clean))
        return self.get(jid)

    def _step_post(self, job, n, objective):
        """every step has its own child post, so the attempt ledger (repeat detection, escalation) is scoped to the step and a rework step is not mistaken for a repeat of an earlier one"""
        try:
            return self.b.post("agency", "status", "[%s S%d] %s" % (job["id"], n, objective), objective[:600], tags=["job-step"], parent=job.get("post_id")).get("id")
        except BoardError:
            return job.get("post_id")

    def next_runnable(self, skip_jobs=()):
        """the next todo step: highest job priority, oldest job, lowest step number; jobs over their wall-time budget are failed instead"""
        now = self.now()
        for r in list(self.db.execute("SELECT * FROM jobs WHERE state='running' ORDER BY priority DESC, created ASC")):
            j = self._row(r)
            if j["id"] in skip_jobs:
                continue
            if self._wall_over(j, now):
                self.cancel_steps(j["id"])
                self.set_state(j["id"], "failed", "agency", "ran out of wall-clock budget (%d min)" % j["budget"]["wall_min"])
                continue
            if self.runs_for_job(j["id"]) >= j["budget"]["max_runs"]:
                self.cancel_steps(j["id"])
                self.set_state(j["id"], "failed", "agency", "used its %d agent runs" % j["budget"]["max_runs"])
                continue
            s = self.db.execute("SELECT * FROM steps WHERE job_id=? AND state='todo' ORDER BY n LIMIT 1", (j["id"],)).fetchone()
            if s:
                busy = self.db.execute("SELECT 1 FROM steps WHERE job_id=? AND state IN ('assigned','running')", (j["id"],)).fetchone()
                if not busy:                                                   # steps of one job run one after another
                    j["meta"] = self.meta(j["id"])
                    return j, self._with_spec(self._row(s))
        return None

    def _wall_over(self, j, now):
        """the wall-clock budget counts from the job's FIRST RUN: time spent waiting for its dependencies, a free agent or the owner is not time spent working"""
        first = self.db.execute("SELECT MIN(started) m FROM runs WHERE job_id=?", (j["id"],)).fetchone()["m"]
        return bool(first) and now - first > j["budget"]["wall_min"] * 60

    def replace_dependency(self, old, new):
        """a job was replaced (a repair): everything that waited for it now waits for the replacement"""
        n = 0
        with self.lock:
            for r in list(self.db.execute("SELECT job_id, data FROM job_meta")):
                try:
                    m = json.loads(r["data"])
                except ValueError:
                    continue
                deps = m.get("depends_on_jobs") or []
                if old in deps:
                    m["depends_on_jobs"] = [new if d == old else d for d in deps]
                    self.db.execute("UPDATE job_meta SET data=? WHERE job_id=?", (json.dumps(m), r["job_id"]))
                    n += 1
        return n

    def set_dependencies(self, jid, actor, drop=None, replace=None):
        """the owner re-points a job's depends_on_jobs: drop some ids, replace others (old -> new). -> the new list. A job held by a dead dependency can then be unblocked."""
        drop, replace = set(drop or []), dict(replace or {})
        for k, v in replace.items():
            if not isinstance(v, str) or v == jid:
                raise BoardError("bad_dependency", "a job cannot depend on %r" % (v,))
            self.get(v)                                                             # the new dependency must exist
        with self.lock:
            m = self.meta(jid)
            self.get(jid)
            old = list(m.get("depends_on_jobs") or [])
            unknown = [d for d in list(drop) + list(replace) if d not in old]
            if unknown:
                raise BoardError("bad_dependency", "%s does not depend on %s (it depends on %s)" % (jid, ", ".join(unknown), ", ".join(old) or "nothing"))
            new = []
            for d in old:
                d = replace.get(d, d)
                if d not in drop and d not in new:
                    new.append(d)
            m["depends_on_jobs"] = new
            if self.db.execute("SELECT 1 FROM job_meta WHERE job_id=?", (jid,)).fetchone():
                self.db.execute("UPDATE job_meta SET data=? WHERE job_id=?", (json.dumps(m), jid))
            else:
                self.db.execute("INSERT INTO job_meta (job_id, data) VALUES (?, ?)", (jid, json.dumps(m)))
        try:
            self.b._event(jid, "agency.dependencies", actor, dict(before=old, after=new))
        except Exception:
            pass
        return new

    def iter_runnable(self):
        """every todo step that could run now, in priority order (one per job: steps of a job run one after another) - used by agents that ask for work (pull)"""
        now = self.now()
        for r in list(self.db.execute("SELECT * FROM jobs WHERE state='running' ORDER BY priority DESC, created ASC")):
            j = self._row(r)
            if self._wall_over(j, now) or self.runs_for_job(j["id"]) >= j["budget"]["max_runs"]:
                continue                                                           # next_runnable() fails these; here they are simply not offered
            s = self.db.execute("SELECT * FROM steps WHERE job_id=? AND state='todo' ORDER BY n LIMIT 1", (j["id"],)).fetchone()
            if s and not self.db.execute("SELECT 1 FROM steps WHERE job_id=? AND state IN ('assigned','running')", (j["id"],)).fetchone() and not self.open_task(j["id"], "step", s["n"]):
                j["meta"] = self.meta(j["id"])
                yield j, self._with_spec(self._row(s))

    # ------------------------------------------------------------ tasks: the claim record for every unit of work, pushed or pulled
    def open_task(self, job_id, kind, step_n=0):
        return self.db.execute("SELECT * FROM tasks WHERE job_id=? AND kind=? AND step_n=? AND state='open' AND expires>?", (job_id, kind, step_n, self.now())).fetchone()

    def try_open_task(self, kind, job_id, step_n, agent, prompt="", run_id=None, attempt_id=None, lease_s=1800, via="push"):
        """claim a unit of work: at most one open task per (job, kind, step) - atomic across processes (the check and the insert are one statement). -> task id, or None when it is taken"""
        with self.lock:
            tid = self.b._next("task", "T")
            n = self.db.execute("INSERT INTO tasks(id,kind,job_id,step_n,agent,prompt,run_id,attempt_id,issued,expires,state,via) SELECT ?,?,?,?,?,?,?,?,?,?,'open',? "
                                "WHERE NOT EXISTS (SELECT 1 FROM tasks WHERE job_id=? AND kind=? AND step_n=? AND state='open' AND expires>?)",
                                (tid, kind, job_id, step_n, agent, prompt, run_id, attempt_id, self.now(), self.now() + lease_s, via, job_id, kind, step_n, self.now())).rowcount
        return tid if n else None

    def task_update(self, tid, **kv):
        with self.lock:
            for k, v in kv.items():
                if k in ("run_id", "attempt_id", "prompt", "expires"):
                    self.db.execute("UPDATE tasks SET %s=? WHERE id=?" % k, (v, tid))

    def task_get(self, tid):
        r = self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def task_for_agent(self, agent):
        r = self.db.execute("SELECT * FROM tasks WHERE agent=? AND state='open' AND expires>? ORDER BY issued LIMIT 1", (agent, self.now())).fetchone()
        return dict(r) if r else None

    def task_close(self, tid, state="closed"):
        with self.lock:
            self.db.execute("UPDATE tasks SET state=? WHERE id=? AND state='open'", (state, tid))

    def tasks_expired(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM tasks WHERE state='open' AND expires<=?", (self.now(),))]

    def heartbeat(self, agent):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO heartbeats(agent,ts) VALUES(?,?)", (agent, self.now()))

    def heartbeat_age(self, agent):
        r = self.db.execute("SELECT ts FROM heartbeats WHERE agent=?", (agent,)).fetchone()
        return self.now() - r["ts"] if r else 1e9

    def cancel_steps(self, jid):
        with self.lock:
            self.db.execute("UPDATE steps SET state='skipped', updated=? WHERE job_id=? AND state IN ('todo','assigned','running')", (self.now(), jid))

    def step_set(self, jid, n, state, agent=None, attempt_id=None, evidence=None, result_ref=None, bump=False):
        with self.lock:
            self.db.execute("UPDATE steps SET state=?, agent=COALESCE(?,agent), attempt_id=COALESCE(?,attempt_id), evidence=COALESCE(?,evidence), result_ref=COALESCE(?,result_ref), "
                            "attempts=attempts+?, updated=? WHERE job_id=? AND n=?", (state, agent, attempt_id, (evidence or None) and _fit(evidence, EVIDENCE_CAP, "step result"), result_ref, 1 if bump else 0, self.now(), jid, n))
            self._ev(jid, "step", agent or "agency", dict(n=n, state=state, agent=agent))

    def result_add(self, jid, touched=(), diff_lines=0, note=""):
        """accumulate what the steps changed (kept in the job's result column, so it survives a restart)"""
        with self.lock:
            r = self.db.execute("SELECT result FROM jobs WHERE id=?", (jid,)).fetchone()
            d = json.loads(r["result"]) if r and r["result"] else dict(touched=[], diff_lines=0, notes=[])
            d["touched"] = sorted(set(d["touched"]) | set(touched))
            d["diff_lines"] += int(diff_lines or 0)
            if note:
                d["notes"] = (d["notes"] + [note[:CAPS["why"]]])[-10:]
            self.db.execute("UPDATE jobs SET result=?, updated=? WHERE id=?", (json.dumps(d), self.now(), jid))
        return d

    def result_set(self, jid, **kv):
        """record named facts about the job's result (check outcome, ...) next to the accumulated touched files"""
        with self.lock:
            r = self.db.execute("SELECT result FROM jobs WHERE id=?", (jid,)).fetchone()
            d = json.loads(r["result"]) if r and r["result"] else dict(touched=[], diff_lines=0, notes=[])
            d.update({k: (_fit(v, 20000, k) if isinstance(v, str) else v) for k, v in kv.items()})      # check_out etc.: a pull reviewer reads this (it was cut at 1200 characters)
            self.db.execute("UPDATE jobs SET result=?, updated=? WHERE id=?", (json.dumps(d), self.now(), jid))
        return d

    def rework_step(self, jid, reasons):
        """a review said no: one more step that addresses the reasons (at most 2 rounds; then the caller fails the job)"""
        with self.lock:
            n = self.db.execute("SELECT COALESCE(MAX(n),0) m FROM steps WHERE job_id=?", (jid,)).fetchone()["m"]
            rounds = self.db.execute("SELECT COUNT(*) c FROM steps WHERE job_id=? AND objective LIKE 'Address the review:%'", (jid,)).fetchone()["c"]
            if rounds >= 2:
                return None
            obj = "Address the review: " + "; ".join(str(r) for r in reasons)          # every reason, whole: this is the only thing the rework agent is told about the review (it was cut to 400 characters)
            self.db.execute("INSERT INTO steps(job_id,n,objective,needs,state,updated,post_id) VALUES(?,?,?,?,?,?,?)", (jid, n + 1, obj, json.dumps(["code"]), "todo", self.now(), self._step_post(self.get(jid), n + 1, obj)))
            self._ev(jid, "rework", "agency", dict(step=n + 1, reasons=list(reasons)))
            return n + 1

    def merges_today(self):
        t = time.localtime(self.now())
        midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return self.db.execute("SELECT COUNT(*) FROM jobs WHERE state='merged' AND updated>=?", (midnight,)).fetchone()[0]

    def all_steps_done(self, jid):
        return not self.db.execute("SELECT 1 FROM steps WHERE job_id=? AND state NOT IN ('done','skipped')", (jid,)).fetchone()

    # ------------------------------------------------------------ runs, caps and cooldowns
    def recover(self):
        """after a crash or restart of the dispatcher: runs that never finished are 'abandoned' and steps left assigned / running go back to todo (nothing else is running them)"""
        with self.lock:
            pull_runs = "(SELECT run_id FROM tasks WHERE state='open' AND via='pull' AND run_id IS NOT NULL)"          # work handed to an agent that asked for it (pull) is still its own
            self.db.execute("UPDATE tasks SET state='abandoned' WHERE state='open' AND via='push'")
            self.db.execute("UPDATE runs SET outcome='abandoned', secs=?-started WHERE outcome IS NULL AND id NOT IN " + pull_runs, (self.now(),))
            n = self.db.execute("UPDATE steps SET state='todo', updated=? WHERE state IN ('assigned','running') AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.job_id=steps.job_id AND t.step_n=steps.n "
                                "AND t.kind='step' AND t.state='open' AND t.via='pull')", (self.now(),)).rowcount
            if n:
                self.b._event(None, "agency.recovered", "agency", dict(steps=n))
        return n

    def run_started(self, agent, jid, n):
        with self.lock:
            return self.db.execute("INSERT INTO runs(agent,job_id,step_n,started) VALUES(?,?,?,?)", (agent, jid, n, self.now())).lastrowid

    def run_done(self, run_id, outcome):
        with self.lock:
            self.db.execute("UPDATE runs SET secs=?-started, outcome=? WHERE id=?", (self.now(), outcome, run_id))

    def runs_today(self, agent):
        t = time.localtime(self.now())
        midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE agent=? AND started>=?", (agent, midnight)).fetchone()[0]

    def runs_active(self, agent):
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE agent=? AND outcome IS NULL AND started>?", (agent, self.now() - 3 * 3600)).fetchone()[0]

    def runs_for_job(self, jid):
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE job_id=?", (jid,)).fetchone()[0]

    def cooldown_set(self, agent, why, base_min=20, cap_min=360, until=None):
        """a limit was hit: exponential cooldown (20, 40, 80 ... capped at 6 h) unless the agent said when it resets (`until`, at most 24 h away); a success resets the strikes"""
        with self.lock:
            r = self.db.execute("SELECT strikes FROM cooldowns WHERE agent=?", (agent,)).fetchone()
            strikes = (r["strikes"] if r else 0) + 1
            if until and self.now() < until <= self.now() + 86400:
                until = until + 60
            else:
                until = self.now() + min(cap_min, base_min * 2 ** (strikes - 1)) * 60
            self.db.execute("INSERT OR REPLACE INTO cooldowns(agent,until,why,strikes) VALUES(?,?,?,?)", (agent, until, (why or "")[:200], strikes))
            self.b._event(None, "agency.cooldown", agent, dict(until=until, why=(why or "")[:200], strikes=strikes))
        return until

    def cooldown_clear(self, agent):
        with self.lock:
            self.db.execute("DELETE FROM cooldowns WHERE agent=?", (agent,))

    def cooldown_until(self, agent):
        r = self.db.execute("SELECT until FROM cooldowns WHERE agent=?", (agent,)).fetchone()
        return r["until"] if r and r["until"] > self.now() else 0

    def cooldown_sweep(self):
        """Delete expired cooldown rows. -> list of {agent, until, why} that just became available again.

        Without this, a limit that said 'resets 3:20am' cools the agent correctly, but failed jobs
        that burned attempts before detect_limit was fixed never get reissued when 3:20 arrives.
        """
        now = self.now()
        with self.lock:
            rows = list(self.db.execute(
                "SELECT agent, until, why, strikes FROM cooldowns WHERE until <= ?", (now,)
            ))
            if rows:
                self.db.execute("DELETE FROM cooldowns WHERE until <= ?", (now,))
        out = []
        for r in rows:
            row = dict(agent=r["agent"], until=r["until"], why=r["why"] or "", strikes=r["strikes"] or 0)
            out.append(row)
            try:
                self.b._event(None, "agency.cooldown_expired", r["agent"], dict(until=r["until"], why=(r["why"] or "")[:200]))
            except Exception:
                pass
        return out

    def limit_failed_jobs(self, since_s=36 * 3600, limit=20):
        """Recent *failed* jobs whose why looks like a provider usage/session limit.

        Cancelled (e.g. owner-sealed) jobs are skipped — those must not auto-reissue.
        """
        from .adapters import detect_limit
        cut = self.now() - since_s
        rows = self.db.execute(
            "SELECT id, state, why, title, updated, created FROM jobs "
            "WHERE state='failed' AND COALESCE(updated, created) >= ? "
            "ORDER BY COALESCE(updated, created) DESC LIMIT ?",
            (cut, limit * 3),
        ).fetchall()
        out = []
        for r in rows:
            why = (r["why"] or "").lower()
            if "owner sealed" in why or "owner-sealed" in why or "filesystem seal" in why:
                continue
            raw = r["why"] or ""
            if not detect_limit(raw) and not any(
                k in why for k in ("session limit", "usage limit", "plan limit", "resets", "429", "quota")
            ):
                continue
            out.append(dict(r))
            if len(out) >= limit:
                break
        return out
