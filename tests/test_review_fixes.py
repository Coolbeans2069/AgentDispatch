"""fixes from the 2026-10-25 review of the fix series: a pull agent's plan gets the same validation as a launched planner's, a limited prompt gives up only the room the earlier
results really take, the watchdog counts only real copies of a job, and the pinned-window model names work against an Ollama-shaped HTTP server"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_agency import rig, done
from agentdispatch.agency import prompts as PR
from agentdispatch.agency.realadapters import RealAdapter, BUDGET

PULL = "pull-agent"


def test_a_pull_agents_plan_is_validated_told_once_then_refused():
    r = rig(checker=lambda job: True)
    for aid, a in r["reg"].agents.items():
        a["enabled"] = aid in (PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    disp, jobs = r["disp"], r["jobs"]
    g = jobs.create("dev", "sum it", "add up the numbers", "general")
    o = disp.offer(PULL)["task"]
    assert o["kind"] == "plan"
    bad = json.dumps(dict(steps=[dict(objective="read C:/data/x.csv and add it up", needs=["write"])]))
    res = disp.submit(PULL, o["task_id"], bad)
    assert res["ok"] and res["result"]["action"] == "plan_refused" and "host-only" in res["result"]["problems"][0], res
    assert jobs.get(g["id"])["steps"] == [] and jobs.task_get(o["task_id"])["state"] == "open"                    # not applied, the task is still the agent's
    again = disp.offer(PULL)["task"]
    assert again["task_id"] == o["task_id"] and "YOUR PLAN WAS REFUSED" in again["prompt"] and "reasoning" in again["prompt"]
    res = disp.submit(PULL, o["task_id"], json.dumps(dict(steps=[dict(objective="x", needs=["telepathy"])])))
    assert res["ok"] is False and res["error"] == "bad_plan" and "after one re-ask" in res["message"], res
    assert jobs.get(g["id"])["steps"] == [] and jobs.get(g["id"])["state"] == "queued"
    o = disp.offer(PULL)["task"]                                                                                  # a new task: a good plan is taken as before
    assert disp.submit(PULL, o["task_id"], json.dumps(dict(steps=[dict(objective="add the numbers in the notes", needs=["write"])])))["result"]["action"] == "planned"
    done(r)


class _WS:
    def __init__(self):
        self.budgets = []

    def files(self):
        return []

    def context(self, objective, budget, priority=(), skip=()):
        self.budgets.append(budget)
        return [], []

    def diff_budget(self, n):
        self.budgets.append(n)
        return "diff --git a/x b/x", []


def _adapter(ws):
    ad = RealAdapter.__new__(RealAdapter)
    ad._ctx = lambda job: (ws, None, None)
    ad.workspace = lambda job: ws
    ad.tools_text = lambda step: ""
    return ad


def test_a_limited_prompt_gives_up_only_what_earlier_results_take():
    ws = _WS()
    ad = _adapter(ws)
    agent = dict(id="grok-cli", cost="plan", run=dict(max_prompt_chars=32000))
    job = dict(id="J-1", kind="code", target="D:/p", goal="g", meta={}, steps=[dict(n=1, state="todo", objective="o")])
    ad.step_request(agent, job["steps"][0], job)
    assert ws.budgets[-1] == min(BUDGET["plan"], 32000 - 6000), ws.budgets                                       # a first step has no earlier results: the whole file budget
    job["steps"] = [dict(n=1, state="done", objective="probe", agent="a", evidence="y" * 2000), dict(n=2, state="todo", objective="o")]
    used = len(PR.earlier_results(job, job["steps"][1], total=ad.earlier_budget(agent)))
    ad.step_request(agent, job["steps"][1], job)
    assert used > 2000 and ws.budgets[-1] == min(BUDGET["plan"], 32000 - 6000 - used), (used, ws.budgets)
    ad.review_request(dict(job, steps=[]), {}, agent)
    assert ws.budgets[-1] == ad.review_budget(agent), ws.budgets                                                   # nothing earlier: the reviewer sees the whole diff budget
    ad.review_request(job, {}, agent)
    assert ws.budgets[-1] == ad.review_budget(agent) - len(PR.earlier_results(job, dict(n=10 ** 6), total=ad.earlier_budget(agent)))
    ad.step_request(dict(agent, run={}), job["steps"][1], job)
    assert ws.budgets[-1] == BUDGET["plan"]                                                                       # an unlimited prompt is unchanged


def test_the_watchdog_counts_only_real_copies_of_a_job():
    from agentdispatch.agency import watchdog as W
    from types import SimpleNamespace
    r = rig()
    jobs = r["jobs"]
    app = SimpleNamespace(jobs=jobs)
    t = "Probe the local transcribe tool and record verified evidence"
    mk = lambda title, target="D:/p/huddle", state="failed": (lambda j: (jobs.db.execute("UPDATE jobs SET state=?, target=? WHERE id=?", (state, target, j["id"])), j)[1])(jobs.create("dev", title, "g", "general"))
    a = mk(t)
    mk(t + " (retry)"), mk(t + " (retry 2) (retry)", state="cancelled"), mk(t + " (retr")           # copies, also one cut off mid-tag
    mk(t + " v2"), mk(t.upper()), mk(t, target="D:/p/other"), mk("Probe_the local transcribe tool%"), mk(t, state="running")   # not copies
    assert W.failed_copies(app, jobs.get(a["id"])) == 4
    assert len(W.failure_history(app, jobs.get(a["id"]), limit=2)) == 2
    long = "Summarise the meeting notes of the huddle project into a one page brief with owners and due dates for each"   # > 95 characters
    b = mk(long)
    mk((long[:95] + " (retry)").strip()[:120])                                                                     # how the watchdog titles a copy
    c2 = (long[:95] + " (retry)")
    mk((c2[:95] + " (retry)").strip()[:120])                                                                       # a copy of the copy: the tag was cut to " (ret"
    mk(long[:80] + " something else")
    assert W.failed_copies(app, jobs.get(b["id"])) == 3, [x["title"] for x in W._copies(app, jobs.get(b["id"]))]
    assert W._base_title("abc (retry 3) (retry") == "abc" and W._base_title("abc (retry) (ret") == "abc" and W._base_title("abc (") == "abc"
    done(r)


class _Ollama(BaseHTTPRequestHandler):
    models, created, refuse = [], [], False

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(200, dict(models=[dict(name=m, model=m) for m in _Ollama.models]))
        self._send(404, dict(error="not found"))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/create":
            _Ollama.created.append(body)
            if _Ollama.refuse or body.get("from") not in _Ollama.models:
                return self._send(400, dict(error="model '%s' not found" % body.get("from")))
            _Ollama.models.append(body["model"] if ":" in body["model"] else body["model"] + ":latest")
            return self._send(200, dict(status="success"))
        self._send(404, dict(error="not found"))


def test_pinned_window_names_against_a_mock_ollama():
    from agentdispatch.agency.agentic import AgentEnv
    srv = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d" % srv.server_port
        _Ollama.models[:] = ["nemotron-cascade-2:latest", "qwen3-coder:30b"]
        cfg = lambda: dict(default=65536, models={"nemotron-cascade-2:latest": 32768})
        env = AgentEnv(None, None, None, ollama=url, cfg=cfg)                                                 # the real HTTP fetch
        assert env.variant("nemotron-cascade-2:latest") == ("nemotron-cascade-2-ctx32k:latest", 32768)
        c = _Ollama.created[-1]
        assert c["model"] == "nemotron-cascade-2-ctx32k:latest" and c["from"] == "nemotron-cascade-2:latest" and c["parameters"] == dict(num_ctx=32768)
        assert "nemotron-cascade-2-ctx32k:latest" in _Ollama.models and not [m for m in _Ollama.models if m.endswith("-ctx32k")]
        n = len(_Ollama.created)
        assert env.variant("nemotron-cascade-2:latest")[0] == "nemotron-cascade-2-ctx32k:latest" and len(_Ollama.created) == n   # listed: not created again
        assert env.variant("nemotron-cascade-2:latest-ctx32k")[0] == "nemotron-cascade-2-ctx32k:latest"                         # the old (broken) name is re-based
        _Ollama.refuse = True
        assert env.variant("qwen3-coder:30b") == ("qwen3-coder:30b", 65536)                                                    # Ollama would not create it: the plain model runs
        assert env.variant("not-installed:7b")[0] == "not-installed:7b"
        _Ollama.refuse = False
        dead = AgentEnv(None, None, None, ollama="http://127.0.0.1:9", cfg=cfg)                                                 # Ollama down: the plain model, no exception
        assert dead.variant("qwen3-coder:30b")[0] == "qwen3-coder:30b"
    finally:
        srv.shutdown()



def test_round_two_portability_and_safety_fixes():
    """a Windows absolute path is refused as a check script on every OS, a very deep calculation is refused on every Python version, and a Lean that cannot answer --version is not offered"""
    import tempfile, shutil
    from agentdispatch.nightshift import command_ok
    for bad in (["python", "C:\\x.py"], ["python", "c:/x.py"], ["python", "\\\\srv\\share\\x.py"], ["python", "/x.py"], ["python", "..\\x.py"], ["python", "a/../../x.py"]):
        assert not command_ok(bad), bad
    assert command_ok(["python", "tools/check.py"]) and command_ok(["python", "tools\\check.py"])
    from agentdispatch.localtools import policy as LP
    assert LP.check_calc("x = " + "1+" * 9000 + "1") == ["the code is nested too deeply to check"]
    assert LP.check_calc("import math\nprint(sum(math.sqrt(i) for i in range(10)))") == []
    d = tempfile.mkdtemp(prefix="leanshim_")
    old = os.environ.get("PATH", "")
    try:
        if os.name == "nt":
            open(os.path.join(d, "lean.cmd"), "w").write("@echo error: no default toolchain configured\r\n@exit /b 1\r\n")
        else:
            open(os.path.join(d, "lean"), "w").write("#!/bin/sh\necho 'error: no default toolchain configured'\nexit 1\n")
            os.chmod(os.path.join(d, "lean"), 0o755)
        os.environ["PATH"] = d + os.pathsep + old
        from agentdispatch import localtools
        box = localtools.build(d, [d], os.path.join(d, "work"))
        t = box.reg.scan(force=True)["tools"]["lean"]
        assert not t["available"] and "no default toolchain" in t["note"] and "can be installed" in t["note"], t
    finally:
        os.environ["PATH"] = old
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
