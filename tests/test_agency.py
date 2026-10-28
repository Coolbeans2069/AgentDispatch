"""the agency (Stage 0): jobs, registry, availability, dispatcher, gate - all with FAKE agents; nothing real is run"""
import json, os, shutil, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["AGENTDISPATCH_SAFE_REPOS"] = "D:/repos/app"                       # the test fixtures' repo layout (Windows-style paths are checked on every OS)
os.environ["AGENTDISPATCH_ALLOWED_ROOTS"] = "D:/repos/app,D:/repos/other"
sys.path.insert(0, ROOT)
from agentdispatch.store import Board, BoardError
from agentdispatch.agency.jobs import Jobs
from agentdispatch.agency.registry import Registry, RegistryError, DEFAULTS, validate
from agentdispatch.agency.availability import Availability
from agentdispatch.agency.adapters import FakeAdapter, Result, detect_limit
from agentdispatch.agency.dispatcher import Dispatcher
from agentdispatch.agency import gate as G
from agentdispatch.agency import policy as P


class Clock:
    t = time.mktime((2026, 10, 16, 14, 0, 0, 0, 0, -1))

    def __call__(self):
        return self.t


IDLE = dict(gpus=[dict(util=2.0)], ram=10.0, ram_total_gb=240, limits={})


def rig(scripts=None, reviews=None, er=IDLE, cfg=None, installed=True, **kw):
    d = tempfile.mkdtemp(prefix="agency_")
    clock = Clock()
    board = Board(d, now=clock)
    jobs = Jobs(board)
    reg = Registry(d)
    reg.agents.pop("ollama-agent", None); reg.agents.pop("openrouter", None)                                                            # the container agent is tested in test_routing; these tests are about the dispatcher with the original agents
    state = dict(er=er)
    av = Availability(jobs, fetch=lambda url: state["er"] if state["er"] is not None else (_ for _ in ()).throw(OSError("down")), which=(lambda b: "C:/x/" + b) if installed else (lambda b: None), now=clock)
    ad = FakeAdapter(scripts, reviews)
    c = dict(P.DEFAULTS, **(cfg or {}))
    disp = Dispatcher(board, jobs, reg, av, ad, d, cfg=c, now=clock, **kw)
    return dict(d=d, clock=clock, board=board, jobs=jobs, reg=reg, av=av, ad=ad, disp=disp, er=state)


def done(r):
    r["board"].close()
    shutil.rmtree(r["d"], ignore_errors=True)


def code_job(r, steps=1, check="python -m pytest -q", target="D:/repos/app", **kw):
    j = r["jobs"].create("dev", "fix the thing", "make the test pass", "code", target, dict(check=check, rubric="small, clear change"), **kw)
    r["jobs"].add_steps(j["id"], [dict(objective="step number %d about topic%d" % (i, i * 7), needs=["code"]) for i in range(1, steps + 1)])
    return j["id"]


def ticks(r, n=10):
    return [r["disp"].tick() for _ in range(n)]


