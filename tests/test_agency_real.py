"""the agency's real adapters (Stage 1) driven by a FAKE agent CLI (tests/fake_agent.py): patch protocol, protected files, path escape, limit message, timeout, planning, review, the check runner,
and the whole pipeline from a job with no steps to 'ready' - without touching the real target."""
import json, os, shutil, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.store import Board
from agentdispatch.agency.jobs import Jobs
from agentdispatch.agency.registry import Registry
from agentdispatch.agency.availability import Availability
from agentdispatch.agency.dispatcher import Dispatcher
from agentdispatch.agency.realadapters import RealAdapter
from agentdispatch.agency.runner import CheckRunner
from agentdispatch.agency.workspace import Workspace, WorkspaceError
from agentdispatch.agency.adapters import parse_reset
from agentdispatch.agency import policy as P

FAKE = os.path.join(ROOT, "tests", "fake_agent.py")
IDLE = dict(gpus=[dict(util=1.0)], ram=10.0, ram_total_gb=240, limits={})


class Clock:
    t = time.mktime((2026, 10, 16, 14, 0, 0, 0, 0, -1))

    def __call__(self):
        return self.t


FAKE_GOOGLE_KEY = "AIza" + "SyA1234567890" + "abcdefghijklmnopqrstuvwx"   # a fake key for the redaction tests, assembled so secret scanners do not flag the source

def agent(mode, strengths, cost="cheap", timeout=60):
    return dict(kind="cli", name=mode, strengths=strengths, cost=cost, concurrency=1, daily_cap=50, sandbox="copy", enabled=True,
                run=dict(argv=[sys.executable, FAKE, mode], stdin_prompt=True, timeout_s=timeout))


def scratch(buggy=True):
    d = tempfile.mkdtemp(prefix="scratch_")
    open(os.path.join(d, "calc.py"), "w").write("def add(a, b):\n    return a - b\n" if buggy else "def add(a, b):\n    return a + b\n")
    open(os.path.join(d, "test_calc.py"), "w").write("import calc\nassert calc.add(2, 3) == 5, 'add is wrong'\nprint('ok')\n")
    open(os.path.join(d, ".env"), "w").write("API_KEY=" + FAKE_GOOGLE_KEY + "\n")
    return d


def rig(agents, target=None):
    d = tempfile.mkdtemp(prefix="agencyreal_")
    work = tempfile.mkdtemp(prefix="agencywork_")
    clock = Clock()
    board = Board(d, now=clock)
    jobs = Jobs(board)
    reg = Registry(None, agents=agents)
    av = Availability(jobs, fetch=lambda u: IDLE, which=lambda b: "x", now=clock)
    runner = CheckRunner(d, cfg=dict(isolation="none"))
    ad = RealAdapter(work)
    cfg = dict(P.DEFAULTS, work_dir=work)

    def checker(job):
        return runner.run(Workspace(work, job["id"], job.get("created")).project, (job.get("acceptance") or {}).get("check"))
    disp = Dispatcher(board, jobs, reg, av, ad, d, cfg=cfg, checker=checker, now=clock)
    return dict(d=d, work=work, board=board, jobs=jobs, reg=reg, ad=ad, disp=disp, clock=clock, runner=runner)


def done(r):
    r["board"].close()
    for k in ("d", "work"):
        shutil.rmtree(r[k], ignore_errors=True)


def job_for(r, target, steps=True, check="python test_calc.py"):
    j = r["jobs"].create("dev", "fix add", "calc.add returns the wrong sign; make the test pass", "code", target, dict(check=check, rubric="a one-line fix"))
    if steps:
        r["jobs"].add_steps(j["id"], [dict(objective="fix the sign error in calc.py", needs=["code"])])
    return j["id"]


def test_patch_protocol_writes_only_the_copy_and_never_shows_secrets():
    t = scratch()
    r = rig({"b": agent("patch", ["code"]), "e": agent("echo", ["code"], cost="plan")}, t)
    try:
        jid = job_for(r, t)
        res = r["disp"].tick()
        assert res["action"] == "done", res
        ws = Workspace(r["work"], jid, r["jobs"].get(jid)["created"])
        assert "a + b" in ws.read("calc.py") and "a - b" in open(os.path.join(t, "calc.py")).read()          # the copy changed, the real target did not
        assert ws.changes() == {"calc.py": "modified"} and ws.diff_lines() == 2
        assert "-    return a - b" in ws.diff() and "+    return a + b" in ws.diff()
        j = r["jobs"].get(jid)
        assert j["result"]["touched"] == ["calc.py"] and j["result"]["diff_lines"] == 2
        step = j["steps"][0]
        assert step["agent"] == "b" and step["state"] == "done"
        # what an agent is shown: files yes, the .env secret never (checked with the echo agent)
        ad = r["ad"]
        out, err = ad._ask(r["reg"].get("e"), "ignored", None)
        tree, picked = ws.context("fix calc.py", 24000)
        assert ".env" not in tree and all(p != ".env" for p, _ in picked) and not any("AIzaSy" in c for _, c in picked)
    finally:
        done(r)
        shutil.rmtree(t, ignore_errors=True)


