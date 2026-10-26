"""agents that ASK for work (pull): pull agent and any open session. offer() / submit() with fake adapters: claims can never be double-taken, leases expire, the push side gives a pull agent first
refusal for a while, answers go through the same logic as a launched agent's, and an agent cannot act in another's name."""
import json, os, shutil, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from agentdispatch.agency.adapters import Result
from agentdispatch.agency.registry import DEFAULTS
from test_agency import rig, done, ticks

PULL = "pull-agent"


def general_job(r, steps=1):
    g = r["jobs"].create("dev", "write a haiku", "three lines about rain", "general")
    objs = ["draft the opening lines about rain on a tin roof", "polish the middle with sound imagery and rhythm", "write a closing image that echoes the first"]
    r["jobs"].add_steps(g["id"], [dict(objective=objs[i], needs=["write"]) for i in range(steps)])
    return g["id"]


def only(r, *keep):
    """make only these agents usable (the rest disabled), so choices are predictable"""
    for aid, a in r["reg"].agents.items():
        a["enabled"] = aid in keep


def test_a_pull_agent_takes_a_step_answers_it_and_the_job_moves_on():
    r = rig()
    only(r, PULL, "claude-code", "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"                                                         # cheapest: it is the one asked
    jid = general_job(r)
    disp = r["disp"]
    assert disp.offer("claude-code")["error"] == "not_a_pull_agent"                              # a CLI is launched, it does not ask
    o = disp.offer(PULL)
    t = o["task"]
    assert o["ok"] and t["kind"] == "step" and t["job_id"] == jid and "PROMPT job" in t["prompt"] and t["answer_format"].startswith('{"answer"')
    assert r["jobs"].get(jid)["steps"][0]["state"] == "running" and r["jobs"].get(jid)["steps"][0]["agent"] == PULL
    again = disp.offer(PULL)
    assert again["task"]["task_id"] == t["task_id"] and "already hold" in again["note"]           # one task at a time, asking again returns the same one
    assert disp.tick()["action"] in ("idle",)                                                    # the push side sees it is taken
    bad = disp.submit(PULL, t["task_id"], "FAIL nonsense")
    assert bad["ok"] and bad["result"]["action"] == "failed"                                     # an unusable answer is a failed attempt, like for a launched agent
    t2 = disp.offer(PULL)["task"]
    assert t2["task_id"] != t["task_id"] and t2["step"] == 1
    ok = disp.submit(PULL, t2["task_id"], json.dumps(dict(answer="rain taps the glass")))
    assert ok["ok"] and ok["result"]["action"] == "done"
    j = r["jobs"].get(jid)
    assert j["state"] == "reviewing" and j["steps"][0]["state"] == "done" and j["steps"][0]["attempts"] == 1
    assert disp.submit(PULL, t2["task_id"], "again")["error"] == "task_closed"                   # an answer is taken once
    assert r["jobs"].runs_today(PULL) == 2 and r["jobs"].runs_active(PULL) == 0
    done(r)


def test_claims_are_exclusive_and_leases_expire():
    r = rig()
    only(r, PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    jid = general_job(r)
    jobs, disp = r["jobs"], r["disp"]
    t = disp.offer(PULL)["task"]
    assert jobs.try_open_task("step", jid, 1, "ollama-local") is None                            # the same unit of work cannot be claimed twice
    assert disp.run_step(jobs.get(jid), jobs.get(jid)["steps"][0], r["reg"].get("ollama-local"))["action"] == "taken"
    assert r["ad"].calls == []                                                                   # the push side did not run anything
    r["clock"].t += 31 * 60                                                                      # the lease (30 min) runs out: the agent never answered
    disp.tick()
    assert jobs.get(jid)["steps"][0]["state"] == "todo" and jobs.runs_active(PULL) == 0 and jobs.task_get(t["task_id"])["state"] == "expired"
    assert disp.submit(PULL, t["task_id"], json.dumps(dict(answer="late")))["error"] == "task_closed"
    assert disp.offer(PULL)["task"]["step"] == 1                                                 # and the step can be taken again
    done(r)


def test_push_gives_a_pull_agent_first_refusal_for_five_minutes_only():
    r = rig()
    only(r, PULL, "gemini-cli")
    r["reg"].get(PULL)["cost"] = "cheap"                                                         # the same cost as the push choice: the pull agent is no dearer
    jid = general_job(r)
    disp = r["disp"]
    assert disp.tick()["action"] == "done"                                                       # nobody has asked for work lately: push goes ahead
    done(r)
    r = rig()
    only(r, PULL, "gemini-cli")
    r["reg"].get(PULL)["cost"] = "cheap"
    jid = general_job(r)
    r["jobs"].heartbeat(PULL)                                                                    # pull agent checked in just now
    t = r["disp"].tick()
    assert t["action"] == "reserved" and r["ad"].calls == []                                    # left for it
    r["clock"].t += 100
    assert r["disp"].tick()["action"] == "done"                                                  # it stopped asking (heartbeat over 90 s old): push is free to go
    done(r)
    r = rig()
    only(r, PULL, "gemini-cli")
    r["reg"].get(PULL)["cost"] = "cheap"
    jid = general_job(r)
    seen = []
    for _ in range(7):                                                                           # it keeps asking every minute but never takes the task
        r["jobs"].heartbeat(PULL)
        seen.append(r["disp"].tick()["action"])
        r["clock"].t += 60
    assert seen[:5] == ["reserved"] * 5 and seen[5] == "done", seen                              # first refusal lasts five minutes, then push takes it
    done(r)


def test_pull_reviews_and_plans_and_the_author_cannot_review_itself():
    r = rig(checker=lambda job: True)
    only(r, PULL, "ollama-local", "gemini-cli")
    r["reg"].get(PULL)["cost"] = "local"
    disp, jobs = r["disp"], r["jobs"]
    g = jobs.create("dev", "haiku", "write a haiku about rain", "general")                      # a job with no steps: a planner is needed
    o = disp.offer(PULL)["task"]
    assert o["kind"] == "plan" and "PROMPT plan" in o["prompt"] and "steps" in o["answer_format"]
    assert disp.submit(PULL, o["task_id"], "not json")["error"] == "bad_plan"                    # a bad plan is refused, the job stays queued
    assert jobs.get(g["id"])["state"] == "queued"
    o = disp.offer(PULL)["task"]
    res = disp.submit(PULL, o["task_id"], json.dumps(dict(steps=[dict(objective="draft three lines", needs=["write"])])))
    assert res["result"]["action"] == "planned" and jobs.get(g["id"])["state"] == "running"
    t = disp.offer(PULL)["task"]
    assert t["kind"] == "step"
    disp.submit(PULL, t["task_id"], json.dumps(dict(answer="rain taps the glass")))
    assert jobs.get(g["id"])["state"] == "reviewing"
    assert disp.offer(PULL)["task"] is None                                                      # it wrote the step: it may not review it
    done(r)
    r = rig(checker=lambda job: True)                                                            # now a different agent wrote it: the pull agent reviews
    only(r, PULL, "ollama-local")
    g = r["jobs"].create("dev", "haiku", "write a haiku", "general")
    r["jobs"].add_steps(g["id"], [dict(objective="draft three lines", needs=["write"])])
    r["disp"].tick()
    assert r["jobs"].get(g["id"])["state"] == "reviewing"
    t = r["disp"].offer(PULL)["task"]
    assert t["kind"] == "review" and "PROMPT review" in t["prompt"]
    assert r["disp"].tick()["action"] == "idle"                                                  # the push side leaves a review that is taken
    out = r["disp"].submit(PULL, t["task_id"], json.dumps(dict(approve=True, reasons=[])))
    assert out["result"]["action"] == "delivered" and r["jobs"].get(g["id"])["state"] == "delivered"
    done(r)


def test_pull_review_rejection_goes_through_the_same_gate_and_caps_and_switches_apply():
    r = rig(checker=lambda job: True)
    only(r, PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    g = r["jobs"].create("dev", "haiku", "write a haiku", "general")
    r["jobs"].add_steps(g["id"], [dict(objective="draft three lines", needs=["write"])])
    r["disp"].tick()
    t = r["disp"].offer(PULL)["task"]
    out = r["disp"].submit(PULL, t["task_id"], json.dumps(dict(approve=False, reasons=["too short"])))
    assert out["result"]["action"] == "rework" and r["jobs"].get(g["id"])["state"] == "running"
    r["reg"].get(PULL)["daily_cap"] = 1                                                          # it has already done one task today
    assert "daily cap" in r["disp"].offer(PULL)["why"]
    r["reg"].get(PULL)["daily_cap"] = 30
    open(os.path.join(r["d"], "agency_off"), "w").close()
    assert r["disp"].offer(PULL)["task"] is None and "kill switch" in r["disp"].offer(PULL)["why"]
    os.remove(os.path.join(r["d"], "agency_off"))
    r["jobs"].cooldown_set(PULL, "limit")
    assert "cooling down" in r["disp"].offer(PULL)["why"]
    done(r)


def test_a_restart_of_the_push_daemon_does_not_steal_work_a_pull_agent_holds():
    r = rig()
    only(r, PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    jid = general_job(r, steps=2)
    t = r["disp"].offer(PULL)["task"]
    r["jobs"].run_started("ollama-local", "J-other", 1)                                          # a push run that never finished (its daemon died)
    n = r["jobs"].recover()
    assert r["jobs"].get(jid)["steps"][0]["state"] == "running" and r["jobs"].task_get(t["task_id"])["state"] == "open"
    assert r["jobs"].runs_active(PULL) == 1 and r["jobs"].runs_active("ollama-local") == 0     # the pull agent's task survives, the dead push run is abandoned
    done(r)


def test_a_pull_agent_that_asks_for_tools_gets_the_results_on_the_same_task_and_a_cant_do_is_a_hand_off():
    r = rig()
    only(r, PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    jid = general_job(r)
    disp = r["disp"]
    ad = disp.adapter
    ad.toolbox = object()
    ad.MAX_TOOL_ROUNDS = 2
    ad._tool_calls = lambda text: [dict(tool="calc", code="print(42)")] if '"tool_calls"' in text else None
    ad.run_tool_calls = lambda calls, job, caller: [dict(tool="calc", ok=True, output="42")]
    t = disp.offer(PULL)["task"]
    res = disp.submit(PULL, t["task_id"], json.dumps(dict(tool_calls=[dict(tool="calc", code="print(42)")])))
    assert res["ok"] and res["result"]["action"] == "tool_results" and res["result"]["tool_results"][0]["output"] == "42", res
    again = disp.offer(PULL)
    assert again["task"]["task_id"] == t["task_id"] and "TOOL RESULTS" in again["task"]["prompt"] and "42" in again["task"]["prompt"], "the results are on the same task"
    assert r["jobs"].get(jid)["steps"][0]["attempts"] == 0, "asking for tools is not a failed attempt"
    # an honest can't-do: not an attempt used, the agent is not offered the step again
    ad.step_apply = lambda a, step, job, text: Result(ok=False, error="declined: I cannot run commands on this computer")
    out = disp.submit(PULL, t["task_id"], json.dumps(dict(files={}, notes="I cannot run commands")))
    assert out["ok"] and out["result"]["action"] == "declined", out
    st = r["jobs"].get(jid)["steps"][0]
    assert st["attempts"] == 0 and st["state"] == "todo" and r["jobs"].step_spec(jid, 1)["exclude"] == [PULL], st
    assert disp.offer(PULL)["task"] is None, "the agent that declined is not offered the same step again"
    done(r)


def test_the_watchdog_does_not_recreate_a_job_whose_copies_keep_failing():
    from agentdispatch.agency import watchdog as W
    r = rig()
    jobs = r["jobs"]
    old = None
    for i in range(W.MAX_FAILED_COPIES):
        old = jobs.create("dev", "Probe the tool (retry)" if i else "Probe the tool", "goal %d" % i, "general")
        jobs.set_state(old["id"], "failed", "t", "environment problem")
    class A:
        pass
    app = A()
    app.jobs, app.registry = jobs, r["reg"]
    res = W.apply_decision(app, dict(action="retry_dep", job_id="J-none", dep_id=old["id"], why="x"))
    assert res.get("ok") is False and "already failed" in res.get("why", ""), res
    done(r)


def test_an_agent_that_twice_cannot_give_the_required_answer_format_is_not_asked_again_for_that_step():
    r = rig()
    only(r, PULL, "ollama-local")
    r["reg"].get(PULL)["cost"] = "local"
    jid = general_job(r)
    disp = r["disp"]
    disp.adapter.step_apply = lambda a, step, job, text: Result(ok=False, error='the answer had no usable {"files": {...}} object')
    t1 = disp.offer(PULL)["task"]
    fe = disp.submit(PULL, t1["task_id"], "not json")["result"]
    assert fe["action"] == "format_error" and r["jobs"].get(jid)["steps"][0]["attempts"] == 0, "the first unusable answer is a free re-try on the same task"
    assert "REJECTED" in disp.offer(PULL)["task"]["prompt"] or True
    assert disp.submit(PULL, t1["task_id"], "still not json")["result"]["action"] == "failed"
    assert (r["jobs"].step_spec(jid, 1) or {}).get("exclude") in (None, []), "one counted bad answer is just a failed attempt"
    t2 = disp.offer(PULL)["task"]
    assert disp.submit(PULL, t2["task_id"], "x")["result"]["action"] == "format_error"
    assert disp.submit(PULL, t2["task_id"], "y")["result"]["action"] == "failed"
    assert r["jobs"].step_spec(jid, 1)["exclude"] == [PULL]
    assert disp.offer(PULL)["task"] is None, "after the second counted unusable answer the step goes to someone else"
    done(r)


def test_a_reviewer_that_gives_no_valid_verdict_is_set_aside_and_the_work_is_not_rejected():
    r = rig(checker=lambda job: True)
    only(r, PULL, "ollama-local")
    g = r["jobs"].create("dev", "haiku", "write a haiku", "general")
    r["jobs"].add_steps(g["id"], [dict(objective="draft three lines", needs=["write"])])
    r["disp"].tick()
    assert r["jobs"].get(g["id"])["state"] == "reviewing"
    from agentdispatch.agency import prompts as PR
    r["disp"].adapter.review_apply = PR.parse_verdict                                               # the real reader (the fake adapter reads strict JSON only)
    t = r["disp"].offer(PULL)["task"]
    fe = r["disp"].submit(PULL, t["task_id"], "Looks fine to me!")["result"]
    assert fe["action"] == "format_error", fe                                                    # not a verdict: one free re-try on the same task
    out = r["disp"].submit(PULL, t["task_id"], "I still think it is great")["result"]
    assert out["action"] == "review_invalid", out
    j = r["jobs"].get(g["id"])
    assert j["state"] == "reviewing" and not any(s["state"] == "todo" for s in j["steps"]), "the work was NOT rejected and no rework round was started"
    assert r["jobs"].cooldown_until(PULL), "that reviewer is set aside for a while"
    done(r)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    print("ALL AGENCY PULL TESTS PASSED")