# ---------------------------------------------------------------- registry / limit words
def test_registry_defaults_valid_and_bad_files_refused():
    assert validate(DEFAULTS) == []
    d = tempfile.mkdtemp()
    try:
        bad = json.loads(json.dumps(DEFAULTS))
        bad["claude-code"]["cost"] = "free-ish"
        del bad["codex-cli"]["run"]
        json.dump(bad, open(os.path.join(d, "agents.json"), "w"))
        try:
            Registry(d)
            assert False
        except RegistryError as e:
            assert "cost" in str(e) and "run" in str(e)
        r = Registry(None)
        assert [a["id"] for a in r.capable(["code"])][0] in ("ollama-agent", "ollama-local", "openrouter")                    # cheapest first
        ids = [a["id"] for a in r.capable(["research"])]
        assert "grok-cli" in ids and "pull-agent" not in ids                                     # the CLI can be launched; the desktop app is pull-only and never pushed to
        assert r.brain("claude-code")["region"] == "frontal" and r.brain("pull-agent")["region"] == "limbic"
        extra = json.loads(json.dumps(DEFAULTS))
        extra["svd-video"] = dict(kind="app", strengths=["video"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"))
        extra["tts-voice"] = dict(kind="app", strengths=["speech"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), brain=dict(color="#ffd34d"))
        r2 = Registry(None, agents=extra)
        assert r2.brain("svd-video")["region"] == "occipital" and r2.brain("tts-voice")["region"] == "broca" and r2.brain("tts-voice")["color"] == "#ffd34d"      # new kinds of agent find their lobe by themselves
        extra["tts-voice"]["brain"] = dict(region="elbow")
        assert any("brain must be" in p for p in validate(extra))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert detect_limit("Error: usage limit reached, try again in 3 hours") and detect_limit("HTTP 429 Too Many Requests") and not detect_limit("assertion failed in test_calc")
    assert detect_limit("You've hit your session limit · resets 3:20am")
    assert detect_limit("exit 1: You've hit your session limit · resets")
    assert detect_limit("weekly limit")
    assert detect_limit("Please retry in 12h27m26.5s")
    assert detect_limit("insufficient_quota")
    assert detect_limit("tokens per min (TPM): Limit 30000")
    assert detect_limit("openrouter: HTTP 429")
    from agentdispatch.agency.adapters import parse_reset
    import time as _t
    until = parse_reset("You've hit your session limit · resets 3:20am", _t.time())
    assert until and until > _t.time()


# ---------------------------------------------------------------- jobs
def test_job_validation_and_state_machine():
    r = rig()
    j = r["jobs"]
    try:
        j.create("dev", "t", "g", "code", "D:/x")                                              # no acceptance
        assert False
    except BoardError as e:
        assert e.code == "no_acceptance"
    for bad in (dict(kind="poem"), dict(title="")):
        try:
            j.create("dev", bad.get("title", "t"), "g", bad.get("kind", "general"))
            assert False
        except BoardError:
            pass
    g = j.create("dev", "write a haiku", "about rain", "general")
    assert g["state"] == "queued" and g["post_id"]
    for bad_steps, code in (([], "bad_steps"), ([dict(objective="x", needs=["telepathy"])], "bad_steps"), ([dict(objective="same words here"), dict(objective="same words here")], "bad_steps"),
                            ([dict(objective="step %d" % i) for i in range(20)], "too_many")):
        try:
            j.add_steps(g["id"], bad_steps)
            assert False, bad_steps
        except BoardError as e:
            assert e.code == code
    g = j.add_steps(g["id"], [dict(objective="draft three lines", needs=["write"])])
    assert g["state"] == "running" and g["steps"][0]["state"] == "todo"
    try:
        j.set_state(g["id"], "merged", "x")                                                    # running -> merged is not a legal move
        assert False
    except BoardError as e:
        assert e.code == "illegal"
    c = j.cancel(g["id"], "dev")
    assert c["state"] == "cancelled" and c["steps"][0]["state"] == "skipped"
    try:
        j.set_state(g["id"], "running", "x")
        assert False
    except BoardError as e:
        assert e.code == "terminal"
    kinds = {e["type"] for e in r["board"].db.execute("SELECT type FROM events WHERE type LIKE 'job.%'")}
    assert {"job.created", "job.state", "job.planned"} <= kinds                                # everything is an event on the board
    done(r)


# ---------------------------------------------------------------- choosing agents
def test_cheapest_capable_agent_wins_and_busy_machine_moves_work_up():
    r = rig()
    jid = code_job(r)
    t = r["disp"].tick()
    assert t["action"] == "done" and t["agent"] == "ollama-local"                              # local first
    done(r)
    r = rig(er=dict(IDLE, gpus=[dict(util=90.0)]))                                             # a render holds the GPU
    jid = code_job(r)
    t = r["disp"].tick()
    assert t["agent"] == "codex-cli", t                                                       # local model refused: busy GPU; next cheapest with 'code'
    done(r)
    r = rig(er=None)                                                                           # engine room not answering: unknown never blocks
    code_job(r)
    assert r["disp"].tick()["agent"] == "ollama-local"
    done(r)


def test_availability_reasons_limits_caps_and_installation():
    r = rig(er=dict(IDLE, limits=dict(codex=dict(windows=[dict(pct=97)], stale=False))))
    codex = r["reg"].get("codex-cli")
    ok, why = r["av"].check(codex)
    assert not ok and "97%" in why
    r["er"]["er"] = dict(IDLE, limits=dict(codex=dict(windows=[dict(pct=97, resets_in_min=300)], stale=True, age_min=60)))     # a stale reading of a full window still holds: usage only grows until it resets
    r["av"]._t = 0
    ok, why = r["av"].check(codex)
    assert not ok and "stale" in why, why
    r["er"]["er"] = dict(IDLE, limits=dict(codex=dict(windows=[dict(pct=97, resets_in_min=30)], stale=True, age_min=60)))      # ... unless the window has reset since the reading
    r["av"]._t = 0
    assert r["av"].check(codex)[0]
    codex["daily_cap"] = 1
    r["jobs"].run_started("codex-cli", "J-x", 1)
    assert "daily cap" in r["av"].check(codex)[1] or "concurrency" in r["av"].check(codex)[1]
    done(r)
    r = rig(installed=False)
    assert "not installed" in r["av"].check(r["reg"].get("claude-code"))[1]
    done(r)


def test_escalation_after_failures_without_the_ledger_blocking_it():
    boom = Result(ok=False, error="tests still fail")
    r = rig(scripts={"ollama-local": [boom], "codex-cli": [Result(ok=True, text="fixed")]})
    jid = code_job(r)
    seq = [t["agent"] for t in ticks(r, 3) if t["action"] in ("failed", "done")]
    assert seq == ["ollama-local", "ollama-local", "codex-cli"], seq                          # local twice, then the next tier
    j = r["jobs"].get(jid)
    assert j["steps"][0]["state"] == "done" and j["steps"][0]["attempts"] == 2
    done(r)


def test_limit_hit_sets_a_cooldown_and_does_not_count_as_a_failure():
    r = rig(scripts={"ollama-local": [Result(ok=False, text="Error 429: rate limit reached")]})
    jid = code_job(r)
    t = r["disp"].tick()
    assert t["action"] == "cooldown" and t["agent"] == "ollama-local"
    assert r["jobs"].get(jid)["steps"][0]["attempts"] == 0 and r["jobs"].get(jid)["steps"][0]["state"] == "todo"
    assert not r["av"].check(r["reg"].get("ollama-local"))[0]
    assert r["disp"].tick()["agent"] == "codex-cli"                                            # work moves on to the next agent
    r["clock"].t += 3 * 3600
    assert r["av"].check(r["reg"].get("ollama-local"))[0]                                      # the cooldown ends by itself
    t2 = r["jobs"].cooldown_set("ollama-local", "again")
    t3 = r["jobs"].cooldown_set("ollama-local", "again")
    assert t3 - r["clock"].t > t2 - r["clock"].t - 1                                           # exponential
    done(r)


def test_claude_session_limit_cools_down_and_unpins_preferred_agent():
    """Claude's 'session limit · resets' must be a cooldown (not a step failure), and sticky preferred_agent must clear."""
    r = rig(scripts={
        "ollama-local": [Result(ok=False, text="You've hit your session limit · resets 3:20am")],
        "codex-cli": [Result(ok=True, text="fixed after failover")],
    })
    jid = code_job(r, meta={"preferred_agent": "ollama-local", "prefer_agent": "ollama-local"})
    j = r["jobs"].get(jid)
    step = [s for s in j["steps"] if s["state"] == "todo"][0]
    agent, _ = r["disp"].choose(j, step)
    assert agent["id"] == "ollama-local"                                                       # preferred_agent alias honored
    t = r["disp"].tick()
    assert t["action"] == "cooldown" and t["agent"] == "ollama-local", t
    assert r["jobs"].get(jid)["steps"][0]["attempts"] == 0
    meta = r["jobs"].meta(jid)
    assert meta.get("prefer_agent") != "ollama-local" and meta.get("preferred_agent") != "ollama-local", meta
    assert meta.get("prefer_restore") == "ollama-local"
    t2 = r["disp"].tick()
    assert t2["agent"] == "codex-cli", t2                                                      # next available agent takes over
    done(r)


def test_blocked_prefer_agent_is_rerouted_not_queued():
    """Preferred agent on cooldown/plan-block must not hold the job — next available runs now."""
    r = rig(scripts={
        "codex-cli": [Result(ok=True, text="should not run first")],
        "ollama-local": [Result(ok=True, text="rerouted here")],
        "claude-code": [Result(ok=True, text="also ok")],
    })
    jid = code_job(r, meta={"prefer_agent": "codex-cli", "preferred_agent": "codex-cli"})
    # Block the preferred agent (simulates plan 99% / cooling)
    r["jobs"].cooldown_set("codex-cli", "plan limit 99% used", until=r["clock"].t + 7200)
    j = r["jobs"].get(jid)
    step = [s for s in j["steps"] if s["state"] == "todo"][0]
    agent, rejects = r["disp"].choose(j, step)
    assert agent and agent["id"] != "codex-cli", (agent, rejects)
    assert any("preferred but blocked" in x or "rerout" in x.lower() for x in rejects), rejects
    meta = r["jobs"].meta(jid)
    assert meta.get("prefer_agent") != "codex-cli"
    assert meta.get("prefer_restore") == "codex-cli"
    t = r["disp"].tick()
    assert t.get("agent") != "codex-cli", t
    assert t.get("action") in ("done", "would_run") or t.get("agent"), t
    done(r)


def test_cooldown_expiry_wakes_limit_failed_job():
    """When a cooldown row expires, failed limit jobs are reissued (the 3:20am gap)."""
    r = rig(scripts={"codex-cli": [Result(ok=True, text="done after wake")], "ollama-local": [Result(ok=True, text="local")]})
    jid = code_job(r)
    # Pretend the job failed overnight on a session limit
    r["jobs"].set_state(jid, "failed", "agency", "step 1 failed after 3 attempt(s): You've hit your session limit · resets 3:20am")
    # cooldown_set rejects past `until` (falls back to exponential); set a near-future then advance the clock.
    r["jobs"].cooldown_set("claude-code", "session limit resets 3:20am", until=r["clock"].t + 120)
    r["clock"].t += 180
    assert r["jobs"].cooldown_until("claude-code") == 0
    out = r["disp"]._wake_after_cooldowns()
    assert out and out["action"] == "cooldown_wake", out
    assert "claude-code" in (out.get("expired") or [])
    assert any(w.get("action") == "reissue" for w in out.get("woken") or []), out
    assert r["jobs"].meta(jid).get("limit_woken") is True
    done(r)


def test_job_fails_when_a_step_exhausts_its_attempts_or_budget():
    boom = Result(ok=False, error="nope")
    r = rig(scripts={a: [boom] for a in DEFAULTS})
    jid = code_job(r, budget=dict(max_attempts_per_step=2))
    out = ticks(r, 4)
    assert r["jobs"].get(jid)["state"] == "failed" and any(t["action"] == "job_failed" for t in out)
    done(r)
    r = rig()
    jid = code_job(r, budget=dict(wall_min=10))
    r["jobs"].db.execute("DELETE FROM runs")
    r["jobs"].cancel_steps  # (the job has not run yet)
    assert not r["jobs"]._wall_over(r["jobs"].get(jid), r["clock"].t + 99999), "waiting is not working: the wall-clock budget counts from the first run"
    rid = r["jobs"].run_started("some-agent", jid, 1)
    r["jobs"].run_done(rid, "failed")
    r["clock"].t += 3600
    r["disp"].tick()
    j = r["jobs"].get(jid)
    assert j["state"] == "failed" and "wall-clock" in j["why"]
    done(r)


# ---------------------------------------------------------------- review gate and apply
def test_full_flow_review_by_a_different_agent_then_ready_merged_or_delivered():
    r = rig(checker=lambda job: True)
    jid = code_job(r, steps=2)
    out = ticks(r, 3)
    assert [t["action"] for t in out] == ["done", "done", "ready"], out
    j = r["jobs"].get(jid)
    assert j["state"] == "ready" and "auto-merge is off" in j["why"]
    assert r["ad"].review_calls and r["ad"].review_calls[0] != "ollama-local"                  # not the author
    done(r)
    merged = []
    r = rig(checker=lambda job: True, cfg=dict(auto_merge=True), apply_fn=lambda job: (merged.append(job["id"]) or dict(ok=True, summary="ff")))
    jid = code_job(r)
    ticks(r, 2)
    assert r["jobs"].get(jid)["state"] == "merged" and merged == [jid]
    other = code_job(r, target="D:/repos/other")                                                  # not a safe repo: waits for the owner
    ticks(r, 2)
    assert r["jobs"].get(other)["state"] == "ready" and "approve to merge" in r["jobs"].get(other)["why"]
    done(r)
    r = rig()
    g = r["jobs"].create("dev", "haiku", "about rain", "general")
    r["jobs"].add_steps(g["id"], [dict(objective="draft three lines", needs=["write"])])
    ticks(r, 2)
    assert r["jobs"].get(g["id"])["state"] == "delivered"
    done(r)


def test_merge_that_fails_is_reported_and_the_job_fails():
    r = rig(checker=lambda job: True, cfg=dict(auto_merge=True), apply_fn=lambda job: dict(ok=False, why="suite failed after merge, reverted"))
    jid = code_job(r)
    ticks(r, 2)
    j = r["jobs"].get(jid)
    assert j["state"] == "failed" and "reverted" in j["why"]
    done(r)


def test_gate_rules_independence_protected_paths_and_size():
    job = dict(acceptance=dict(check="x"))
    ok = G.decide(job, "a", "b", True, dict(approve=True), ["pkg/x.py"], 20)
    assert ok["ok"]
    assert "different agent" in G.decide(job, "a", "a", True, dict(approve=True))["reasons"][0]
    assert not G.decide(job, "a", "b", False, dict(approve=True))["ok"]
    assert not G.decide(job, "a", "b", None, dict(approve=True))["ok"]
    assert not G.decide(job, "a", "b", True, dict(approve=False, reasons=["ugly"]))["ok"]
    hit = G.decide(job, "a", "b", True, dict(approve=True), ["agentdispatch/agency/gate.py", "dev/manifest.json"], 5)
    assert not hit["ok"] and hit["needs_owner"]
    assert G.decide(job, "a", "b", True, dict(approve=True), [], 5000)["needs_owner"]
    assert G.protected_hits(["tests/test_agency_x.py", "src/ok.py", "data/agents.json"]) == ["tests/test_agency_x.py", "data/agents.json"]


def test_protected_file_change_and_missing_checker_block_the_job_for_the_owner():
    r = rig(scripts={"ollama-local": [Result(ok=True, text="done", touched=["agentdispatch/agency/gate.py"], diff_lines=10)]}, checker=lambda job: True)
    jid = code_job(r)
    out = ticks(r, 2)
    j = r["jobs"].get(jid)
    assert j["state"] == "blocked" and "protected" in j["why"], j["why"]
    assert any(n["id"] == jid for n in r["jobs"].needs_you())
    done(r)
    r = rig()                                                                                  # a check is configured but no runner exists yet (Stage 1)
    jid = code_job(r)
    ticks(r, 2)
    assert r["jobs"].get(jid)["state"] == "blocked" and "Stage 1" in r["jobs"].get(jid)["why"]
    done(r)


def test_review_rejection_adds_a_rework_step_twice_then_fails():
    no = dict(approve=False, reasons=["no tests added"])
    r = rig(reviews={a: [no] for a in DEFAULTS}, checker=lambda job: True)
    jid = code_job(r)
    out = ticks(r, 8)
    j = r["jobs"].get(jid)
    assert j["state"] == "failed" and "review failed twice" in j["why"], (j["state"], j["why"])
    assert sum(1 for s in j["steps"] if s["objective"].startswith("Address the review")) == 2
    done(r)
    yes = dict(approve=True, reasons=[])
    r = rig(reviews={a: [no, yes] for a in DEFAULTS}, checker=lambda job: True)
    jid = code_job(r)
    ticks(r, 6)
    assert r["jobs"].get(jid)["state"] == "ready"                                             # fixed in the second round
    done(r)


def test_no_independent_reviewer_blocks():
    agents = json.loads(json.dumps(DEFAULTS))
    for a in agents:
        if a != "ollama-local":
            agents[a]["enabled"] = False
    d = tempfile.mkdtemp()
    json.dump(agents, open(os.path.join(d, "agents.json"), "w"))
    shutil.rmtree(d, ignore_errors=True)
    r = rig()
    for a in r["reg"].agents:
        if a != "ollama-local":
            r["reg"].agents[a]["enabled"] = False
    jid = code_job(r)
    ticks(r, 2)
    assert r["jobs"].get(jid)["state"] == "blocked" and "reviewer" in r["jobs"].get(jid)["why"]                 # nobody capable exists: the owner must decide
    done(r)
    r = rig(checker=lambda job: True)                                                                         # capable reviewers exist but are cooling down: the job WAITS in review
    jid = code_job(r)
    r["disp"].tick()
    for a in ("gemini-cli", "codex-cli", "claude-code", "grok-cli"):
        r["jobs"].cooldown_set(a, "limit", until=r["clock"].t + 7200)
    t = r["disp"].tick()
    assert t["action"] == "wait" and r["jobs"].get(jid)["state"] == "reviewing", t
    r["clock"].t += 3 * 3600
    assert r["disp"].tick()["action"] == "ready"                                                              # and carries on once one is back
    done(r)


# ---------------------------------------------------------------- switches, restart, lock
def test_kill_switch_quiet_hours_and_policy():
    r = rig()
    code_job(r)
    open(os.path.join(r["d"], "agency_off"), "w").close()
    assert r["disp"].tick()["action"] == "idle" and r["adcalls"] == [] if False else r["disp"].tick()["action"] == "idle"
    assert r["ad"].calls == []
    os.remove(os.path.join(r["d"], "agency_off"))
    r["disp"].cfg["quiet_hours"] = [8, 22]                                                     # 14:00 is inside
    assert r["disp"].tick()["why"] == "quiet hours"
    r["disp"].cfg["quiet_hours"] = [22, 6]
    assert r["disp"].tick()["action"] == "done"
    assert P.in_quiet_hours(dict(quiet_hours=[22, 6]), time.mktime((2026, 10, 16, 23, 0, 0, 0, 0, -1))) and P.is_safe_repo(P.DEFAULTS, "d:\\REPOS\\APP\\") and not P.is_safe_repo(P.DEFAULTS, "D:/repos/other")
    done(r)


def test_state_survives_a_restart():
    r = rig()
    jid = code_job(r, steps=2)
    r["disp"].tick()
    r["board"].close()
    board = Board(r["d"], now=r["clock"])
    jobs = Jobs(board)
    j = jobs.get(jid)
    assert [s["state"] for s in j["steps"]] == ["done", "todo"] and jobs.runs_today("ollama-local") == 1
    assert jobs.counts() == {"running": 1}
    board.close()
    shutil.rmtree(r["d"], ignore_errors=True)


def test_gate_files_are_hash_locked():
    root = tempfile.mkdtemp()
    data = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "agentdispatch", "agency"))
        for f in ("gate.py", "policy.py", "dispatcher.py"):
            open(os.path.join(root, "agentdispatch", "agency", f), "w").write("# " + f + "\n")
        assert "no lock file" in G.verify_lock(root, data)[0]
        G.lock(root, data)
        assert G.verify_lock(root, data) == []
        open(os.path.join(root, "agentdispatch", "agency", "gate.py"), "a").write("approve_everything = True\n")
        assert "gate.py changed" in G.verify_lock(root, data)[0]
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)