def test_bad_answers_are_failures_not_crashes():
    t = scratch()
    try:
        for mode, needle in (("protected", "test file"), ("traverse", "escapes"), ("nojson", "usable")):
            r = rig({"x": agent(mode, ["code"])}, t)
            jid = job_for(r, t)
            out = r["disp"].tick()
            assert out["action"] == "failed" and needle in out["why"], (mode, out)
            ws = Workspace(r["work"], jid, r["jobs"].get(jid)["created"])
            assert ws.changes() == {}, (mode, ws.changes())                                                    # nothing was written
            assert not os.path.exists(os.path.join(os.path.dirname(r["work"]), "evil.py"))
            done(r)
    finally:
        shutil.rmtree(t, ignore_errors=True)


def test_limit_message_sets_a_cooldown_until_the_stated_time_and_a_hang_is_killed():
    t = scratch()
    try:
        r = rig({"lim": agent("limit", ["code"]), "ok": agent("patch", ["code"], cost="plan")}, t)
        jid = job_for(r, t)
        out = r["disp"].tick()
        assert out["action"] == "cooldown" and out["agent"] == "lim", out
        until = r["jobs"].cooldown_until("lim")
        want = parse_reset("try again at 8:47 PM", r["clock"].t)
        assert abs(until - (want + 60)) < 2 and until > r["clock"].t + 5 * 3600                               # the stated reset time, not a blind guess
        assert r["disp"].tick()["agent"] == "ok"                                                              # and work moves on
        done(r)
        r = rig({"h": agent("hang", ["code"], timeout=2)}, t)
        job_for(r, t)
        t0 = time.time()
        out = r["disp"].tick()
        assert out["action"] == "failed" and "timed out" in out["why"] and time.time() - t0 < 30, out
        done(r)
    finally:
        shutil.rmtree(t, ignore_errors=True)
    assert parse_reset("try again in 3 hours", 1000.0) == 1000.0 + 3 * 3600 and parse_reset("nothing here") is None


def test_whole_pipeline_from_a_job_with_no_steps_to_ready():
    t = scratch()
    r = rig({"planner": agent("plan", ["plan"], cost="cheap"), "builder": agent("patch", ["code"], cost="local"), "reviewer": agent("approve", ["review"], cost="plan")}, t)
    try:
        jid = job_for(r, t, steps=False)
        actions = []
        for _ in range(8):
            out = r["disp"].tick()
            actions.append(out["action"])
            if out["action"] in ("ready", "blocked", "job_failed", "idle"):
                break
        assert actions == ["planned", "done", "ready"], actions
        j = r["jobs"].get(jid)
        assert j["state"] == "ready" and j["result"]["check_ok"] is True and "approve to merge" in j["why"]
        assert j["steps"][0]["agent"] == "builder"
        assert "a - b" in open(os.path.join(t, "calc.py")).read()                                             # the real project is untouched
        assert r["jobs"].runs_today("planner") >= 1 and r["jobs"].runs_today("reviewer") == 1
        # a wrong fix: the check fails in the copy, so the gate refuses it and a rework step is added
        r2 = rig({"planner": agent("plan", ["plan"]), "builder": agent("badpatch", ["code"], cost="local"), "reviewer": agent("approve", ["review"], cost="plan")}, t)
        j2 = job_for(r2, t, steps=False)
        for _ in range(4):
            out = r2["disp"].tick()
        job = r2["jobs"].get(j2)
        assert job["result"]["check_ok"] is False and any(s["objective"].startswith("Address the review") for s in job["steps"]), job["steps"]
        done(r2)
    finally:
        done(r)
        shutil.rmtree(t, ignore_errors=True)


