"""the campaign layer and the local tools: what a model may run on this computer, how tools are installed and engineered, the campaign state and its proofs of integrity, job governance (dependencies,
scope, cutoff, controls, stop rules), sealed evidence, shadow pairs, playbooks, and the whole thing end to end with fake agents"""
import json, os, shutil, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.app import App
from agentdispatch.store import BoardError
from agentdispatch.agency.availability import Availability
from agentdispatch.agency.dispatcher import Dispatcher
from agentdispatch.agency.realadapters import RealAdapter
from agentdispatch.agency.registry import DEFAULTS
from agentdispatch.agency.workspace import Workspace
from agentdispatch.campaign import meta as M, scope as SC, templates as T, engineer
from agentdispatch.campaign.state import CampaignState
from agentdispatch.localtools import policy as LP
from agentdispatch.web import Server, make_handler, new_token

FAKE = os.path.join(ROOT, "tests", "fake_agent.py")
IDLE = dict(gpus=[dict(util=1.0)], ram=10.0, ram_total_gb=240, limits={})
HAVE = {}
SYMPY_INTEGRAL = "import sympy as sp\nx=sp.symbols('x')\nprint(sp.integrate(sp.sin(x)**2,(x,0,sp.pi)))"
STDLIB_INTEGRAL = ("import math\nn=200000\nh=math.pi/n\ns=sum(math.sin((i+0.5)*h)**2 for i in range(n))*h\n"
                   "print('pi/2' if abs(s-math.pi/2)<1e-9 else s)")                      # the same integral, numerically, with the standard library only


def integral_code(box):
    """sympy is an optional extra (requirements-dev.txt): use it when the calculator's python has it, else the standard-library version"""
    return SYMPY_INTEGRAL if box.reg.scan()["tools"].get("sympy", {}).get("available") else STDLIB_INTEGRAL


def agent(mode, strengths, cost="cheap"):
    return dict(kind="cli", name=mode, strengths=strengths, cost=cost, concurrency=1, daily_cap=500, sandbox="copy", enabled=True, run=dict(argv=[sys.executable, FAKE, mode], stdin_prompt=True, timeout_s=60))


def rig(agents):
    """a real App on temp folders with the registry replaced by fake agents (+ the local-tools agent) and a dispatcher wired the way the broker wires it"""
    d, work = tempfile.mkdtemp(prefix="camp_"), tempfile.mkdtemp(prefix="campwork_")
    json.dump(dict(work_dir=work), open(os.path.join(d, "agency.json"), "w"))
    app = App(d, with_index=False)
    ag = dict(agents)
    ag["local-tools"] = dict(DEFAULTS["local-tools"])
    app.registry.agents = {k: dict(v, id=k) for k, v in ag.items()}
    for a in app.registry.agents.values():
        a.setdefault("er_id", None), a.setdefault("binary", None), a.setdefault("limits_source", "none")
    av = Availability(app.jobs, fetch=lambda u: IDLE, which=lambda b: "x")
    adapter = RealAdapter(work, None, toolbox=app.localtools, governor=app.campaign.governor)
    cfg = dict(__import__("agentdispatch.agency.policy", fromlist=["x"]).DEFAULTS, work_dir=work, max_diff_lines=800)
    disp = Dispatcher(app.board, app.jobs, app.registry, av, adapter, d, cfg=cfg, checker=None, hooks=app.campaign.governor)
    return dict(app=app, d=d, work=work, disp=disp, jobs=app.jobs, gov=app.campaign.governor, camp=app.campaign, box=app.localtools)


def done(r):
    r["app"].board.close()
    for k in ("d", "work"):
        shutil.rmtree(r[k], ignore_errors=True)


def ticks(r, n=12):
    out = []
    for _ in range(n):
        t = r["disp"].tick()
        out.append(t)
        if t["action"] in ("idle",):
            break
    return out


