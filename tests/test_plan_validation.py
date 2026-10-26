"""a plan is checked before it becomes steps: needs that no enabled, installed agent has and host-only files in sandbox steps are refused, the planner is told once (with the full NEEDS list) and asked again"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_agency import rig, done
from agentdispatch.agency.realadapters import RealAdapter


def test_plan_problems():
    r = rig()
    job = dict(kind="general", target="", goal="g")
    d = r["disp"]
    assert d.plan_problems([dict(objective="summarise the notes", needs=["write"])], job) == []
    p = d.plan_problems([dict(objective="read D:\\\\projects\\\\x\\\\data.csv and sum it", needs=["write"])], job)
    assert p and "host-only" in p[0], p
    p = d.plan_problems([dict(objective="make a song", needs=["audio"])], job)                  # the music agent is disabled by default
    assert p and "no enabled, installed agent" in p[0], p
    p = d.plan_problems([dict(objective="x", needs=["telepathy"])], job)
    assert p and "use only:" in p[0] and "reasoning" in p[0], p
    tj = dict(kind="code", target="D:/proj/a", goal="g")
    assert d.plan_problems([dict(objective="edit D:/proj/a/mod.py", needs=["code"])], tj) == []
    done(r)


def test_plan_is_reasked_once_then_refused():
    ad = RealAdapter.__new__(RealAdapter)
    asked = []
    replies = ['{"steps": [{"objective": "read C:/data/x.csv", "needs": ["write"]}]}', '{"steps": [{"objective": "read data/x.csv", "needs": ["write"]}]}']
    ad._ask = lambda agent, prompt, cwd: (asked.append(prompt) or replies[len(asked) - 1], None)
    ad.plan_request = lambda job: "PLAN IT"
    ad.plan_check_fn = lambda steps, job: ["step 1 relies on host-only path(s) C:/data/x.csv"] if "C:/" in steps[0]["objective"] else []
    st = ad.plan(dict(id="a"), dict(kind="general"))
    assert st[0]["objective"] == "read data/x.csv" and len(asked) == 2 and "YOUR PLAN WAS REFUSED" in asked[1] and "reasoning" in asked[1]
    asked.clear()
    replies[1] = replies[0]
    try:
        ad.plan(dict(id="a"), dict(kind="general"))
        assert False, "should refuse"
    except RuntimeError as e:
        assert "after one re-ask" in str(e) and len(asked) == 2


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