def test_review_rejection_is_carried_into_a_rework_step_and_non_code_jobs_deliver():
    t = scratch()
    r = rig({"planner": agent("plan", ["plan"]), "builder": agent("patch", ["code"], cost="local"), "reviewer": agent("reject", ["review"], cost="plan")}, t)
    try:
        jid = job_for(r, t, steps=False)
        for _ in range(4):
            r["disp"].tick()
        steps = r["jobs"].get(jid)["steps"]
        assert any("no explanation of the change" in s["objective"] for s in steps)
    finally:
        done(r)
        shutil.rmtree(t, ignore_errors=True)
    r = rig({"w": agent("answer", ["write", "plan", "review"], cost="cheap"), "w2": agent("approve", ["review"], cost="plan")})
    try:
        g = r["jobs"].create("dev", "haiku", "write a haiku about rain", "general")
        r["jobs"].add_steps(g["id"], [dict(objective="write three lines about rain", needs=["write"])])
        for _ in range(3):
            out = r["disp"].tick()
        job = r["jobs"].get(g["id"])
        assert job["state"] == "delivered" and "rain taps the glass" in job["steps"][0]["evidence"], job
    finally:
        done(r)


def test_check_runner_refuses_unsafe_commands_and_reports_no_isolation():
    t = scratch(buggy=False)
    try:
        runner = CheckRunner(tempfile.gettempdir(), cfg=dict(isolation="none"))
        ok, out = runner.run(t, "python test_calc.py")
        assert ok is True and "ok" in out
        bad = scratch(buggy=True)
        assert runner.run(bad, "python test_calc.py")[0] is False
        shutil.rmtree(bad, ignore_errors=True)
        try:
            runner.run(t, "python -c print(1)")
            assert False
        except ValueError:
            pass
        docker_less = CheckRunner(tempfile.gettempdir(), cfg=dict(isolation="docker", docker_image="no-such-image:0"))
        assert docker_less.run(t, "python test_calc.py")[0] is None                                           # no container: not run, and said so
    finally:
        shutil.rmtree(t, ignore_errors=True)


def test_workspace_refuses_escapes_and_oversize():
    t = scratch()
    work = tempfile.mkdtemp()
    try:
        ws = Workspace.ensure(work, "J-1", t)
        for bad in ("../x.py", "/abs.py", "a/../../b.py", ""):
            try:
                ws.write(bad, "x")
                assert False, bad
            except WorkspaceError:
                pass
        try:
            ws.write("big.py", "x" * 160000)
            assert False
        except WorkspaceError:
            pass
        assert ws.write("new/dir/mod.py", "a = 1\nb = 2\n") == 2 and ws.changes() == {"new/dir/mod.py": "added"}
        assert Workspace.ensure(work, "J-1", t).project == ws.project                                           # reused, not copied again
        try:
            Workspace.ensure(work, "J-2", t, max_mb=0)
            assert False
        except WorkspaceError:
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(t, ignore_errors=True)


def test_a_reviewer_that_hits_its_limit_cools_down_and_the_next_one_reviews():
    t = scratch()
    r = rig({"planner": agent("plan", ["plan"]), "builder": agent("patch", ["code"], cost="local"), "r1": agent("limit", ["review"], cost="cheap"), "r2": agent("approve", ["review"], cost="plan")}, t)
    try:
        jid = job_for(r, t, steps=False)
        acts = [r["disp"].tick()["action"] for _ in range(5)]
        assert acts[:2] == ["planned", "done"] and acts[2] == "cooldown" and acts[3] == "ready", acts                  # r1 hit its limit, r2 reviewed
        assert r["jobs"].cooldown_until("r1") > r["clock"].t
    finally:
        done(r)
        shutil.rmtree(t, ignore_errors=True)
    assert parse_reset("Please retry in 12h27m26.508906422s.", 1000.0) == 1000.0 + 12 * 3600 + 27 * 60 + 26.508906422


def test_workspaces_are_unique_per_job_instance():
    t = scratch()
    work = tempfile.mkdtemp()
    try:
        a = Workspace.ensure(work, "J-000001", t, stamp=1000.0)
        a.write("calc.py", "def add(a, b):" + chr(10) + "    return a + b" + chr(10))
        b = Workspace.ensure(work, "J-000001", t, stamp=2000.0)                                                 # same id, a different job (a reset board): a fresh copy
        assert a.dir != b.dir and b.changes() == {} and a.changes() == {"calc.py": "modified"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    print("ALL AGENCY REAL-ADAPTER TESTS PASSED")