def git(repo, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", repo] + list(a), capture_output=True, text=True, timeout=60)


# ---------------------------------------------------------------- local tools
def test_calculations_lean_git_and_controls_obey_the_policy():
    d = tempfile.mkdtemp(prefix="tools_")
    box = __import__("agentdispatch.localtools", fromlist=["x"]).build(d, [d], os.path.join(d, "work"))
    try:
        st = box.reg.scan(force=True)["tools"]
        assert st["python"]["available"] and st["calc"]["available"]
        r = box.run("calc", code=integral_code(box))
        assert r["ok"] and r["stdout"].strip() == "pi/2" and r["sha256"], r
        for bad in ("import os\nos.system('dir')", "open('x','w')", "import subprocess", "__import__('os')", "print(().__class__)", "import numpy as np\nnp.save('a.npy', [1])"):
            r = box.run("calc", code=bad)
            assert not r["ok"] and r["problems"], bad
        r = box.run("calc", code="while True:\n    pass", timeout=2)
        assert not r["ok"] and "timed out" in r["problems"][0]
        assert LP.check_calc("x = " + "1+" * 9000 + "1")                                              # nested too deeply to check: refused, not a crash
        assert LP.check_calc("x = 1\n" * 5000)                                                           # longer than the code limit
        if st["lean"]["available"]:
            r = box.run("lean", code="theorem t (a b : Nat) : a + b = b + a := Nat.add_comm a b")
            assert r["ok"], r
            r = box.run("lean", code="theorem t (a : Nat) : a = a + 0 := by sorry")
            assert not r["ok"] and r["sorry"] and "sorry" in r["problems"][0]
            r = box.run("lean", code="def f : IO Unit := IO.println \"x\"")
            assert not r["ok"] and "IO." in r["problems"][0]
            r = box.run("lean", code="theorem bad : 1 = 2 := rfl")
            assert not r["ok"] and r["errors"]
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        open(os.path.join(repo, "a.txt"), "w").write("x")
        git(repo, "add", "."), git(repo, "commit", "-qm", "one")
        r = box.run("git", args=["log", "--oneline", "-1"], cwd=repo)
        assert r["ok"] and "one" in r["stdout"]
        for bad in (["push"], ["commit", "-m", "x"], ["log", "-c", "core.pager=x"], ["checkout", "."], ["branch", "newbranch"]):
            assert not box.run("git", args=bad, cwd=repo)["ok"], bad
        assert not box.run("git", args=["log"], cwd=os.path.dirname(d))["ok"]                      # outside every allowed folder
        assert not box.run("pytest", args=["-q"], cwd=None)["ok"]                                   # code-running tools need a trusted folder
        assert not box.run("no-such-tool")["ok"]
        ok, out = box.controls([dict(label="pos", tool="calc", code="print(1+1)"), dict(label="adv", tool="calc", code="raise SystemExit(3)", expect="fail")])
        assert ok and [c["met"] for c in out] == [True, True]
        ok, out = box.controls([dict(label="adv-refused", tool="calc", code="import os", expect="fail")])
        assert not ok and out[0]["got"] == "fail" and not out[0]["met"]                             # a refusal can never satisfy an adverse control
        ok, out = box.controls([dict(label="pos-fails", tool="calc", code="raise SystemExit(1)")])
        assert not ok
        hist = box.history(50)
        assert any(h["tool"] == "calc" for h in hist) and box.usage()["calc"] >= 5
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_installs_are_requested_never_taken_and_only_from_short_lists():
    d = tempfile.mkdtemp(prefix="inst_")
    calls = []
    box = __import__("agentdispatch.localtools", fromlist=["x"]).build(d, [d], os.path.join(d, "work"))
    from agentdispatch.localtools.installer import Installer
    box.installer = inst = Installer(box, spawn=lambda argv, env, timeout, **k: (calls.append(argv) or (0, "ok")), fetch=lambda url, dest: open(dest, "wb").write(b"zip"))
    try:
        if box.reg.scan()["tools"]["sympy"]["available"]:
            assert inst.request("sympy", "pull-agent")["state"] == "installed"                       # already there
        else:
            assert inst.request("sympy", "pull-agent")["state"] == "pending" and not calls            # missing: only requested, nothing runs
        assert not inst.request("no-such", "x")["ok"]
        r = inst.request("z3", "pull-agent", "need an smt solver")
        if box.reg.scan()["tools"]["z3"]["available"]:
            return
        assert r["state"] == "pending" and not calls, r                                           # nothing runs until the owner approves
        assert inst.request("z3", "codex-cli")["id"] == r["id"]                                    # asked twice = one request
        assert inst.plan("z3")["kind"] == "pip" and inst.plan("z3")["packages"] == ["z3-solver"]
        assert inst.plan("lean")["kind"] == "elan" and inst.plan("lean")["url"].startswith("https://github.com/")
        assert inst.plan("ffmpeg")["kind"] == "winget"
        assert inst.deny(r["id"])["state"] == "denied" and not calls
        r2 = inst.request("z3", "x")
        res = inst.approve(r2["id"], "owner", wait=True)
        assert res["ok"]
        assert calls and any("pip" in " ".join(c) or "venv" in " ".join(c) for c in calls)         # approved: it ran (the fake spawn installs nothing, so it is reported not detected)
        assert inst.get(r2["id"])["state"] in ("done", "failed")
        box.cfg["auto_install"] = ["pip"]
        before = len(calls)
        r3 = inst.request("gmpy2", "x")
        for _ in range(240):
            if inst.get(r3["id"])["state"] in ("done", "failed"):
                break
            time.sleep(0.5)
        assert len(calls) > before, "auto_install pip: the owner's setting lets an allow-listed package install itself"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_staged_pip_install_is_reviewed_in_isolation_before_it_is_used():
    d = tempfile.mkdtemp(prefix="stage_")
    box = __import__("agentdispatch.localtools", fromlist=["x"]).build(d, [d], os.path.join(d, "work"))
    inst = box.installer
    try:
        box.reg.scan(force=True)
        log = []
        assert inst.base_python() and inst.base_python("3.99") is None or True
        ok = inst._pip(dict(kind="pip", packages=["networkx"], into=""), log)                      # already satisfied by the main install: no network needed, but the whole staging + review path runs
        assert ok and any("review passed" in l for l in log) and any("review networkx: passed" in l for l in log), log
        live = [p for p in box.cfg["extra_pythons"]]
        assert len(live) == 1 and "staging" in live[0] and os.path.isfile(live[0])
        assert json.load(open(inst._manifest_path()))["base"] == ["networkx"]
        # a package that fails its review is never put into use, and its staging environment is removed
        inst.SMOKE["networkx"] = "assert False, 'broken'"
        log2 = []
        assert not inst._pip(dict(kind="pip", packages=["networkx"], into=""), log2)
        assert any("REVIEW FAILED" in l for l in log2) and box.cfg["extra_pythons"] == live
        assert not any(os.path.isdir(p) for p in __import__("glob").glob(os.path.join(inst.home(), "staging", "base-*")) if p not in [os.path.dirname(os.path.dirname(live[0]))])
        # a refused name never reaches pip; a version the machine does not have is said so
        assert not inst._pip(dict(kind="pip", packages=["evil-package"]), [])
        log3 = []
        assert not inst._pip(dict(kind="pip", packages=["networkx"], python="3.99"), log3) and "no Python 3.99" in log3[0]
        assert inst.request("z3", "x", python="3.x")["error"] == "bad_python" or box.reg.scan()["tools"]["z3"]["available"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- meta, scope, templates
def test_job_governance_fields_are_validated_and_default_to_no_authority():
    m = M.normalize({})
    assert m["human_approval"] == "before_promotion" and not m["result_bearing"] and not any(m[f] for f in M.AUTHORITY_FLAGS)
    for bad in (dict(nonsense=1), dict(role="king"), dict(stop_on=["whatever"]), dict(claim_class="MAGIC"), dict(independence=dict(mode="SEALED_SHADOW")), dict(result_bearing=True, human_approval="none"),
                dict(depends_on_gates="no"), dict(pair=dict(role="x")), dict(controls=[1])):
        try:
            M.normalize(bad)
            assert False, bad
        except BoardError:
            pass
    ok = M.normalize(dict(depends_on_gates=["G05"], independence=dict(mode="SEALED_SHADOW", cutoff="ead50d9"), scope=dict(write=["agents/**"], forbidden=["artifacts/codex_new/**"])))
    assert ok["depends_on_gates"] == {"G05": "CLOSED"}
    assert SC.write_ok(ok, "agents/g06/a.py")[0] and not SC.write_ok(ok, "src/a.py")[0] and not SC.write_ok(ok, "artifacts/codex_new/x.json")[0]
    assert SC.read_ok(ok, "src/a.py") and not SC.read_ok(ok, "artifacts/codex_new/deep/x.json")
    assert SC.match("a/b/c.py", ["**/c.py"]) and SC.match("a/b.py", ["a"]) and not SC.match("ab/b.py", ["a"])
    for tid in T.all_templates():
        kw = T.apply(tid, dict(gate="G06", campaign="REL", cutoff="abc1234"))
        assert kw["meta"]["template"] == tid and kw["title"]
    try:
        T.apply("independent-shadow", dict(gate="G06"))
        assert False
    except BoardError:
        pass


# ---------------------------------------------------------------- the campaign state
HANDOFF = """# handoff

```text
SPEC-SIGNOFF = CLOSED-AUDITED
PERF BRANCH 2 = UNCONSUMED
LOAD TEST = NOT_CLOSED
SECURITY REVIEW = NOT_RUN
```

more prose = not a status line

```text
API DRIFT CHECK = OPEN
PILOT ACCESS = NONE
```
"""


def test_campaign_state_imports_status_blocks_chains_every_change_and_never_moves_by_itself():
    d = tempfile.mkdtemp(prefix="cs_")
    try:
        cs = CampaignState(d)
        created, changed = cs.import_blocks("REL", HANDOFF, "handoff.md")
        g = cs.campaign("REL")["gates"]
        assert created == 6 and g["SPEC-SIGNOFF"]["status"] == "CLOSED-AUDITED" and g["API DRIFT CHECK"]["status"] == "OPEN" and "more prose" not in " ".join(g)
        assert cs.import_blocks("REL", HANDOFF, "handoff.md") == (0, 0)
        cs.define_gate("REL", "G07", "G07", depends_on=["API DRIFT CHECK"], status="NOT_RUN")
        view = {x["id"]: x["derived"] for x in cs.gate_view("REL")}
        assert view["SPEC-SIGNOFF"] == "closed" and view["G07"] == "locked" and view["API DRIFT CHECK"] == "open"
        assert cs.next_open("REL")["id"] in ("PERF BRANCH 2", "LOAD TEST", "SECURITY REVIEW", "API DRIFT CHECK", "PILOT ACCESS")
        cs.transition("REL", "API DRIFT CHECK", "CLOSED-AUDITED", "owner", why="audited")
        assert {x["id"]: x["derived"] for x in cs.gate_view("REL")}["G07"] == "open"
        # a proposal waits; the owner decides; an agent cannot make it automatic
        p = cs.propose("REL", "G07", "CLOSED-AUDITED", "J-000009", "codex-cli", "done")
        assert p["proposal"]["state"] == "pending" and cs.campaign("REL")["gates"]["G07"]["status"] == "NOT_RUN"
        assert cs.decide(p["proposal"]["id"], False)["proposal"]["state"] == "rejected" and cs.campaign("REL")["gates"]["G07"]["status"] == "NOT_RUN"
        cs.d["campaigns"]["REL"]["authority"] = "PROMOTION_ALLOWED"
        cs.define_gate("REL", "G07", requires_human=False, auto_promote=True)
        auto = cs.propose("REL", "G07", "CLOSED-AUDITED", "J-000010", "codex-cli", "done again", claim="HEURISTIC")
        assert auto["proposal"]["state"] == "approved" and cs.campaign("REL")["gates"]["G07"]["status"] == "CLOSED-AUDITED"      # only because the owner allowed it for this gate
        cs.define_gate("REL", "G08", "G08", status="NOT_RUN", requires_human=False, auto_promote=True)
        never = cs.propose("REL", "G08", "CLOSED-AUDITED", "J-000011", "codex-cli", "x", claim="INDEPENDENTLY_CONFIRMED")
        assert never["proposal"]["state"] == "pending", "a self-claim of independent confirmation never promotes by itself"
        ok, why = cs.verify_log()
        assert ok, why
        lines = open(cs.log_path, encoding="utf-8").read().splitlines()
        rec = json.loads(lines[3])
        rec["to"] = "TAMPERED"
        lines[3] = json.dumps(rec)
        open(cs.log_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        assert not CampaignState(d).verify_log()[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- the governor with fake agents
def test_a_job_waits_for_its_gates_and_a_result_bearing_job_waits_for_the_owner():
    r = rig({"w": agent("answer", ["write", "plan", "review"]), "rv": agent("approve", ["review"], cost="plan")})
    try:
        cs = r["camp"].state
        cs.define_gate("C1", "A", "A", status="OPEN")
        cs.define_gate("C1", "B", "B", depends_on=["A"], status="NOT_RUN")
        j = r["jobs"].create("dev", "needs A", "write", "general", "", None, None, 1, dict(campaign="C1", gate="B", depends_on_gates=["A"], seal=False))
        r["jobs"].add_steps(j["id"], [dict(objective="write something", needs=["write"])])
        assert not r["gov"].ready(r["jobs"].get(j["id"]))[0] and "gate" in r["gov"].ready(r["jobs"].get(j["id"]))[1]
        assert ticks(r)[0]["action"] == "idle" and r["jobs"].get(j["id"])["steps"][0]["state"] == "todo"      # held back: nothing ran
        cs.transition("C1", "A", "CLOSED-AUDITED", "owner")
        assert r["gov"].ready(r["jobs"].get(j["id"]))[0]
        k = r["jobs"].create("dev", "restricted", "run", "production", "", None, None, 1, dict(campaign="C1", result_bearing=True, human_approval="before_run"))
        r["jobs"].add_steps(k["id"], [dict(objective="run the frozen thing", needs=["write"])])
        ok, why = r["gov"].ready(r["jobs"].get(k["id"]))
        assert not ok and "approval" in why and any(w["needs_approval"] for w in r["gov"].waiting())
        r["camp"].state.approve_run(k["id"], "owner")
        assert r["gov"].ready(r["jobs"].get(k["id"]))[0]
        first = r["jobs"].create("dev", "first", "write", "general")
        second = r["jobs"].create("dev", "second", "write", "general", "", None, None, 1, dict(depends_on_jobs=[first["id"]]))
        assert "waiting for job" in r["gov"].ready(r["jobs"].get(second["id"]))[1]
        r["jobs"].cancel(first["id"], "dev")
        assert "is cancelled" in r["gov"].ready(r["jobs"].get(second["id"]))[1]
    finally:
        done(r)


def test_a_tool_step_runs_on_this_computer_with_no_model_then_is_reviewed_sealed_and_proposed_never_promoted():
    r = rig({"rv": agent("approve", ["review"], cost="plan")})
    try:
        cs = r["camp"].state
        cs.define_gate("C1", "G06", "G06", status="OPEN")
        j = r["jobs"].create("dev", "integral", "check the integral of sin^2 over 0..pi", "general", "", dict(rubric="the value is shown"), None, 1,
                             dict(campaign="C1", gate="G06", template="numerical-certificate", role="primary", claim_class="CERTIFICATE_NUMERICAL", stop_on=["check_failed", "control_failed", "hash_mismatch"],
                                  controls=[dict(label="adverse: a false identity must fail", tool="calc", code="assert 1 == 2", expect="fail"), dict(label="positive", tool="calc", code="print(2+2)", expect="pass")]))
        r["jobs"].add_steps(j["id"], [dict(objective="integrate sin^2 on 0..pi exactly", needs=["tool"], tool=dict(tool="calc", code=integral_code(r["box"])))])
        out = ticks(r)
        job = r["jobs"].get(j["id"])
        assert job["state"] in ("delivered", "ready"), (job["state"], job["why"], out)
        assert job["steps"][0]["agent"] == "local-tools" and "pi/2" in job["steps"][0]["evidence"]       # the computer did the work: no model wrote the answer
        assert r["jobs"].runs_for_job(j["id"]) == 2                                                    # the tool step and the review: the only agent run was the reviewer's
        assert job["result"]["controls_ok"] is True
        assert r["box"].history(10, job=j["id"])
        # sealed evidence, machine-readable result, playbook, and a proposal that waits for the owner
        ev = r["app"].api.evidence(j["id"])
        assert ev["sealed"] and ev["seal_verifies"] and ev["result"]["status"] == "COMPLETE" and ev["result"]["claim_class"] == "CERTIFICATE_NUMERICAL"
        pend = r["camp"].state.pending()
        assert len(pend) == 1 and pend[0]["to"] == "CANDIDATE" and cs.campaign("C1")["gates"]["G06"]["status"] == "OPEN"
        pb = r["camp"].playbooks.get("PB-" + j["id"])
        assert pb["outcome"] == "ok" and pb["tools"][0]["tool"] == "calc" and pb["cost"]["local_tool_runs"] >= 1 and pb["route"]
        assert r["app"].api.playbooks(q="integral sin")["playbooks"]
        # the seal is immutable: change an artifact and verification fails; a correction is an erratum
        art = os.path.join(r["camp"].evidence.job_dir(j["id"]), "artifacts", "workflow_inputs.json")
        open(art, "a").write(" ")
        assert not r["camp"].evidence.verify_seal(j["id"])[0]
        assert r["camp"].evidence.erratum(j["id"], "typo in the title", "owner") and r["camp"].evidence.errata(j["id"])
        assert r["camp"].evidence.verify()[0]
    finally:
        done(r)


def test_a_failing_control_or_a_stop_rule_keeps_the_result_from_being_accepted():
    r = rig({"rv": agent("approve", ["review"], cost="plan")})
    try:
        j = r["jobs"].create("dev", "bad control", "x", "general", "", None, None, 1,
                             dict(campaign="C1", gate="G", controls=[dict(label="adverse control that does not fail", tool="calc", code="print(1)", expect="fail")], stop_on=["control_failed"]))
        r["jobs"].add_steps(j["id"], [dict(objective="calc", needs=["tool"], tool=dict(tool="calc", code="print(5)"))])
        ticks(r, 8)
        job = r["jobs"].get(j["id"])
        assert job["state"] != "delivered" and job["result"]["controls_ok"] is False, job["state"]
        assert any("adverse control" in (s.get("objective") or "") or "control" in (job["why"] or "") for s in job["steps"]) or "control" in job["why"]
        pb = r["gov"].workflow(r["jobs"].get(j["id"]), "blocked")                                      # the workflow as it would be filed if the job ended here
        assert any(f["kind"] == "control_failed" for f in pb["failure_points"]) and pb["outcome"] == "blocked"
        # a discrepancy flag is a stop rule
        k = r["jobs"].create("dev", "flagged", "x", "general", "", None, None, 1, dict(campaign="C1", gate="G", stop_on=["discrepancy"], seal=False))
        r["jobs"].add_steps(k["id"], [dict(objective="calc", needs=["tool"], tool=dict(tool="calc", code="print(5)"))])
        r["gov"]._write(k["id"], "flags.json", dict(discrepancy=True))
        ticks(r, 8)
        kj = r["jobs"].get(k["id"])
        assert kj["state"] == "blocked" and "STOPPED" in kj["why"] and "discrepancy" in kj["why"], kj["why"]
    finally:
        done(r)


def test_a_model_can_ask_the_computer_to_calculate_and_only_the_short_result_goes_back():
    r = rig({"m": agent("toolcalls", ["research", "write"]), "rv": agent("approve", ["review"], cost="plan")})
    try:
        j = r["jobs"].create("dev", "integral", "what is the integral of sin^2 over 0..pi?", "general")
        r["jobs"].add_steps(j["id"], [dict(objective="compute it", needs=["write"])])
        ticks(r)
        job = r["jobs"].get(j["id"])
        assert job["state"] == "delivered" and "pi/2" in job["steps"][0]["evidence"], job["steps"][0]
        h = r["box"].history(5, job=j["id"])
        assert h and h[0]["caller"] == "m" and h[0]["tool"] == "calc", h
    finally:
        done(r)


def test_scope_is_enforced_where_files_move_and_a_cutoff_hides_what_is_newer():
    repo = tempfile.mkdtemp(prefix="repo_")
    r = rig({"w": agent("forbidden", ["code"], cost="local"), "rv": agent("approve", ["review"], cost="plan")})
    try:
        os.makedirs(os.path.join(repo, "src")), os.makedirs(os.path.join(repo, "secret"))
        open(os.path.join(repo, "src", "a.py"), "w").write("a = 1\n")
        git(repo, "init", "-q"), git(repo, "add", "."), git(repo, "commit", "-qm", "one")
        old = git(repo, "rev-parse", "HEAD").stdout.strip()
        open(os.path.join(repo, "src", "newer.py"), "w").write("newer = 1\n")
        open(os.path.join(repo, "secret", "codex.json"), "w").write("{}")
        git(repo, "add", "."), git(repo, "commit", "-qm", "two")
        ws, full = Workspace.ensure_git(r["work"], "J-X", repo, old)
        assert full == old and ws.files() == ["src/a.py"], ws.files()
        ws2, _ = Workspace.ensure_git(r["work"], "J-Y", repo, "HEAD", allow=lambda rel: not SC.match(rel, ["secret/**"]))
        assert "src/newer.py" in ws2.files() and "secret/codex.json" not in ws2.files()
        try:
            Workspace.ensure_git(r["work"], "J-Z", repo, "deadbeef")
            assert False
        except Exception as e:
            assert "not a commit" in str(e)
        # a patch that writes outside the allowed scope is refused before it is written
        json.dump(dict(allowed_roots=[repo.replace("\\", "/")]), open(os.path.join(r["d"], "nightshift.json"), "w"))
        j = r["jobs"].create("dev", "scoped", "write only under src", "code", repo.replace("\\", "/"), dict(rubric="x"), None, 1,
                             dict(repo=repo, scope=dict(write=["src/**"], forbidden=["secret/**"]), independence=dict(mode="SEALED_SHADOW", cutoff=old, forbidden_inputs=["secret/**"])))
        r["jobs"].add_steps(j["id"], [dict(objective="change something", needs=["code"])])
        r["disp"].tick()
        st = r["jobs"].get(j["id"])["steps"][0]
        assert st["attempts"] == 1 and "scope" in (st["evidence"] or "") + str(r["app"].board.db.execute("SELECT evidence FROM attempts ORDER BY ts DESC LIMIT 1").fetchone()[0]), st
        w = Workspace(r["work"], j["id"], r["jobs"].get(j["id"])["created"])
        assert w.files() == ["src/a.py"] and not os.path.exists(os.path.join(w.project, "secret"))       # the job's copy is the cutoff snapshot
    finally:
        done(r)
        shutil.rmtree(repo, ignore_errors=True)


def test_a_model_cannot_confirm_itself_and_a_shadow_pair_is_compared_after_both_seal():
    r = rig({"p": agent("closes", ["research", "write", "plan"]), "s": agent("closes", ["research", "write"], cost="plan"), "rv": agent("approve", ["review"], cost="plan")})
    try:
        cs = r["camp"].state
        cs.define_gate("C1", "G06", "G06", status="OPEN")
        j = r["jobs"].create("dev", "primary", "close G06", "general", "", dict(rubric="x"), None, 1, dict(campaign="C1", gate="G06", template="primary-closure", role="primary", unlocks=["G06"]))
        r["jobs"].add_steps(j["id"], [dict(objective="derive the bound", needs=["write"])])
        ticks(r)
        res = r["gov"]._read(j["id"], "result.json")
        assert res["claim_class"] == "" and res["requested_transition"] == {"G06": "CLOSED-AUDITED"}, res          # INDEPENDENTLY_CONFIRMED was dropped: only a second sealed job can earn it
        pend = [p for p in cs.pending() if p["gate"] == "G06"]
        assert pend and pend[0]["to"] == "CLOSED-AUDITED" and cs.campaign("C1")["gates"]["G06"]["status"] == "OPEN"
        try:
            r["gov"].spawn_shadow(j["id"])
            assert False
        except BoardError:
            pass
        sh = r["gov"].spawn_shadow(j["id"], cutoff="abc1234")
        assert sh["meta"]["independence"]["mode"] == "SEALED_SHADOW" and sh["meta"]["pair"] == dict(of=j["id"], role="shadow", with_=[]) and j["id"] in sh["meta"]["independence"]["forbidden_inputs"]
        # both sealed with the same stated value -> they agree (evidence, not a promotion)
        cmp = r["gov"].compare(j["id"], j["id"])
        assert cmp["match"] in (True, None)
        r["gov"]._write(sh["id"], "agent_result.json", dict(values={"bound": "1/9"}))
        r["camp"].evidence.seal(sh["id"], None, [], None, {}, {"result.json": "{}"})
        cmp = r["gov"].compare(j["id"], sh["id"])
        assert cmp["match"] is False and "bound" in cmp["differences"][0]
        r["gov"]._pairs_after(r["jobs"].get(sh["id"]), {})
        assert r["gov"]._read(j["id"], "flags.json")["discrepancy"] and any(x["title"].startswith("Reconcile") for x in r["jobs"].list(limit=50))
    finally:
        done(r)


def test_an_open_premise_spawns_a_bounded_follow_up_job():
    r = rig({"p": agent("opens", ["research", "write", "plan"]), "rv": agent("approve", ["review"], cost="plan")})
    try:
        j = r["jobs"].create("dev", "primary", "close G06", "general", "", dict(rubric="x"), None, 1, dict(campaign="C1", gate="G06", template="primary-closure", role="primary"))
        r["jobs"].add_steps(j["id"], [dict(objective="derive", needs=["write"])])
        ticks(r)
        auto = [x for x in r["jobs"].list(limit=50) if x["owner"] == "governor"]
        assert len(auto) == 1 and "vector orientation drift bound" in auto[0]["title"] + auto[0]["goal"]
        assert r["gov"].spawn_followup(r["jobs"].get(j["id"]), "the vector orientation drift bound") is None          # the same premise is not spawned twice
        deep = r["jobs"].create("dev", "deep", "x", "general", "", None, None, 1, dict(campaign="C1", auto_depth=3))
        assert r["gov"].spawn_followup(r["jobs"].get(deep["id"]), "another premise") is None                          # depth limit
    finally:
        done(r)


# ---------------------------------------------------------------- playbooks
def test_playbooks_are_filed_searched_summarised_by_failure_and_carried_to_another_project():
    r = rig({"rv": agent("approve", ["review"], cost="plan")})
    try:
        pb = r["camp"].playbooks
        base = dict(kind="general", template="primary-closure", campaign="C1", gate="G06", created=1, finished=2, secs=1, steps=[dict(n=1, objective="integrate", needs=["tool"], agent="local-tools", tool="calc")], route=[], tools=[dict(tool="calc")],
                    acceptance=None, meta={}, lessons=["start at a stronger tier"], cost={}, artifacts=[], result=None, checks={})
        pb.record(dict(base, job="J-1", title="integrate sin squared", goal="integrate sin^2 exactly", outcome="ok", failure_points=[]))
        pb.record(dict(base, job="J-2", title="integrate cos squared", goal="integrate cos^2 exactly", outcome="failed", failure_points=[dict(where="step 1", kind="step_failed", agent="ollama-local", reason="the answer had no usable JSON 12")]))
        pb.record(dict(base, job="J-3", title="another integral", goal="integrate tan", outcome="failed", failure_points=[dict(where="step 2", kind="step_failed", agent="ollama-local", reason="the answer had no usable JSON 7")]))
        assert pb.get("PB-J-1")["job"] == "J-1" and os.path.isfile(pb.index["PB-J-1"]["file"]) and pb.index["PB-J-1"]["category"] == "general/primary-closure"
        assert [x["job"] for x in pb.search("integral of sin squared", outcome="ok")] == ["J-1"]
        f = pb.failures()
        assert f[0]["count"] == 2 and f[0]["kind"] == "step_failed" and f[0]["agents"] == ["ollama-local"], f              # numbers folded: the same failure counted twice
        s = pb.suggest("integrate sin^2 exactly")
        assert s["similar_successes"][0]["id"] == "PB-J-1" and s["warnings"]
        assert {c["category"]: c["total"] for c in pb.categories()} == {"general/primary-closure": 3}
        pb.record(dict(base, job="J-1", title="integrate sin squared", goal="integrate sin^2 exactly", outcome="failed", failure_points=[]))     # re-recorded: moved, not duplicated
        assert len(pb.index) == 3 and "ok" not in pb.index["PB-J-1"]["file"].split(os.sep)[-2:-1]
        other = tempfile.mkdtemp(prefix="other_")
        from agentdispatch.campaign.playbooks import Playbooks
        n = Playbooks(other).import_(pb.export())
        assert n == 3 and Playbooks(other).get("PB-IMP-J-2")["imported"]
        shutil.rmtree(other, ignore_errors=True)
    finally:
        done(r)


# ---------------------------------------------------------------- engineering a missing tool
def test_a_missing_tool_is_found_installed_or_built_and_a_built_one_joins_the_toolbox():
    r = rig({"b": agent("answer", ["write"]), "rv": agent("approve", ["review"], cost="plan")})
    try:
        box, jobs, gov = r["box"], r["jobs"], r["gov"]
        ex = engineer.need_tool(box, jobs, gov, "algebra", "symbolic algebra: integrate and simplify expressions exactly", None, "pull-agent")
        if box.reg.scan()["tools"]["sympy"]["available"]:
            assert ex["action"] == "exists" and ex["tool"]["id"] in ("sympy", "calc")
        else:                                                                                        # sympy is an optional extra: without it the answer is "install it" (owner approval)
            assert ex["action"] in ("install", "exists") and ex["tool"]["id"] in ("sympy", "calc"), ex
        assert not engineer.need_tool(box, jobs, gov, "x", "short", None, "a")["ok"]
        json.dump(dict(allowed_roots=[r["d"].replace("\\", "/")]), open(os.path.join(r["d"], "nightshift.json"), "w"))
        b = engineer.need_tool(box, jobs, gov, "rot13", "rotate every letter in a text by thirteen places and print the result", [dict(args=["hello"], expect="uryyb")], "pull-agent", build_dir=os.path.join(r["d"], "build"))
        assert b["action"] == "build" and jobs.get(b["job"])["meta"]["tool_build"] == dict(name="rot13"), b
        folder = b["folder"]
        assert os.path.isfile(os.path.join(folder, "test_tool.py")) and "uryyb" in open(os.path.join(folder, "examples.json")).read()
        # the "agent" writes the tool in the job's workspace copy, then the owner accepts: it is registered
        job = jobs.get(b["job"])
        ws = Workspace.ensure(r["work"], job["id"], folder, stamp=job["created"])
        open(os.path.join(ws.project, "tool.py"), "w").write("import codecs, sys\nprint(codecs.encode(' '.join(sys.argv[1:]), 'rot13'))\n")
        jobs.add_steps(job["id"], [dict(objective="write the tool", needs=["code"])])
        jobs.set_state(job["id"], "reviewing", "t")
        jobs.set_state(job["id"], "ready", "t")
        jobs.set_state(job["id"], "merged", "owner", "accepted")
        t = {x["id"]: x for x in box.reg.listing()}
        assert t["custom-rot13"]["available"] and t["custom-rot13"]["custom"]
        out = box.run("custom-rot13", args=["hello"])
        assert out["ok"] and out["stdout"].strip() == "uryyb", out
        assert not os.path.exists(os.path.join(box.reg.custom_dir, "rot13", "test_tool.py"))
        assert "tool:custom-rot13" in [c["id"] for c in __import__("agentdispatch.agency.brain", fromlist=["x"]).tool_cards(r["app"])]          # it appears in the brain by itself
    finally:
        done(r)


def test_a_production_job_with_a_folder_and_a_check_is_code_work_and_the_folder_must_be_allowed():
    from agentdispatch.agency.jobs import is_code
    r = rig({"c": agent("patch", ["code"], cost="local"), "rv": agent("approve", ["review"], cost="plan")})
    proj = tempfile.mkdtemp(prefix="proj_")
    try:
        open(os.path.join(proj, "calc.py"), "w").write("def add(a, b):\n    return a - b\n")
        open(os.path.join(proj, "test_calc.py"), "w").write("import calc\nassert calc.add(2, 3) == 5\n")
        try:
            r["jobs"].create("dev", "build", "x", "production", proj.replace("\\", "/"), dict(check="python test_calc.py"))
            assert False, "a folder that is not allowed must be refused, whatever the kind"
        except BoardError as e:
            assert e.code == "bad_target" and "Allow" in str(e)
        assert r["jobs"].create("dev", "free text", "x", "general", "anything goes")["state"] == "queued"                   # no check: the target is only a note
        json.dump(dict(allowed_roots=[proj.replace("\\", "/")]), open(os.path.join(r["d"], "nightshift.json"), "w"))
        j = r["jobs"].create("dev", "build", "make add work", "production", proj.replace("\\", "/"), dict(check="python test_calc.py"))
        assert is_code(j) and not is_code(dict(kind="production", target="", acceptance={}))
        r["jobs"].add_steps(j["id"], [dict(objective="fix add", needs=["code"])])
        r["jobs"].cancel(r["jobs"].list(limit=5)[-1]["id"], "t")                                                      # the free-text job is not part of this check
        ticks(r, 3)
        ws = Workspace(r["work"], j["id"], r["jobs"].get(j["id"])["created"])
        assert "return a + b" in open(os.path.join(ws.project, "calc.py")).read(), "the agent got the project's files and answered with file contents, not prose"
        # a reviewer that is not there is said so on the job instead of leaving it silently in review
        r["jobs"].set_state(j["id"], "reviewing", "t", "all steps done")
        r["app"].registry.agents.pop("rv")
        r["disp"].tick()
        assert "reviewer" in (r["jobs"].get(j["id"])["why"] or ""), r["jobs"].get(j["id"])["why"]
    finally:
        done(r)
        shutil.rmtree(proj, ignore_errors=True)


def test_the_architect_interviews_studies_designs_plans_is_attacked_by_another_model_and_only_then_creates_jobs():
    from agentdispatch.intake import web_api as IW, blueprint as BP
    r = rig({"claude-code": agent("architect", ["plan", "research"], cost="premium"), "codex-cli": agent("architect", ["plan", "review"], cost="plan")})
    proj = os.path.join(tempfile.mkdtemp(prefix="arch_"), "bracket")
    try:
        app = r["app"]
        for a in app.registry.agents.values():
            a["er_id"], a["binary"] = None, None
        it, arch = IW.intake_of(app), IW.architect_of(app)
        sid = it.new()["id"]
        txt = "I need a fold-down wall bracket for a 45 kg compressor, under 8 kg. TARGET=%s" % proj.replace("\\", "/")
        it.say(sid, "user", txt)
        arch.start(sid, "interview", txt).join(60)
        s = it.get(sid)
        bp = s["blueprint"]
        assert bp["title"] == "Fold-down wall bracket" and [q["id"] for q in bp["open_questions"]] == ["Q1"] and bp["reference_systems"][0]["name"] == "vendor-firmware.bin" and s["messages"][-1]["by"] == "claude-code"
        assert s["form"]["title"] == "Fold-down wall bracket" and s["work"]["state"] == "idle"
        arch.start(sid, "think").join(240)
        s = it.get(sid)
        log = " | ".join(t["text"] for t in s["thinking"])
        assert "runs calc" in log and "Fixing the blockers" in log, log                                   # it studied with a tool, was attacked, and revised
        assert s["critique"]["by"] == "codex-cli", s["critique"]                                          # a DIFFERENT model than the one that wrote the blueprint
        assert s["phase"] == "ready" and s["readiness"]["ok"], s["readiness"]
        assert s["blueprint"]["findings"][0]["text"].startswith("the load table") and s["readiness"]["coverage"] == {"R1": ["build"], "R2": ["tests"]}
        assert [x["key"] for x in s["blueprint"]["plan"]["jobs"]] == ["tests", "build"] and s["blueprint"]["design"]["hooks"][0]["name"] == "mass-check"
        # the gate: a plan with an uncovered requirement, a loop, or an unverifiable job is not ready
        bad = BP.normalize(dict(plan=dict(jobs=[dict(key="a", title="a", goal="g", kind="general", depends_on=["b"], covers=["R1"]), dict(key="b", title="b", goal="g", kind="general", depends_on=["a"], rubric="x")])), s["blueprint"])
        v = BP.validate(bad, app)
        assert not v["ok"] and any("loop" in b for b in v["blockers"]) and any("no way to be verified" in b for b in v["blockers"]) and any("R2" in b for b in v["blockers"]), v["blockers"]
        # nothing is created before it is approved, and an unallowed folder is refused clearly
        assert r["jobs"].list(limit=5) == []
        try:
            arch.submit_plan(sid, "dev")
            assert False
        except BoardError as e:
            assert e.code == "bad_target" and "allowed project folders" in str(e)
        json.dump(dict(allowed_roots=[os.path.dirname(proj).replace("\\", "/"), r["d"].replace("\\", "/")]), open(os.path.join(r["d"], "nightshift.json"), "w"))
        made = arch.submit_plan(sid, "dev")
        by_title = {j["title"]: j for j in made}
        assert os.path.isfile(os.path.join(proj, "tools", "acceptance.py")), "the acceptance script the architect wrote was installed in the new project folder"
        build, tests = by_title["Build the bracket model"], by_title["Write acceptance tests"]
        tool_job = next(j for j in made if j["title"].startswith("Build tool: mass-calc"))
        assert tests["id"] in build["meta"]["depends_on_jobs"] and tool_job["id"] in build["meta"]["depends_on_jobs"] and build["acceptance"]["check"] == "python tools/acceptance.py"
        assert not r["gov"].ready(r["jobs"].get(build["id"]))[0] and it.get(sid)["status"] == "submitted"       # the build waits for its tests and its tool
        try:
            arch.submit_plan(sid, "dev")
            assert False
        except BoardError as e:
            assert e.code in ("closed", "not_ready")
    finally:
        done(r)
        shutil.rmtree(os.path.dirname(proj), ignore_errors=True)


def test_accepted_changes_are_applied_to_the_real_folder_with_a_backup_and_undone_when_the_check_fails():
    from agentdispatch.agency.apply import make_apply
    d, work, proj = tempfile.mkdtemp(prefix="ap_"), tempfile.mkdtemp(prefix="apw_"), tempfile.mkdtemp(prefix="app_")
    try:
        open(os.path.join(proj, "a.py"), "w").write("old\n")
        open(os.path.join(proj, "gone.py"), "w").write("bye\n")
        job = dict(id="J-000001", created=5, target=proj, acceptance=dict(check="python a.py"))
        ws = Workspace.ensure(work, "J-000001", proj, stamp=5)
        ws.write("a.py", "new\n")
        ws.write("sub/b.py", "added\n")
        os.remove(os.path.join(ws.project, "gone.py"))
        bad = make_apply(d, work, lambda p_, c: (False, "boom"))(job)
        assert not bad["ok"] and "put back" in bad["why"] and open(os.path.join(proj, "a.py")).read() == "old\n" and os.path.isfile(os.path.join(proj, "gone.py")) and not os.path.exists(os.path.join(proj, "sub", "b.py"))
        ok = make_apply(d, work, lambda p_, c: (True, ""))(job)
        assert ok["ok"] and open(os.path.join(proj, "a.py")).read() == "new\n" and not os.path.exists(os.path.join(proj, "gone.py")) and open(os.path.join(proj, "sub", "b.py")).read() == "added\n"
        assert any("a.py" in f for _, _, fs in os.walk(os.path.join(d, "backups")) for f in fs), "the overwritten file was backed up"
        assert not make_apply(d, work, None)(dict(job, target=os.path.join(proj, "nope")))["ok"]
    finally:
        for x in (d, work, proj):
            shutil.rmtree(x, ignore_errors=True)


def test_autonomous_mode_red_before_green_budget_and_repair_of_a_failed_job():
    import agentdispatch.agency.policy as POL
    from agentdispatch.intake import web_api as IW, autonomy
    from agentdispatch.intake.autonomy import Supervisor
    r = rig({"claude-code": agent("vacuous", ["plan", "research"], cost="premium"), "codex-cli": agent("vacuous", ["plan", "review"], cost="plan")})
    root = tempfile.mkdtemp(prefix="proots_")
    try:
        app = r["app"]
        for a in app.registry.agents.values():
            a["er_id"], a["binary"] = None, None
        assert POL.is_safe_repo(dict(POL.DEFAULTS, safe_trees=["D:/x"]), "d:/x/sub/p") and not POL.is_safe_repo(dict(POL.DEFAULTS, safe_trees=["D:/x"]), "d:/x/../y") and not POL.is_safe_repo(POL.DEFAULTS, "d:/x/sub")
        it, arch = IW.intake_of(app), IW.architect_of(app)
        # off by default: a ready plan waits for the owner
        assert not app.usage.autonomy.load()["enabled"] and not it.new()["autonomous"]
        cfg = autonomy.enable(app, root, strong_calls_per_session=30)
        ag = json.load(open(os.path.join(app.data, "agency.json")))
        assert cfg["enabled"] and ag["auto_merge"] is True and root.replace("\\", "/") in ag["safe_trees"] and root.replace("\\", "/") in app.localtools.allowed_roots
        proj = os.path.join(root, "bracket").replace("\\", "/")
        sid = it.new()["id"]
        assert it.get(sid)["autonomous"] is True
        txt = "A fold-down bracket. TARGET=%s" % proj
        it.say(sid, "user", txt)
        arch.start(sid, "auto", txt).join(300)
        s = it.get(sid)
        log = " | ".join(t["text"] for t in s["thinking"])
        assert "PASSES before any work is done" in " ".join(s["readiness"]["blockers"]) or "Fixing the blockers" in log, log                  # the vacuous test was caught
        assert s["phase"] == "running" and s["status"] == "submitted" and "Autonomous" in log, (s["phase"], s["status"], s["readiness"])
        assert os.path.isfile(os.path.join(proj, "tools", "acceptance.py")) and "import brackets" in open(os.path.join(proj, "tools", "acceptance.py")).read()      # the red version was installed
        jid = s["made"]["build"]
        assert r["jobs"].get(jid)["acceptance"]["check"] == "python tools/acceptance.py"
        u = app.usage.summary()
        assert u["strong_calls_today"] >= 4 and u["strong_tokens_est"] > 0 and any(a["architect_calls"] for a in u["agents"] if a["id"] in ("claude-code", "codex-cli"))
        # the job fails: the supervisor climbs the repair LADDER - first the same job again at a stronger model (no architect call), then the architect rewrites it, then it may SPLIT it
        r["jobs"].add_steps(jid, [dict(objective="x", needs=["code"])]) if not r["jobs"].get(jid)["steps"] else None
        r["jobs"].set_state(jid, "failed", "t", "step 1 failed after 3 attempt(s): boom")
        calls0 = app.usage.summary()["strong_calls_today"]
        sup = Supervisor(app)
        sup.tick()
        s = it.get(sid)
        assert s["repairs"] == {"build": 1} and s["made"]["build"] != jid, s["repairs"]
        new = r["jobs"].get(s["made"]["build"])
        assert "retry" in new["title"] and "PREVIOUS ATTEMPT FAILED" in new["goal"] and "boom" in new["goal"] and new["priority"] == 2
        assert app.usage.summary()["strong_calls_today"] == calls0, "step one of the ladder costs no architect call"
        assert r["disp"].min_tier(r["jobs"].get(new["id"]), r["jobs"].get(new["id"])["steps"][0]) != "strong", "the first repair is a free-zone retry: it does not force the paid strong tier"
        r["jobs"].set_state(new["id"], "failed", "t", "boom again")
        sup.last.clear()
        sup.tick()
        s = it.get(sid)
        assert s["repairs"] == {"build": 2}
        new2 = r["jobs"].get(s["made"]["build"])
        assert new2["owner"] == "architect (repair)" and new2["meta"].get("free_zone"), new2["title"]               # the rewrite is also a free-zone retry now
        # a SPLIT: the architect says the job was too big; the pieces form a chain and everything that waited moves to the last piece
        other = r["jobs"].create("dev", "waits", "g", "general", "", dict(rubric="x"), None, 1, dict(depends_on_jobs=[new2["id"]]))
        s["blueprint"]["plan"]["jobs"][0]["goal"] += " SPLIT-ME"
        it.update(sid, lambda se: se.update(blueprint=s["blueprint"]))
        r["jobs"].set_state(new2["id"], "failed", "t", "too big")
        sup.last.clear()
        sup.tick()
        s = it.get(sid)
        last = r["jobs"].get(s["made"]["build"])
        assert s["repairs"] == {"build": 3} and "checks" in last["title"] and r["jobs"].meta(other["id"])["depends_on_jobs"] == [last["id"]], (s["repairs"], last["title"])
        first = next(x for x in r["jobs"].list(limit=40) if x["title"].endswith("geometry"))
        assert r["jobs"].meta(last["id"])["depends_on_jobs"] == [first["id"]], "the pieces run in order"
        r["jobs"].set_state(last["id"], "failed", "t", "no")
        sup.last.clear()
        sup.tick()
        assert it.get(sid)["repairs"] == {"build": 3}, "a job gets three repairs and then it is the person's"
        # the budget: past the cap the strong models are left alone
        app.usage.autonomy.save(strong_calls_per_session=1)
        ok, why = app.usage.allow_strong("claude-code", sid)
        assert not ok and "calls" in why
        app.usage.autonomy.save(strong_calls_per_session=30, strong_calls_per_day=1)
        assert not app.usage.allow_strong("claude-code", None)[0]
        assert not autonomy.disable(app)["enabled"] and json.load(open(os.path.join(app.data, "agency.json")))["auto_merge"] is False
        assert autonomy.trial_text("csv_report_service").startswith("Build and deliver a complete production-grade") and autonomy.trial_text("../etc") is None
    finally:
        done(r)
        shutil.rmtree(root, ignore_errors=True)


def test_a_failing_change_that_is_also_too_big_is_reworked_not_parked_for_the_owner():
    from agentdispatch.agency.gate import decide
    r = rig({"w": agent("answer", ["write", "plan", "review"]), "rv": agent("reject", ["review"], cost="plan")})
    try:
        j = r["jobs"].create("dev", "big", "g", "general", "", dict(rubric="x"))
        r["jobs"].add_steps(j["id"], [dict(objective="write", needs=["write"])])
        r["jobs"].set_state(j["id"], "reviewing", "t")
        r["jobs"].result_set(j["id"], check_ok=False, touched=["a.py"], diff_lines=1500)
        out = r["disp"]._decide_review(r["jobs"].get(j["id"]), "rv", dict(approve=False, reasons=["tests fail"]))
        assert out["action"] == "rework" and "split the work" in " ".join(out["reasons"]), out
        # everything fine except size: that IS the owner's
        j2 = r["jobs"].create("dev", "big2", "g", "general", "", dict(rubric="x"))
        r["jobs"].add_steps(j2["id"], [dict(objective="write", needs=["write"])])
        r["jobs"].set_state(j2["id"], "reviewing", "t")
        r["jobs"].result_set(j2["id"], check_ok=True, touched=["a.py"], diff_lines=1500)
        assert r["disp"]._decide_review(r["jobs"].get(j2["id"]), "rv", dict(approve=True, reasons=[]))["action"] == "blocked"
        # a protected file is always the owner's
        j3 = r["jobs"].create("dev", "prot", "g", "general", "", dict(rubric="x"))
        r["jobs"].add_steps(j3["id"], [dict(objective="write", needs=["write"])])
        r["jobs"].set_state(j3["id"], "reviewing", "t")
        r["jobs"].result_set(j3["id"], check_ok=False, touched=["agentdispatch/agency/gate.py"], diff_lines=10)
        assert r["disp"]._decide_review(r["jobs"].get(j3["id"]), "rv", dict(approve=False, reasons=["x"]))["action"] == "blocked"
    finally:
        done(r)


def test_reviewers_are_shown_whole_files_never_half_a_module_and_told_what_they_did_not_see():
    work, proj = tempfile.mkdtemp(prefix="dfw_"), tempfile.mkdtemp(prefix="dfp_")
    try:
        open(os.path.join(proj, "keep.py"), "w").write("x = 1\n")
        ws = Workspace.ensure(work, "J-D", proj)
        ws.write("small.py", "a = 1\nb = 2\n")
        ws.write("big.py", "".join("line_%d = %d\n" % (i, i) for i in range(5000)))
        text, cut = ws.diff_budget(6000)
        assert "small.py" in text and "line_5 " not in text and len(cut) == 1 and cut[0].startswith("big.py") and "50" in cut[0], (len(text), cut)
        text, cut = ws.diff_budget(600000)
        assert not cut and "line_4999" in text
        ad = RealAdapter(work, None)
        assert ad.review_budget(None) == 60000 and ad.review_budget(dict(cost="premium")) == 160000 and ad.review_budget(dict(cost="plan", run=dict(max_prompt_chars=24000))) == 16000
        job = dict(id="J-D", kind="code", target=proj, created=None, acceptance={}, goal="g", meta={}, steps=[])
        ad.workspace = lambda j: ws
        prompt = ad.review_request(job, {}, dict(cost="premium"))
        assert "line_4999" in prompt and "TOO LONG" not in prompt
        prompt = ad.review_request(job, {}, dict(cost="free"))
        assert "THESE FILES WERE TOO LONG" in prompt and "big.py" in prompt and "Do not approve or reject on what you could not see" in prompt
    finally:
        for x in (work, proj):
            shutil.rmtree(x, ignore_errors=True)


def test_the_model_chooser_uses_the_best_model_with_quota_learns_cooldowns_and_skips_dead_ones():
    r = rig({"gem": dict(agent("gemini", ["research", "write"]), run=dict(argv=[sys.executable, FAKE, "gemini"], stdin_prompt=True, timeout_s=60, models=["m1", "gone", "m2", "m3"]))})
    log = os.path.join(r["d"], "fake_models.log")
    os.environ["FAKE_LOG"] = log
    os.environ["FAKE_BAD"] = "m1"
    try:
        app = r["app"]
        er = {"gemini": [dict(key="m3", frac=0.99), dict(key="m2", frac=0.2)]}
        av = Availability(r["jobs"], fetch=lambda u: er, which=lambda b: "x")
        ad = RealAdapter(r["work"], None, book=av.book)
        gem = app.registry.agents["gem"]
        assert [m for m in av.book.usable(gem)] == ["m1", "gone", "m2"], "m3 is skipped before it fails: the engine room says it is 99% used"
        out, err = ad._ask(gem, "hello", None)
        assert not err and "answered by m2" in out and ad.last_model == "m2", (out, err)
        tried = open(log).read().split()
        assert tried == ["m1", "gone", "m2"], tried
        assert av.book.why_not(gem, "m1") and "cooling" in av.book.why_not(gem, "m1") and "does not exist" in av.book.why_not(gem, "gone")
        row = r["jobs"].db.execute("SELECT until FROM model_cooldowns WHERE agent='gem' AND model='m1'").fetchone()
        assert 1.9 * 3600 < row["until"] - time.time() < 2.3 * 3600, "the cooldown is the time the error text said (2h5m)"
        open(log, "w").write("")
        out, err = ad._ask(gem, "again", None)
        assert "answered by m2" in out and open(log).read().split() == ["m2"], "known-bad models are not tried again"
        assert av.check(gem)[0]
        # everything out: the agent is unavailable with the reasons, and a call says so as a usage limit
        os.environ["FAKE_BAD"] = "m1,m2"
        er["gemini"][1]["frac"] = 0.99
        assert not av.book.usable(gem) and not av.check(gem)[0] and "every model is out" in av.check(gem)[1]
        out, err = ad._ask(gem, "x", None)
        assert err.startswith("usage limit") and agentdispatch_detect(err)
        av.book.clear("gem")
        er["gemini"][1]["frac"] = 0.1
        os.environ["FAKE_BAD"] = ""
        assert av.check(gem)[0] and "answered by" in ad._ask(gem, "y", None)[0]
    finally:
        os.environ.pop("FAKE_LOG", None)
        os.environ.pop("FAKE_BAD", None)
        done(r)


def agentdispatch_detect(text):
    from agentdispatch.agency.adapters import detect_limit
    return detect_limit(text)


def test_a_stalled_job_is_said_on_the_board_once_and_the_plan_status_lists_every_job():
    from agentdispatch.intake import web_api as IW
    from agentdispatch.intake.autonomy import Supervisor
    r = rig({"w": agent("answer", ["write", "plan", "review"]), "rv": agent("approve", ["review"], cost="plan")})
    try:
        app = r["app"]
        it = IW.intake_of(app)
        sid = it.new(True)["id"]
        j = r["jobs"].create("dev", "slow job", "g", "general", "", dict(rubric="x"))
        r["jobs"].add_steps(j["id"], [dict(objective="write", needs=["write"])])
        it.update(sid, lambda se: se.update(status="submitted", made={"slow": j["id"]}))
        assert it.public(it.get(sid))["plan_status"]["slow"]["state"] == "running"
        sup = Supervisor(app)
        old = r["jobs"].get(j["id"])
        old["updated"] = time.time() - 3 * 3600
        sup.watch_stall(it.get(sid), "slow", old)
        sup.watch_stall(it.get(sid), "slow", old)
        posts = [p_ for p_ in app.board.read(limit=20)["posts"] if p_["title"].startswith("[stalled]")]
        assert len(posts) == 1, "said once"
        fresh = r["jobs"].get(j["id"])
        sup.watch_stall(it.get(sid), "other", fresh)
        assert len([p_ for p_ in app.board.read(limit=20)["posts"] if p_["title"].startswith("[stalled]")]) == 1, "a job that moved recently is not stalled"
    finally:
        done(r)


def test_agents_can_read_a_project_file_and_a_git_failure_says_why():
    d = tempfile.mkdtemp(prefix="rf_")
    box = __import__("agentdispatch.localtools", fromlist=["x"]).build(d, [d], os.path.join(d, "work"))
    try:
        box.reg.scan(force=True)
        open(os.path.join(d, "a.txt"), "w").write("x" * 20000)
        open(os.path.join(d, "b.bin"), "wb").write(b"\x00\x01\x02")
        r = box.run("read_file", file=os.path.join(d, "a.txt"))
        assert r["ok"] and len(r["stdout"]) == 12000 and "20000 characters" in r["note"]
        assert len(box.run("read_file", file=os.path.join(d, "a.txt"), code="12000")["stdout"]) == 8000
        assert not box.run("read_file", file=os.path.join(d, "b.bin"))["ok"] and not box.run("read_file", file=os.path.join(os.path.dirname(d), "x"))["ok"]
        r = box.run("git", args=["log", "-1"], cwd=d)
        assert not r["ok"] and "not a git repository" in r["problems"][0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_overnight_lessons_binary_files_nul_characters_a_failing_local_model_and_a_plan_without_a_strong_critic():
    from agentdispatch.agency.workspace import WorkspaceError
    from agentdispatch.intake import web_api as IW
    from agentdispatch.intake import autonomy
    r = rig({"claude-code": agent("vacuous", ["plan", "research"], cost="premium"), "ollama-local": agent("echo", ["code", "fast"], cost="local")})
    proj = tempfile.mkdtemp(prefix="nul_")
    root = tempfile.mkdtemp(prefix="nulroot_")
    try:
        ws = Workspace.ensure(r["work"], "J-X", proj)
        for bad in ("sample.wav", "x/model.STEP", "a.png"):
            try:
                ws.write(bad, "text")
                assert False, bad
            except WorkspaceError as e:
                assert "binary" in str(e)
        try:
            ws.write("a.txt", "has a \x00 inside")
            assert False
        except WorkspaceError:
            pass
        assert ws.write("a.txt", "plain text\n") >= 0
        # a local model whose last six runs all failed is left alone for a while, then tried again
        app = r["app"]
        for a in app.registry.agents.values():
            a["er_id"], a["binary"] = None, None
        agent_ = app.registry.agents["ollama-local"]
        agent_["kind"] = "local"
        av = Availability(r["jobs"], fetch=lambda u: IDLE, which=lambda b: "x")
        assert av.check(agent_)[0]
        for i in range(6):
            rid = r["jobs"].run_started("ollama-local", "J-X", 1)
            r["jobs"].run_done(rid, "failed")
        ok, why = av.check(agent_)
        assert not ok and "last 6 runs all failed" in why
        r["jobs"].run_done(r["jobs"].run_started("ollama-local", "J-X", 1), "ok")
        assert av.check(agent_)[0], "one success and it is trusted again"
        # a review that rejected the cheap agent's work is never redone at the cheap tier; a tool example needs the sandbox, not host files
        j = r["jobs"].create("dev", "t", "g", "general", "", dict(rubric="x"))
        r["jobs"].add_steps(j["id"], [dict(objective="write", needs=["write"])])
        r["jobs"].rework_step(j["id"], ["the check failed"])
        jj = r["jobs"].get(j["id"])
        assert r["disp"].min_tier(jj, jj["steps"][1]) == "mid" and r["disp"].min_tier(jj, jj["steps"][0]) == "ollama"
        r["jobs"].rework_step(j["id"], ["still failing"])
        jj = r["jobs"].get(j["id"])
        assert r["disp"].min_tier(jj, jj["steps"][2]) == "strong"
        from agentdispatch.intake import blueprint as BPm
        bpx = BPm.normalize(dict(title="t", summary="s", requirements=["a"], design=dict(tools_needed=[dict(name="stt-check", description="check numbers", examples=[dict(args=["samples/a.wav"], expect="5"), dict(args=["D:/x/y"], expect="1")])]),
                                 plan=dict(jobs=[dict(title="j", goal="g", kind="general", rubric="r", covers=["R1"])])))
        assert any("sandbox" in b for b in BPm.validate(bpx)["blockers"])
        # autonomous mode does not approve a plan that no strong second model attacked: it waits and retries
        autonomy.enable(app, root)
        it, arch = IW.intake_of(app), IW.architect_of(app)
        app.router.cfg.update(local_wait_s=1, local_timeout_s=10, check_vram=False)                    # the router is patient in production (waits for a free slot / GPU room, long timeouts); this test must not wait for the real local model
        arch.candidates = lambda exclude=(): [a for a in app.registry.agents.values() if a["id"] == "claude-code" and a["id"] not in exclude]          # only one strong model is available
        sid = it.new()["id"]
        txt = "A bracket. TARGET=%s/b" % root.replace("\\", "/")
        it.say(sid, "user", txt)
        arch.start(sid, "auto", txt).join(300)
        s = it.get(sid)
        assert s["phase"] == "waiting_review" and s["status"] == "open" and s["review_tries"] == 1 and s["critique"]["strong"] is False, (s["phase"], s["critique"])
        assert not any(x["title"].startswith("Build the bracket") for x in r["jobs"].list(limit=20)), "nothing was created without a strong second review"
        # a second strong model turns up: the supervisor retries the review and the plan goes ahead
        arch.candidates = lambda exclude=(): [a for a in app.registry.agents.values() if a["id"] == "claude-code" and a["id"] not in exclude] if "claude-code" not in exclude else [dict(app.registry.agents["claude-code"], id="codex-cli", name="Codex")]
        it.update(sid, lambda se: se.update(review_at=0))
        autonomy.Supervisor(app).tick(jobs=False)
        for _ in range(100):
            if it.get(sid)["status"] == "submitted":
                break
            time.sleep(0.5)
        assert it.get(sid)["status"] == "submitted" and it.get(sid)["critique"]["strong"] is True
    finally:
        done(r)
        for x in (proj, root):
            shutil.rmtree(x, ignore_errors=True)


# ---------------------------------------------------------------- web, MCP and the pages
def call(port, method, path, body=None, token=None):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Content-Type": "application/json", "Host": "127.0.0.1:%d" % port}
    if token:
        h["Authorization"] = "Bearer " + token
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def test_pages_endpoints_and_mcp_tools_with_the_right_owner_rules():
    d, work = tempfile.mkdtemp(prefix="campweb_"), tempfile.mkdtemp(prefix="campwork_")
    json.dump(dict(work_dir=work), open(os.path.join(d, "agency.json"), "w"))
    try:
        app = App(d, with_index=False)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent_t, owner = new_token(d, "codex-cli"), new_token(d, "dev", "owner")
        for path in ("/campaign", "/tools", "/library"):
            st, html = call(port, "GET", path)
            assert st == 200 and b'id="sbnav"' in html and b"AgentDispatch" in html, path
        assert call(port, "GET", "/api/tools")[1]["tools"] and call(port, "GET", "/api/campaign")[1]["templates"]
        # an agent token may run a tool and ask for installs; the owner's moves are the owner's
        st, r = call(port, "POST", "/api/tools/run", dict(tool="calc", code="print(6*7)"), agent_t)
        assert st == 200 and r["stdout"].strip() == "42"
        assert call(port, "POST", "/api/tools/run", dict(tool="calc", code="import os"), agent_t)[0] == 400
        assert call(port, "POST", "/api/tools/run", dict(tool="calc", code="print(1)"))[0] == 401
        for path, body in (("/api/campaign/load", dict(spec=json.load(open(os.path.join(ROOT, "agentdispatch", "campaign", "examples", "release_readiness.json"))))), ("/api/campaign/import", dict(campaign="C9", text=HANDOFF)),
                           ("/api/tools/config", dict(auto_install=["pip"])), ("/api/campaign/transition", dict(campaign="REL", gate="G06", to="CLOSED")), ("/api/tools/approve", dict(id="I-0001"))):
            assert call(port, "POST", path, body, agent_t)[0] == 403, path
        st, r = call(port, "POST", "/api/campaign/load", dict(spec=json.load(open(os.path.join(ROOT, "agentdispatch", "campaign", "examples", "release_readiness.json")))), owner)
        assert st == 200 and r["campaigns"][0]["next"] == "G06" and r["campaigns"][0]["counts"]["closed"] == 4, r["campaigns"][0]["counts"]
        st, r = call(port, "POST", "/api/jobs/from-template", dict(template="primary-closure", fields=dict(gate="G06", campaign="REL")), agent_t)
        assert st == 200 and r["meta"]["gate"] == "G06" and r["meta"]["human_approval"] == "before_promotion"
        st, r = call(port, "POST", "/api/campaign/propose", dict(campaign="REL", gate="G06", to="CANDIDATE"), agent_t)
        assert st == 200 and r["proposal"]["state"] == "pending"
        assert call(port, "POST", "/api/campaign/decide", dict(id=r["proposal"]["id"], approve=True), agent_t)[0] == 403
        st, r = call(port, "POST", "/api/campaign/decide", dict(id=r["proposal"]["id"], approve=True), owner)
        assert st == 200 and call(port, "GET", "/api/campaign")[1]["campaigns"][0]["gates"][4]["status"] == "CANDIDATE"
        mcp = lambda name, args, tok=agent_t: call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, tok)[1]["result"]["structuredContent"]
        assert mcp("tool_run", dict(tool="calc", code="print(2**10)"))["stdout"].strip() == "1024"
        assert mcp("tool_list", {})["ok"] and mcp("campaign_next", dict(campaign="REL"))["next"]["id"] == "G06"
        assert mcp("job_from_template", dict(template="evidence-seal", fields=dict(gate="G05-LP")))["ok"]
        assert mcp("playbook_search", dict(query="x"))["ok"] and mcp("playbook_failures", {})["ok"] and mcp("playbook_suggest", dict(goal="seal evidence"))["ok"]
        assert mcp("tool_need", dict(name="zz", description="a tool that does something that nothing here does at all, honestly"))["error"] == "bad_target"          # the build folder must be an allowed project folder
        json.dump(dict(allowed_roots=[d.replace("\\", "/")]), open(os.path.join(d, "nightshift.json"), "w"))
        assert mcp("tool_need", dict(name="zz", description="a tool that does something that nothing here does at all, honestly"))["ok"] is True
        st, r = call(port, "POST", "/api/jobs", dict(title="gov", goal="g", kind="general", meta=dict(campaign="REL", gate="G06", scope=dict(write=["a/**"]))), agent_t)
        assert st == 200 and call(port, "GET", "/api/jobs/" + r["job"]["id"])[1]["job"]["meta"]["scope"]["write"] == ["a/**"]
        st, r = call(port, "POST", "/api/jobs", dict(title="gov", goal="g", kind="general", meta=dict(role="nope")), agent_t)
        assert st == 400
        folder = tempfile.mkdtemp(prefix="allow_")
        assert call(port, "POST", "/api/agency/allow_root", dict(path=folder), agent_t)[0] == 403
        assert call(port, "POST", "/api/agency/allow_root", dict(path=os.path.join(folder, "nope")), owner)[0] == 400
        st, r = call(port, "POST", "/api/agency/allow_root", dict(path=folder), owner)
        assert st == 200 and folder.replace("\\", "/") in r["allowed_roots"] and folder.replace("\\", "/") in app.localtools.allowed_roots
        shutil.rmtree(folder, ignore_errors=True)
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- the intake
def test_intake_chat_fills_the_form_validates_everything_and_never_loses_the_persons_words():
    from agentdispatch.intake import chat
    form = dict(title="", goal="", kind="general", target="", check="", rubric="", priority=1, requirements=[], steps=[], template="", gate="", campaign="", notes="")
    new, changed, notes = chat.merge(form, dict(title="Wall bracket", goal="Design a fold-down bracket for a 45 kg compressor.", kind="code", check="rm -rf /", priority=9, requirements=["under 8 kg", "under 8 kg", "4 anchors"],
                                              steps=[dict(objective="draw it", needs=["code", "nonsense"]), "check loads"], template="primary-closure", evil="x", target="D:/p"))
    assert new["title"] == "Wall bracket" and new["kind"] == "code" and new["check"] == "" and new["priority"] == 1 and "evil" not in new and new["template"] == "", new
    assert new["requirements"] == ["under 8 kg", "4 anchors"] and new["steps"][0]["needs"] == ["code"] and new["steps"][1]["needs"] == ["research"] and notes and "check" not in changed
    new2, ch2, _ = chat.merge(new, dict(goal="Design a fold-down bracket for a 45 kg compressor. Load centre 420 mm from the wall.", check="python tools/acceptance.py"))
    assert "420 mm" in new2["goal"] and new2["check"] == "python tools/acceptance.py"                      # the goal grows; an allowed check is accepted
    new3, _, _ = chat.merge(new2, dict(goal="Also: wall is concrete."))
    assert new3["goal"].startswith(new2["goal"]) and "concrete" in new3["goal"], "a short addition is appended, not a replacement"
    assert chat.merge(dict(new3, campaign="C1"), dict(template="primary-closure"))[0]["template"] == "primary-closure"


def test_intake_end_to_end_chat_uploads_voice_analysis_and_submit():
    from agentdispatch.intake import media, web_api as IW
    d, work = tempfile.mkdtemp(prefix="intk_"), tempfile.mkdtemp(prefix="intkwork_")
    json.dump(dict(work_dir=work), open(os.path.join(d, "agency.json"), "w"))
    proj = tempfile.mkdtemp(prefix="intkproj_")
    open(os.path.join(proj, "acceptance.py"), "w").write("print('ok')\n")
    try:
        app = App(d, with_index=False)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tok = new_token(d, "dev", "owner")
        assert call(port, "GET", "/intake")[0] == 200 and b"Create the job" in call(port, "GET", "/intake")[1] and call(port, "POST", "/api/intake/session", {})[0] == 401
        sid = call(port, "POST", "/api/intake/session", {}, tok)[1]["session"]["id"]
        # a model answers: the form fills; then the router fails: the person's words are kept
        answers = [json.dumps(dict(reply="What load?", form=dict(title="Wall bracket", goal="Fold-down bracket for a 45 kg compressor.", requirements=["under 8 kg"])))]

        def fake_chat(req, actor="x"):
            assert req["response_format"]["type"] == "json_object" and req["messages"][-1]["content"].count("THE PERSON JUST SAID") == 1
            if not answers:
                raise RuntimeError("no model")
            return dict(choices=[dict(message=dict(content=answers.pop(0)))])
        app.router.chat = fake_chat
        st, r = call(port, "POST", "/api/intake/%s/chat" % sid, dict(text="I need a fold-down wall bracket for a 45 kg compressor, under 8 kg.", mode="local"), tok)
        assert st == 200 and r["session"]["form"]["title"] == "Wall bracket" and r["session"]["form"]["requirements"] == ["under 8 kg"] and "title" in r["changed"] and r["session"]["messages"][-1]["text"] == "What load?"
        st, r = call(port, "POST", "/api/intake/%s/chat" % sid, dict(text="The load centre is 420 mm from the wall.", mode="local"), tok)
        assert "420 mm" in r["session"]["form"]["goal"] and "Fold-down" in r["session"]["form"]["goal"] and "could not reach a model" in r["session"]["messages"][-1]["text"]
        # the person edits a field by hand, including clearing one
        st, r = call(port, "POST", "/api/intake/%s/form" % sid, dict(form=dict(title="Bracket", rubric="", requirements=["a", "b"], kind="code", target=proj.replace("\\", "/"), check="python acceptance.py")), tok)
        f = r["session"]["form"]
        assert f["title"] == "Bracket" and f["kind"] == "code" and f["requirements"] == ["a", "b"] and f["check"] == "python acceptance.py"
        # files: a text file is read, an audio file is transcribed (the speech model is stubbed here: it is tested for real separately), a voice note becomes a chat message
        import http.client

        def upload(name, data, mime="", action="upload"):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            c.request("POST", "/api/intake/%s/%s?name=%s&mime=%s" % (sid, action, name, mime), data, {"Authorization": "Bearer " + tok, "Host": "127.0.0.1:%d" % port})
            rr = c.getresponse()
            return rr.status, json.loads(rr.read())
        media.transcribe = lambda app_, path, language=None: ("the bracket folds down flat", "stubbed")
        st, r = upload("notes.txt", b"anchor holes are M12 on 100 mm centres")
        assert st == 200 and r["file"]["kind"] == "text"
        st, r = upload("clip.mp3", b"\x00" * 4000, "audio/mpeg")
        assert r["file"]["kind"] == "audio"
        for _ in range(50):
            s = call(port, "GET", "/api/intake/" + sid, token=tok)[1]["session"]
            if all(x["analysis"]["state"] in ("done", "failed", "waiting") for x in s["files"]):
                break
            time.sleep(0.2)
        assert "M12" in s["files"][0]["analysis"]["summary"] and "folds down flat" in s["files"][1]["analysis"]["summary"], s["files"]
        assert any("I looked at notes.txt" in m["text"] for m in s["messages"])
        st, fl = call(port, "GET", "/api/intake/%s/file/F1" % sid, token=tok)
        assert st == 200 and b"M12" in fl
        answers.append(json.dumps(dict(reply="Got it.", form=dict(goal="Fold-down bracket. Also folds down flat."))))
        st, r = upload("voice.webm&mode=local", b"\x01" * 3000, "audio/webm", "voice")
        assert st == 200 and r["session"]["messages"][-2]["text"] == "the bracket folds down flat" and r["session"]["messages"][-2]["role"] == "user"
        assert upload("huge.bin", b"x" * 10)[0] == 200
        # a folder that is not allowed: refused with a clear message; allowed: the job is created with its attachments recorded
        st, r = call(port, "POST", "/api/intake/%s/submit" % sid, {}, tok)
        assert st == 400 and "allowed project folders" in r["message"]
        assert call(port, "POST", "/api/agency/allow_root", dict(path=proj), tok)[0] == 200
        st, r = call(port, "POST", "/api/intake/%s/submit" % sid, {}, tok)
        assert st == 200, r
        j = app.jobs.get(r["job"]["id"])
        assert j["title"] == "Bracket" and "Requirements:" in j["goal"] and "notes.txt" in j["goal"] and j["acceptance"]["check"] == "python acceptance.py"
        assert os.path.isfile(os.path.join(app.campaign.evidence.job_dir(j["id"]), "inputs", "F1_notes.txt"))
        assert call(port, "POST", "/api/intake/%s/submit" % sid, {}, tok)[0] == 400                          # a second submit of the same intake is refused
        assert call(port, "POST", "/api/intake/%s/chat" % sid, dict(text="more"), tok)[0] in (200, 400)
        srv.shutdown()
        app.board.close()
    finally:
        for x in (d, work, proj):
            shutil.rmtree(x, ignore_errors=True)


if __name__ == "__main__":
    for f in (test_calculations_lean_git_and_controls_obey_the_policy, test_installs_are_requested_never_taken_and_only_from_short_lists, test_a_staged_pip_install_is_reviewed_in_isolation_before_it_is_used, test_job_governance_fields_are_validated_and_default_to_no_authority,
              test_campaign_state_imports_status_blocks_chains_every_change_and_never_moves_by_itself, test_a_job_waits_for_its_gates_and_a_result_bearing_job_waits_for_the_owner,
              test_a_tool_step_runs_on_this_computer_with_no_model_then_is_reviewed_sealed_and_proposed_never_promoted, test_a_failing_control_or_a_stop_rule_keeps_the_result_from_being_accepted,
              test_a_model_can_ask_the_computer_to_calculate_and_only_the_short_result_goes_back, test_scope_is_enforced_where_files_move_and_a_cutoff_hides_what_is_newer,
              test_a_model_cannot_confirm_itself_and_a_shadow_pair_is_compared_after_both_seal, test_an_open_premise_spawns_a_bounded_follow_up_job,
              test_playbooks_are_filed_searched_summarised_by_failure_and_carried_to_another_project, test_a_missing_tool_is_found_installed_or_built_and_a_built_one_joins_the_toolbox,
              test_a_production_job_with_a_folder_and_a_check_is_code_work_and_the_folder_must_be_allowed, test_intake_chat_fills_the_form_validates_everything_and_never_loses_the_persons_words, test_intake_end_to_end_chat_uploads_voice_analysis_and_submit, test_the_architect_interviews_studies_designs_plans_is_attacked_by_another_model_and_only_then_creates_jobs, test_accepted_changes_are_applied_to_the_real_folder_with_a_backup_and_undone_when_the_check_fails, test_autonomous_mode_red_before_green_budget_and_repair_of_a_failed_job, test_a_failing_change_that_is_also_too_big_is_reworked_not_parked_for_the_owner, test_reviewers_are_shown_whole_files_never_half_a_module_and_told_what_they_did_not_see, test_the_model_chooser_uses_the_best_model_with_quota_learns_cooldowns_and_skips_dead_ones, test_a_stalled_job_is_said_on_the_board_once_and_the_plan_status_lists_every_job, test_agents_can_read_a_project_file_and_a_git_failure_says_why, test_overnight_lessons_binary_files_nul_characters_a_failing_local_model_and_a_plan_without_a_strong_critic, test_pages_endpoints_and_mcp_tools_with_the_right_owner_rules):
        f()
        print("ok", f.__name__)
    print("ALL CAMPAIGN TESTS PASSED")
