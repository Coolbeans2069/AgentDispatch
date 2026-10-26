"""the stuck-job watchdog once a dependency's copies keep failing: it hands the dependency to the architect's re-plan path (with every failed copy's reason), or tells the owner ONCE and
then leaves the job alone; it never asks the small model when the rules already decided; jobs whose steps have not started are shown as waiting, not running."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_agency import rig, done
from agentdispatch.agency import watchdog as W
from agentdispatch.intake import Intake


class App:
    pass


def setup(copies=W.MAX_FAILED_COPIES):
    r = rig()
    jobs = r["jobs"]
    dep = None
    for i in range(copies):
        dep = jobs.create("dev", "Probe the tool (retry)" if i else "Probe the tool", "goal %d" % i, "general")
        jobs.set_state(dep["id"], "failed", "t", "environment problem %d" % i)
    child = jobs.create("dev", "Use the tool", "goal", "general", meta=dict(depends_on_jobs=[dep["id"]]))
    jobs.add_steps(child["id"], [dict(objective="use the probe result to do the thing", needs=["write"])])
    jobs.set_state(child["id"], "blocked", "t", "held by failed dependency: %s" % dep["id"])
    app = App()
    app.jobs, app.registry, app.board, app.data = jobs, r["reg"], r["board"], r["d"]
    return r, app, dep["id"], child["id"]


def test_capped_dependency_is_escalated_once_and_never_sent_to_the_llm():
    r, app, dep, child = setup()
    asked = []
    real = W._ask_local
    W._ask_local = lambda *a, **k: asked.append(1) or {"action": "retry_dep"}
    cfg = dict(W.DEFAULTS, use_llm=True)
    stuck = W.find_stuck(app, cfg)
    assert [s["job"]["id"] for s in stuck] == [child], stuck
    d = W.decide(app, stuck[0], cfg)
    assert d["action"] == "replan" and d["dep_id"] == dep and d.get("via") == "rule", d
    res = W.apply_decision(app, d)
    assert res["ok"] and res["action"] == "escalate" and "already failed" in res["why"], res
    assert "not part of an autonomous plan" in res["why"], res
    assert r["jobs"].get(child)["why"].startswith("needs you:"), r["jobs"].get(child)["why"]
    issues = r["board"].read(kind="issue", limit=50)
    issues = issues.get("posts", issues) if isinstance(issues, dict) else issues
    assert len(issues) == 1 and "needs you" in issues[0]["title"], issues
    # the next ticks leave it alone: no re-decision, no second post
    assert W.find_stuck(app, cfg) == [], "an escalated job is not re-decided every tick"
    again = W.escalate(app, child, dep, "x")
    assert again.get("already"), again
    W._ask_local = real
    assert not asked, "the small model is not asked when the rules decided"
    done(r)


def test_capped_dependency_goes_to_the_architect_with_its_failure_history():
    r, app, dep, child = setup()
    it = Intake(app)
    app._intake = it
    s = it.new(autonomous=True)
    first_copy = r["jobs"].db.execute("SELECT id FROM jobs WHERE title='Probe the tool'").fetchone()["id"]
    it.update(s["id"], lambda se: se.update(made={"probe": first_copy}, status="submitted"))
    calls = []

    class Arch:
        intake = it

        def repair(self, sid, key, history=None, rewrite=False):
            calls.append((sid, key, history, rewrite))
            new = r["jobs"].create("architect (repair)", "Probe the tool, rewritten", "better goal", "general")
            it.update(sid, lambda se: se["made"].update({key: new["id"]}))
            return new
    app._architect = Arch()
    res = W.apply_decision(app, dict(action="replan", job_id=child, dep_id=dep, why="%d copies already failed" % W.MAX_FAILED_COPIES))
    assert res["ok"] and res["action"] == "replan" and res["session"] == s["id"] and res["key"] == "probe", res
    sid, key, history, rewrite = calls[0]
    assert rewrite is True and len(history) == W.MAX_FAILED_COPIES and all("environment problem" in h["why"] for h in history), history
    assert r["jobs"].meta(child)["depends_on_jobs"] == [res["last"]], r["jobs"].meta(child)
    assert child in res["unblocked"] and r["jobs"].get(child)["state"] != "blocked"
    done(r)


def test_below_the_cap_the_rules_still_retry_without_asking_the_llm():
    r, app, dep, child = setup(copies=1)
    cfg = dict(W.DEFAULTS, use_llm=True)
    stuck = W.find_stuck(app, cfg)
    d = W.decide(app, stuck[0], cfg)
    assert d["action"] == "retry_dep" and d.get("via") == "rule", d
    done(r)


def test_a_running_job_whose_steps_have_not_started_is_shown_as_waiting():
    r = rig()
    j = r["jobs"].create("dev", "later", "goal", "general")
    r["jobs"].add_steps(j["id"], [dict(objective="do the first part of the work", needs=["write"])])
    assert r["jobs"].get(j["id"])["shown"] == "waiting"
    assert [x["shown"] for x in r["jobs"].list(state="running")] == ["waiting"]
    r["jobs"].db.execute("UPDATE steps SET attempts=1 WHERE job_id=?", (j["id"],))
    assert r["jobs"].get(j["id"])["shown"] == "running"
    done(r)


def test_the_supervisor_does_not_rewrite_a_child_held_by_a_failed_dependency():
    from agentdispatch.intake.autonomy import Supervisor
    assert any(w in "held by failed dependency: J-000172" for w in Supervisor.WAIT_WORDS)
    assert any(w in "needs you: 4 copies already failed" for w in Supervisor.WAIT_WORDS)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