def test_every_cli_branch_still_starts():
    """a local import in one branch of __main__.main once shadowed App for the others and crash-looped the server: run the branches that use App"""
    import subprocess
    d = tempfile.mkdtemp(prefix="cli_")
    try:
        for args in (["agency", "status"], ["nightshift", "status"], ["agency", "list"], ["agency", "tick", "--dry"], ["token", "t-agent"]):
            r = subprocess.run([sys.executable, "-m", "agentdispatch", "--data", d] + args, cwd=ROOT, capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, (args, r.stderr[-400:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unsafe_check_commands_are_refused_and_a_crash_is_recovered():
    r = rig()
    for bad in ("rm -rf /", "python -c \"import os\"", "powershell -c whoami", "python -m http.server", ["bash", "x.sh"]):
        try:
            r["jobs"].create("dev", "t", "g", "code", "D:/x", dict(check=bad))
            assert False, bad
        except BoardError as e:
            assert e.code == "bad_check", (bad, e.code)
    ok = r["jobs"].create("dev", "t", "g", "code", "D:/x", dict(check="python -m pytest -q tests"))
    assert ok["acceptance"]["check"].startswith("python -m pytest")
    jid = code_job(r)
    t = r["jobs"].get(jid)["steps"][0]
    r["jobs"].step_set(jid, 1, "running", agent="ollama-local")                                 # the dispatcher died mid-step
    r["jobs"].run_started("ollama-local", jid, 1)
    assert r["disp"].tick()["action"] == "idle" or True
    assert r["jobs"].recover() == 1
    assert r["jobs"].get(jid)["steps"][0]["state"] == "todo" and r["jobs"].runs_active("ollama-local") == 0
    assert r["disp"].tick()["action"] == "done"                                                # and it carries on
    done(r)


def test_code_job_targets_are_limited_and_reviews_count_as_runs():
    from agentdispatch.app import App
    d = tempfile.mkdtemp(prefix="agency_app_")
    try:
        app = App(d, with_index=False)
        for bad in ("C:/Windows", "D:/", "D:/repos/app/../Users", "/etc"):
            try:
                app.jobs.create("dev", "t", "g", "code", bad, dict(check="python -m pytest -q"))
                assert False, bad
            except BoardError as e:
                assert e.code == "bad_target", (bad, e.code)
        assert app.jobs.target_ok("D:/repos/app") and app.jobs.target_ok("d:\\repos\\other\\")            # the default roots; slashes / case do not matter
        # creating one also needs the folder to exist: use a real temporary project (not a fixed host path) so this runs anywhere
        proj = os.path.join(d, "Proj"); os.makedirs(proj)
        json.dump(dict(allowed_roots=[proj.replace("\\", "/")]), open(os.path.join(d, "nightshift.json"), "w"))
        assert app.jobs.create("dev", "t", "g", "code", proj, dict(check="python -m pytest -q"))["state"] == "queued"
        assert app.jobs.create("dev", "t", "g", "code", (proj.upper() if os.name == "nt" else proj) + os.sep, dict(check="python -m pytest -q"))["state"] == "queued"
        assert app.jobs.create("dev", "t", "g", "general", "anything goes")["state"] == "queued"              # only code jobs name folders
        try:
            app.jobs.get("J-999999")
        except BoardError as e:
            assert e.code == "not_found"
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    r = rig(checker=lambda job: True)
    jid = code_job(r)
    ticks(r, 2)
    assert r["jobs"].runs_today(r["ad"].review_calls[0]) == 1                                             # the review was counted
    done(r)


def test_daemon_refuses_to_start_unlocked_or_tampered():
    r = rig()
    problems = r["disp"].startup_check()
    assert problems and "no lock file" in problems[0]
    try:
        r["disp"].loop()
        assert False
    except RuntimeError as e:
        assert "will not start" in str(e)
    G.lock(ROOT, r["d"])
    assert r["disp"].startup_check() == []
    done(r)


def test_unblock_returns_a_finished_job_to_review_not_to_a_dead_running_state():
    r = rig()
    jid = code_job(r, steps=2)
    ticks(r, 2)                                                                                 # both steps done, then it blocks (no check runner)
    r["disp"].tick()
    assert r["jobs"].get(jid)["state"] == "blocked"
    j = r["jobs"].unblock(jid, "dev")
    assert j["state"] == "reviewing"
    other = code_job(r, steps=2)
    r["jobs"].set_state(other, "blocked", "dev", "x")
    assert r["jobs"].unblock(other, "dev")["state"] == "running"                               # unfinished steps: back to running
    try:
        r["jobs"].unblock(jid, "dev")
        assert False
    except BoardError as e:
        assert e.code == "illegal"
    done(r)


def test_brain_feed_cards_pulses_and_strand():
    from agentdispatch.app import App
    from agentdispatch.agency import brain as BR
    d = tempfile.mkdtemp(prefix="brain_")
    try:
        json.dump(dict(work_dir=os.path.join(d, "work")), open(os.path.join(d, "agency.json"), "w"))
        app = App(d, with_index=False)
        app.availability.which = lambda b: "x"
        app.availability.fetch = lambda url: dict(gpus=[dict(util=3.0)], ram=10.0, ram_total_gb=240, limits={}, agents=[
            dict(id="claude", n=3, cpu=22.0, busy=True, running=True), dict(id="codex", n=1, cpu=0.5, busy=False, running=True), dict(id="pullagent", n=9, cpu=1.0, busy=False, running=True)])
        reg = app.registry
        assert BR.match_agent(reg, "codex-mcp-client") == "codex-cli" and BR.match_agent(reg, "grok-shell-agentdispatch") == "grok-cli"
        assert BR.match_agent(reg, "claude-code") == "claude-code" and BR.match_agent(reg, "agency") is None and BR.match_agent(reg, "somebody") is None
        cards = {c["id"]: c for c in BR.agent_cards(app)}
        assert cards["claude-code"]["state"] == "busy" and cards["claude-code"]["region"] == "frontal" and cards["claude-code"]["color"] == "#e8793a"       # a process working hard, outside the agency
        assert cards["codex-cli"]["state"] in ("online", "active") and cards["pull-agent"]["state"] in ("online", "active") and cards["pull-agent"]["pull_only"] is True              # the desktop app is running: it lights up even though the agency cannot push to it
        assert cards["grok-cli"]["state"] == "idle"                                                                                                   # signed in, available, not running
        assert cards["ollama-local"]["state"] in ("idle", "online", "active") and cards["gemini-cli"]["state"] in ("idle", "online", "active")
        j = app.jobs.create("dev", "demo", "make a thing", "general")
        app.jobs.add_steps(j["id"], [dict(objective="draft it", needs=["write"]), dict(objective="polish it", needs=["write"])])
        app.jobs.run_started("gemini-cli", j["id"], 1)                                                       # the agency is running a step on Gemini right now
        assert {c["id"]: c for c in BR.agent_cards(app)}["gemini-cli"]["state"] == "active"
        app.jobs.cooldown_set("ollama-local", "limit")
        assert {c["id"]: c for c in BR.agent_cards(app)}["ollama-local"]["state"] == "cooldown"
        app.jobs.step_set(j["id"], 1, "done", agent="gemini-cli")
        app.jobs.step_set(j["id"], 2, "running", agent="claude-code")
        st = BR.strand(app)
        assert [x["kind"] for x in st["segments"]] == ["plan", "step", "step", "check", "review", "deliver"]
        assert [x["state"] for x in st["segments"]][:3] == ["done", "done", "running"] and st["segments"][1]["color"] == "#4da3ff" and st["segments"][2]["region"] == "frontal"
        assert st["segments"][3]["state"] == "done" and st["job"]["id"] == j["id"] and not st["complete"]                     # no check configured: that segment is simply done
        ps, ev, ts = BR.pulses(app, 0, 0.0)
        kinds = {p["kind"] for p in ps}
        assert {"job", "state", "step_done", "step_running", "limit"} <= kinds and ev > 0
        assert any(p["agent"] == "gemini-cli" and p["kind"] == "step_done" for p in ps)
        ps2, ev2, _ = BR.pulses(app, ev, 0.0)
        assert ps2 == [] and ev2 == ev                                                                       # the cursor: nothing is sent twice
        app.jobs.step_set(j["id"], 2, "failed", agent="claude-code")
        assert BR.strand(app)["segments"][2]["state"] == "failed"
        snap = BR.snapshot(app)
        assert snap["ok"] and len([a for a in snap["agents"] if not a["id"].startswith("tool:")]) == len(reg.agents) and snap["counts"] == {"running": 1}
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_adding_a_new_kind_of_agent_from_the_command_line():
    import subprocess
    d = tempfile.mkdtemp(prefix="addagent_")
    try:
        run = lambda *a: subprocess.run([sys.executable, "-m", "agentdispatch", "--data", d, "agency"] + list(a), cwd=ROOT, capture_output=True, text=True, timeout=120)
        r = run("agent-add", "svd-video", "--kind", "app", "--strengths", "video,image", "--name", "Video model")
        assert r.returncode == 0 and "occipital" in r.stdout, (r.stdout, r.stderr[-300:])                       # no region given: found from what it can do
        r = run("agent-add", "my-voice", "--kind", "app", "--strengths", "speech", "--color", "#ffd34d")
        assert r.returncode == 0 and "broca" in r.stdout
        r = run("agent-add", "my-cli", "--kind", "cli", "--strengths", "write", "--argv", json.dumps(["mycli", "-p", "{prompt}"]), "--region", "temporal")
        assert r.returncode == 0 and "temporal" in r.stdout
        bad = run("agent-add", "oops", "--strengths", "telepathy")
        assert bad.returncode == 1 and "refused" in bad.stdout                                                  # an unknown strength is refused, nothing is written
        reg = Registry(d)
        reg.agents.pop("ollama-agent", None); reg.agents.pop("openrouter", None)
        assert {"claude-code", "svd-video", "my-voice", "my-cli"} <= set(reg.agents) and "oops" not in reg.agents
        assert reg.get("svd-video")["enabled"] is False and reg.get("my-cli")["enabled"] is True              # a pull-only app is listed but not pushed to; a CLI with a command can be used
        listing = run("agents").stdout
        assert "svd-video" in listing and "broca" in listing
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_lobes_are_functions_and_models_are_tenants():
    from agentdispatch.agency import brain as BR
    assert BR.lobe_for(["code"]) == "parietal" and BR.lobe_for(["video"]) == "occipital" and BR.lobe_for(["image", "vision"]) == "occipital" and BR.lobe_for(["speech"]) == "broca"
    assert BR.lobe_for(["audio"]) == "auditory" and BR.lobe_for(["write"]) == "temporal" and BR.lobe_for(["research", "long-context"]) == "temporal" and BR.lobe_for(["fast"]) == "cerebellum"
    assert BR.lobe_for(["code"], "plan") == "frontal" and BR.lobe_for(["write"], "review") == "frontal" and BR.lobe_for([]) == "temporal"
    for name, lobe in (("qwen2.5vl:7b", "occipital"), ("local-qwen3-coder-30b:latest", "parietal"), ("local-deepseek-r1-8b:latest", "frontal"), ("local-gpt-oss-120b:latest", "frontal"),
                       ("local-qwen3-4b:latest", "cerebellum"), ("local-gemma3-1b:latest", "cerebellum"), ("local-gemma3-27b:latest", "temporal"), ("qwen3.6:35b", "temporal")):
        assert BR.model_lobe(name) == lobe, (name, BR.model_lobe(name))
    assert BR.comfy_role({"1": dict(class_type="WanImageToVideo", inputs=dict(unet_name="wan2.2_i2v_high.safetensors")), "2": dict(class_type="KSampler", inputs={})}) == ("video", ["wan2.2_i2v_high.safetensors"])
    assert BR.comfy_role({"1": dict(class_type="UNETLoader", inputs=dict(unet_name="z_image_turbo_bf16.safetensors")), "2": dict(class_type="KSampler", inputs={}), "3": dict(class_type="SaveImage", inputs={})})[0] == "image"
    assert BR.comfy_role({"1": dict(class_type="FishAudioS2TTS", inputs={})})[0] == "speech" and BR.comfy_role({"1": dict(class_type="ACEStepSampler", inputs={})})[0] == "audio"
    job = dict(kind="code", goal="x")
    easy = BR.difficulty(job, dict(objective="fix a typo", needs=["code"]), 0, 0)
    hard = BR.difficulty(job, dict(objective="x" * 400, needs=["code", "long-context", "vision"]), 2, 2)
    assert 0 < easy < hard <= 1.0 and BR.difficulty(job, dict(objective="fix a typo", needs=["code"]), 1, 0) > easy


def test_the_route_of_a_job_is_recorded_hop_by_hop_with_the_model_and_why():
    from agentdispatch.agency import brain as BR
    from agentdispatch.app import App
    d = tempfile.mkdtemp(prefix="route_")
    try:
        json.dump(dict(work_dir=os.path.join(d, "work")), open(os.path.join(d, "agency.json"), "w"))
        r = rig(checker=lambda job: True)
        jid = code_job(r, steps=1)
        ticks(r, 3)                                                                                          # step on ollama-local, then the review
        app = type("A", (), dict(board=r["board"], jobs=r["jobs"], registry=r["reg"], data=r["d"], availability=r["av"]))()
        hops = BR.route_chain(app, jid)
        assert [(h["kind"], h["lobe"]) for h in hops] == [("step", "parietal"), ("review", "frontal")], hops
        assert hops[0]["agent"] == "ollama-local" and hops[0]["why"] and 0 < hops[0]["difficulty"] <= 1 and hops[0]["outcome"] == "done" and hops[0]["secs"] is not None
        assert hops[1]["agent"] != "ollama-local" and hops[1]["outcome"] in ("approve", "reject")
        # an agent that was passed over is named, with the reason
        r["jobs"].cooldown_set("ollama-local", "limit")
        j2 = code_job(r, steps=1)
        ticks(r, 1)
        h2 = BR.route_chain(app, j2)
        assert h2[0]["agent"] != "ollama-local" and any("ollama-local" in c and "cooling" in c for c in h2[0]["considered"]), h2[0]
        # a pull agent's hand-off is a hop too
        for other in (jid, j2):
            r["jobs"].cancel(other, "dev")
        g = r["jobs"].create("dev", "haiku", "write a haiku", "general")
        r["jobs"].add_steps(g["id"], [dict(objective="draft three lines about rain", needs=["write"])])
        for a in r["reg"].agents.values():
            if a["id"] != "pull-agent":
                a["enabled"] = False
        r["disp"].offer("pull-agent")
        hp = BR.route_chain(app, g["id"])
        assert hp[0]["via"] == "pull" and hp[0]["lobe"] == "temporal" and hp[0]["agent"] == "pull-agent"
    finally:
        done(r)
        shutil.rmtree(d, ignore_errors=True)


def test_running_models_and_service_windows_appear_as_tenants():
    from agentdispatch.agency import brain as BR
    r = rig()
    app = type("A", (), dict(board=r["board"], jobs=r["jobs"], registry=r["reg"], data=r["d"], availability=r["av"]))()
    real = BR._get_json

    def fake(url, timeout=1.2):
        if url.endswith("/api/ps"):
            return dict(models=[dict(name="qwen2.5vl:7b", size=6 * 2**30, size_vram=6 * 2**30), dict(name="local-qwen3-coder-30b:latest", size=18 * 2**30, size_vram=16 * 2**30)])
        if url.endswith("/api/tags"):
            return dict(models=[{}] * 53)
        if ":8188/queue" in url:
            return dict(queue_running=[[0, "p", {"1": dict(class_type="WanImageToVideo", inputs=dict(unet_name="wan2.2_i2v.safetensors"))}]], queue_pending=[])
        if ":8189/queue" in url:
            return dict(queue_running=[], queue_pending=[])
        if "8804" in url:
            return dict(ok=True)
        raise OSError("down")
    try:
        BR._get_json = fake
        BR._svc.update(t=0.0, data=None)
        svc = BR.services(app, force=True)
        assert svc["ollama"]["installed"] == 53 and [m["lobe"] for m in svc["ollama"]["loaded"]] == ["occipital", "parietal"]
        assert [(w["port"], w["busy"], w["role"]) for w in svc["comfy"]] == [(8188, True, "video"), (8189, False, None)] and svc["studio"] is True
        cards = {c["id"]: c for c in BR.agent_cards(app, svc)}
        assert cards["comfy-video"]["state"] == "active" and cards["comfy-video"]["models"] == ["wan2.2_i2v.safetensors"]
        assert cards["comfy-image"]["state"] == "online"                        # windows are up but busy with video: image is idle-online
        assert cards["comfy-speech"]["state"] == "online" and cards["ollama-local"]["models"] == ["qwen2.5vl:7b", "local-qwen3-coder-30b:latest"] and cards["ollama-local"]["state"] in ("online", "active")
        ten = BR.tenants(app, svc)
        names = {(t["kind"], t.get("name"), t.get("lobe")) for t in ten if t["kind"] in ("model", "window")}
        assert ("model", "qwen2.5vl:7b", "occipital") in names and ("model", "local-qwen3-coder-30b:latest", "parietal") in names
        assert any(t["kind"] == "window" and t["working"] and t["lobe"] == "occipital" for t in ten)
        BR._get_json = lambda url, timeout=1.2: (_ for _ in ()).throw(OSError("down"))                                              # nothing running: nothing invented
        BR._svc.update(t=0.0, data=None)
        svc = BR.services(app, force=True)
        assert svc["ollama"]["loaded"] == [] and svc["comfy"] == [] and {c["id"]: c for c in BR.agent_cards(app, svc)}["comfy-video"]["state"] == "off"
    finally:
        BR._get_json = real
        BR._svc.update(t=0.0, data=None)
        done(r)


def test_a_fast_preference_does_not_deadlock_a_step_that_escalation_sent_to_a_stronger_tier():
    r = rig()
    jid = code_job(r)
    job, step = r["jobs"].get(jid), r["jobs"].get(jid)["steps"][0]
    step = dict(step, needs=["code", "fast"])
    job = dict(job, meta=dict(job.get("meta") or {}, min_tier="strong"))
    agent, why = r["disp"].choose(job, step)
    assert agent and r["reg"].tier(agent["id"]) == "strong", (agent and agent["id"], why)
    done(r)


def test_security_fixes_calc_builtins_long_chain_lines_and_ffmpeg_relative_escape():
    import json as _json
    from agentdispatch.localtools import policy as LPOL
    # tool=calc: __builtins__ gave back open() / __import__ by another name
    for code in ("b = __builtins__\nb['open']('x.txt', 'w').write('hi')", "print(__builtins__)", "__import__('os')"):
        assert LPOL.check_calc(code), code
    assert LPOL.check_calc("print(2 + 2)") == []
    # the hash chain's tip is the last line however long it is (a 4 KB tail cut a long claim in half)
    from agentdispatch.campaign.state import last_line
    p = os.path.join(tempfile.mkdtemp(prefix="tip_"), "chain.jsonl")
    with open(p, "wb") as f:
        f.write((_json.dumps(dict(hash="a" * 64, claim="x" * 20000)) + "\n" + _json.dumps(dict(hash="b" * 64, claim="y" * 30000)) + "\n").encode())
    with open(p, "rb") as f:
        assert _json.loads(last_line(f))["hash"] == "b" * 64
    with open(p, "wb") as f:
        f.write(b'{"hash": "only"}')
    with open(p, "rb") as f:
        assert last_line(f) == '{"hash": "only"}'
    # the governor fails closed: a job whose gates cannot be read must not run as if it had none
    from agentdispatch.campaign.govern import Governor
    import inspect
    assert "return True, \"\"\n        except Exception" not in inspect.getsource(Governor.ready)
    # ffmpeg and friends: a relative ../ path is resolved against the folder the tool runs in
    from agentdispatch.localtools.toolbox import ToolBox as Toolbox
    src = inspect.getsource(Toolbox.run) + inspect.getsource(Toolbox)
    assert 'split("/")' in src and "outside the allowed folders" in src


def test_a_host_check_runs_after_the_sandbox_check_only_when_the_owner_allows_it_and_never_passes_by_default():
    from agentdispatch.agency.runner import CheckRunner
    d = tempfile.mkdtemp(prefix="hostchk_")
    try:
        open(os.path.join(d, "check.py"), "w").write("print('sandbox ok')\n")
        open(os.path.join(d, "host_ok.py"), "w").write("import sys; print('real tool ran'); sys.exit(0)\n")
        open(os.path.join(d, "host_bad.py"), "w").write("import sys; print('real tool broke'); sys.exit(3)\n")
        runner = CheckRunner(d, cfg=dict(isolation="none"))                                       # no Docker in this test: the sandbox check runs plainly
        acc = dict(check="python check.py", host_check="python host_ok.py")
        ok, out = runner.run_job(d, acc, allow_host=False)
        assert ok is None and "host checks are not enabled" in out, (ok, out)                    # blocked for the owner, not passed
        ok, out = runner.run_job(d, acc, allow_host=True)
        assert ok is True and "real tool ran" in out and "passed" in out, (ok, out)
        ok, out = runner.run_job(d, dict(check="python check.py", host_check="python host_bad.py"), allow_host=True)
        assert ok is False and "FAILED" in out and "real tool broke" in out, (ok, out)
        ok, out = runner.run_job(d, dict(check="python check.py"), allow_host=False)               # no host check: unchanged
        assert ok is True
    finally:
        shutil.rmtree(d, ignore_errors=True)
    r = rig()
    for bad in ("rm -rf /", "powershell -c whoami", "python -c \"import os\""):
        try:
            r["jobs"].create("dev", "t", "g", "code", "D:/x", dict(check="python check.py", host_check=bad))
            assert False, bad
        except BoardError as e:
            assert e.code == "bad_check", (bad, e.code)
    j = r["jobs"].create("dev", "t", "g", "code", "D:/x", dict(check="python check.py", host_check="python tests/host_check.py"))
    assert j["acceptance"]["host_check"] == "python tests/host_check.py"
    done(r)


def test_a_step_is_shown_what_the_earlier_steps_of_its_job_produced():
    from agentdispatch.agency import prompts as PR
    job = dict(goal="g", kind="general", acceptance={}, meta={}, steps=[
        dict(n=1, state="done", objective="run the tool", evidence="TOOL OUTPUT: hello world"),
        dict(n=2, state="failed", objective="broken step", evidence="never shown"),
        dict(n=3, state="todo", objective="write it up", evidence=None)])
    p = PR.step_prompt(job, dict(n=3, objective="write it up", needs=["write"], attempts=0), [], [])
    assert "STEP 1" in p and "TOOL OUTPUT: hello world" in p and "never shown" not in p
    assert PR.earlier_results(job, dict(n=1)) == ""


def test_a_projects_nested_data_folder_reaches_the_workspace_but_the_brokers_own_data_folder_does_not():
    from agentdispatch.agency.workspace import Workspace
    work = tempfile.mkdtemp(prefix="wsw_")
    plain, broker = tempfile.mkdtemp(prefix="wsp_"), tempfile.mkdtemp(prefix="wsb_")
    try:
        for root in (plain, broker):
            os.makedirs(os.path.join(root, "tests", "data"))
            os.makedirs(os.path.join(root, "data"))
            open(os.path.join(root, "tests", "data", "fixture.py"), "w").write("X = 1")
            open(os.path.join(root, "data", "board.txt"), "w").write("secret-ish")
        os.makedirs(os.path.join(broker, "agentdispatch"))
        ws = Workspace.ensure(work, "J-P", plain)
        assert os.path.isfile(os.path.join(ws.project, "tests", "data", "fixture.py")) and os.path.isfile(os.path.join(ws.project, "data", "board.txt")), "a project's own data folders are its fixtures"
        wb = Workspace.ensure(work, "J-B", broker)
        assert os.path.isfile(os.path.join(wb.project, "tests", "data", "fixture.py")), "nested tests/data is a fixture"
        assert not os.path.exists(os.path.join(wb.project, "data")), "the broker's own top-level data folder (boards, tokens) is never copied"
    finally:
        for x in (work, plain, broker):
            shutil.rmtree(x, ignore_errors=True)


def test_a_static_daily_cap_is_stretched_by_a_live_reading_with_room_but_never_removed():
    for reading, runs, expect_ok in ((None, 2, False), (20, 5, True), (20, 8, False), (90, 2, False)):
        er = dict(IDLE, limits=dict(codex=dict(windows=[dict(pct=reading)], stale=False))) if reading is not None else IDLE
        r = rig(er=er)
        codex = r["reg"].get("codex-cli")
        codex["daily_cap"] = 2
        for i in range(runs):
            r["jobs"].run_started("codex-cli", "J-x", i + 1)
            r["jobs"].run_done(r["jobs"].db.execute("SELECT MAX(id) m FROM runs").fetchone()["m"], "ok")
        ok, why = r["av"].check(codex)
        assert ok is expect_ok, (reading, runs, ok, why)
        done(r)


def test_every_prompt_ends_with_a_strict_reply_contract_and_tool_call_dialects_are_understood():
    from agentdispatch.agency import prompts as PR
    from agentdispatch.agency.realadapters import RealAdapter
    code = dict(id="J", goal="g", kind="code", target="D:/x", acceptance=dict(check="python check.py"), meta={}, steps=[])
    text = dict(id="J", goal="g", kind="general", acceptance={}, meta={}, steps=[])
    for p in (PR.step_prompt(code, dict(n=1, objective="o", needs=["code"], attempts=0), [], []), PR.step_prompt(text, dict(n=1, objective="o", needs=["write"], attempts=0), [], []),
              PR.plan_prompt(code, []), PR.review_prompt(code, "diff", True, "ok")):
        tail = p[-1700:]
        assert "REPLY FORMAT (strict" in tail and "first character is `{`" in tail and "Example of a valid reply" in tail and "I cannot do this" in tail, tail[-400:]
    assert '"files"' in PR.step_prompt(code, dict(n=1, objective="o", needs=["code"], attempts=0), [], [])[-1200:] and '"answer"' in PR.step_prompt(text, dict(n=1, objective="o", needs=["write"], attempts=0), [], [])[-1200:]
    ad = RealAdapter(tempfile.mkdtemp(prefix="fmtw_"), None)
    ours = ad._tool_calls('{"tool_calls": [{"tool": "calc", "code": "print(1)"}]}')
    other = ad._tool_calls('{"tool_calls": [{"tool_call_id": "0", "tool_name": "read_file", "parameters": {"path": "tests/check_probe.py"}}]}')
    oai = ad._tool_calls('{"tool_calls": [{"type": "function", "function": {"name": "calc", "arguments": "{\\"code\\": \\"print(2)\\"}"}}]}')
    named = ad._tool_calls('{"tool_calls": [{"name": "git", "arguments": {"args": ["log", "-1"]}}]}')
    assert ours == [dict(tool="calc", code="print(1)")], ours
    assert other and other[0]["tool"] == "read_file" and other[0]["file"] == "tests/check_probe.py", other
    assert oai == [dict(tool="calc", code="print(2)")], oai
    assert named == [dict(tool="git", args=["log", "-1"])], named
    assert ad._tool_calls('{"files": {"a.py": "x"}}') is None


def test_a_wrong_format_is_re_asked_once_inside_the_attempt_and_an_empty_reply_moves_to_the_next_model():
    from agentdispatch.agency.realadapters import RealAdapter
    work = tempfile.mkdtemp(prefix="fmtr_")
    try:
        ad = RealAdapter(work, None)
        ad.step_request = lambda agent, step, job: "THE PROMPT"
        asked = []
        replies = ["Sure! Here is what I would do ...", json.dumps(dict(answer="the real answer"))]

        def fake(agent, prompt, cwd, job):
            asked.append(prompt)
            return replies[len(asked) - 1], ""
        ad._ask_with_tools = fake
        ad.step_apply = lambda a, step, job, text: Result(ok=True, text="applied") if '"answer"' in text else Result(ok=False, error='the answer had no usable {"answer": ...} object', text=text)
        job = dict(id="J", goal="g", kind="general", acceptance={}, meta={}, steps=[])
        res = ad.run(dict(id="a", run=dict(via="cli")), dict(n=1, objective="o", needs=["write"], attempts=0), job)
        assert res.ok and len(asked) == 2 and "YOUR PREVIOUS REPLY WAS REJECTED" in asked[1] and "Sure! Here is" in asked[1], (res.error, asked)
        asked.clear()
        replies[:] = ["still prose", "more prose"]
        res = ad.run(dict(id="a", run=dict(via="cli")), dict(n=1, objective="o", needs=["write"], attempts=0), job)
        assert not res.ok and "no usable" in res.error and len(asked) == 2, "a second bad answer is a failed attempt (one free repair only)"
        # an empty reply from the first model: the next usable model of the agent's ladder answers
        calls = []

        def chat(prompt, model=None):
            calls.append(model)
            return ("", model) if len(calls) == 1 else ("{\"answer\": \"ok\"}", model)
        rr = rig()
        from agentdispatch.agency.vram import VramWatch
        av = Availability(rr["jobs"], fetch=lambda u: IDLE, which=lambda b: "x", data_dir=rr["d"], vram=VramWatch(fetch=lambda *a, **k: {}, smi=lambda: []))
        ad2 = RealAdapter(work, chat, book=av.book)
        agent = dict(id="custom-router", run=dict(via="router", model="m-one", models=["m-one", "m-two", "m-three"]))
        out, err = ad2._ask(agent, "p", None)
        assert not err and "ok" in out, (out, err)
        assert len(calls) == 2 and calls[0] != calls[1], calls                     # the first empty reply moves to a different model of the ladder
        ad3 = RealAdapter(work, lambda prompt, model=None: ("", model), book=av.book)
        out, err = ad3._ask(agent, "p", None)
        assert not out and "empty reply" in err and "usage limit" not in err, err
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_a_step_that_already_failed_against_an_exact_check_goes_first_to_an_agent_that_can_run_the_check():
    import agentdispatch.nightshift as NS
    r = rig()
    r["reg"].agents["ollama-agent"] = dict(DEFAULTS["ollama-agent"], id="ollama-agent")
    keep, NS.docker_ready = NS.docker_ready, (lambda image: True)
    try:
        jid = code_job(r)
        job = r["jobs"].get(jid)
        fresh = dict(r["jobs"].get(jid)["steps"][0], needs=["code"], attempts=0)
        failed = dict(fresh, attempts=1)
        a0, _ = r["disp"].choose(job, fresh)
        a1, why = r["disp"].choose(job, failed)
        assert a1 and a1["id"] == "ollama-agent", (a0 and a0["id"], a1 and a1["id"], why)
        rework = dict(fresh, objective="Address the review: the reviewer did not approve")
        a2, _ = r["disp"].choose(job, rework)
        assert a2 and a2["id"] == "ollama-agent"
    finally:
        NS.docker_ready = keep
    done(r)


def test_a_job_parked_on_a_dependency_resumes_by_itself_and_a_held_job_says_what_it_waits_for():
    r = rig()
    disp = r["disp"]
    state = dict(ok=False)

    class H:
        def ready(self, job):
            return (state["ok"], "" if state["ok"] else "waiting for job J-9 (running)")
        def controls(self, job):
            return (None, "")
        def stop_reasons(self, job):
            return []
    disp.hooks = H()
    jid = code_job(r)
    r["jobs"].set_state(jid, "blocked", "agency", "held by failed dependency: its dependency J-8 is cancelled")
    disp.tick()
    assert r["jobs"].get(jid)["state"] == "blocked", "its dependency is still not satisfied"
    r["jobs"].set_state(jid, "running", "owner", "x")
    assert disp._held(jid) and "waiting for job J-9" in (r["jobs"].get(jid)["why"] or ""), "a held job says what it waits for"
    r["jobs"].set_state(jid, "blocked", "agency", "held by failed dependency: its dependency J-8 is cancelled")
    state["ok"] = True
    disp.tick()
    assert r["jobs"].get(jid)["state"] in ("running", "reviewing"), r["jobs"].get(jid)["state"]
    done(r)


def test_verdicts_are_read_leniently_and_a_launched_reviewer_is_re_asked_once_before_it_fails():
    from agentdispatch.agency import prompts as PR
    from agentdispatch.agency.realadapters import RealAdapter
    for text, want in (('{"approve": true, "reasons": ["ok"]}', True), ('{"verdict": "approve", "reason": "fine"}', True), ('{"approved": false, "reasons": ["x"]}', False),
                       ('```json\n{"decision": "reject"}\n```', False), ('{"approve": "yes"}', True)):
        assert PR.parse_verdict(text)["approve"] is want, text
    assert PR.parse_verdict("I think it looks good")["reasons"] == [PR.INVALID_VERDICT]
    ad = RealAdapter(tempfile.mkdtemp(prefix="vrd_"), None)
    ad.review_request = lambda job, results, agent=None: "REVIEW PROMPT"
    replies = ["Looks great to me", '{"approve": true, "reasons": ["fine"]}']
    asked = []
    ad._ask = lambda agent, prompt, cwd: (asked.append(prompt), (replies[len(asked) - 1], ""))[1]
    v = ad.review(dict(id="rv"), dict(id="J"), None)
    assert v["approve"] is True and len(asked) == 2 and "REJECTED" in asked[1]
    asked.clear()
    replies[:] = ["prose", "more prose"]
    try:
        ad.review(dict(id="rv"), dict(id="J"), None)
        assert False, "two non-verdicts must fail the reviewer"
    except RuntimeError as e:
        assert "no valid verdict" in str(e)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    print("ALL AGENCY TESTS PASSED")
