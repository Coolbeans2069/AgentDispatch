"""routing inside an agent: which model and how much thinking a task gets, by difficulty, retries and what is left of each quota"""
import json, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_campaign import rig, done, agent, FAKE, IDLE
from agentdispatch.agency.availability import Availability
from agentdispatch.agency.realadapters import RealAdapter
from agentdispatch.agency.registry import DEFAULTS
from agentdispatch.agency import routing


def mk(er=None, data=None):
    from agentdispatch.agency.vram import VramWatch
    r = rig({})
    snap = er if er is not None else IDLE
    av = Availability(r["jobs"], fetch=lambda u: snap, which=lambda b: "x", data_dir=data or r["d"], vram=VramWatch(fetch=lambda *a, **k: {}, smi=lambda: []))     # tests have no GPUs: no opinion
    return r, av


def test_an_easy_task_gets_the_cheap_model_and_low_effort_a_hard_or_retried_one_gets_more():
    r, av = mk()
    cl = dict(DEFAULTS["claude-code"], id="claude-code")
    easy = av.router.pick(cl, 0.25, "step")
    assert (easy["model"], easy["effort"]) == ("haiku", "low"), easy
    mid = av.router.pick(cl, 0.5, "step")
    assert mid["model"] == "sonnet" and mid["effort"] in ("low", "medium"), mid
    hard = av.router.pick(cl, 0.9, "code")
    assert (hard["model"], hard["effort"]) == ("opus", "high"), hard
    again = av.router.pick(cl, 0.25, "step", attempts=3)                            # failed three times: a stronger rung, never the same cheap one
    assert again["skill"] > easy["skill"] + 0.3, again
    cx = dict(DEFAULTS["codex-cli"], id="codex-cli")
    assert av.router.pick(cx, 0.2)["model"] == "gpt-6-luna" and av.router.pick(cx, 0.2)["effort"] == "low"
    assert av.router.pick(cx, 0.95)["model"] == "gpt-6-astra"
    gk = dict(DEFAULTS["grok-cli"], id="grok-cli")
    assert av.router.pick(gk, 0.2)["model"] == "grok-4.5" and av.router.pick(gk, 0.85)["model"] == "grok-4.7"
    done(r)


def test_gemini_uses_flash_lite_for_easy_work_and_moves_to_the_next_model_on_a_429_with_the_real_reset_time():
    r, av = mk(er=dict(IDLE, gemini=[dict(key="gemini-3.8-flash", frac=0.0), dict(key="gemini-3-flash-preview", frac=0.0)]))
    gem = dict(DEFAULTS["gemini-cli"], id="gemini-cli")
    assert av.router.pick(gem, 0.2)["model"] == "gemini-3.1-flash-lite"
    assert av.router.pick(gem, 0.8)["model"] == "gemini-3.8-flash"
    until = av.book.cool(gem, "gemini-3.8-flash", "429 You exceeded your current quota. Please retry in 20h37m.")
    assert 20 * 3600 < until - time.time() < 21.5 * 3600, "the cooldown is the reset time the error gave"
    nxt = av.router.pick(gem, 0.8)
    assert nxt["model"] == "gemini-3-flash-preview", nxt
    assert [x["model"] for x in av.router.order(gem, 0.8)][0] == "gemini-3-flash-preview"
    done(r)


def test_a_nearly_used_model_or_plan_costs_more_and_a_stale_reading_counts_as_fuller():
    full = dict(IDLE, gemini=[dict(key="gemini-3.5-flash-lite", frac=0.9), dict(key="gemini-3.1-flash-lite", frac=0.1)])
    r, av = mk(er=full)
    gem = dict(DEFAULTS["gemini-cli"], id="gemini-cli")
    assert av.router.pick(gem, 0.3)["model"] == "gemini-3.1-flash-lite"
    done(r)
    stale_er = dict(IDLE, limits=dict(grokcli=dict(windows=[dict(pct=55, resets_in_min=9000)], stale=True, age_min=900)))
    fresh_er = dict(IDLE, limits=dict(grokcli=dict(windows=[dict(pct=55, resets_in_min=9000)], stale=False, age_min=2)))
    gk = dict(DEFAULTS["grok-cli"], id="grok-cli")
    r, av = mk(er=stale_er)
    u_stale, st = av.router.used(gk, av.router.ladder(gk)[0])
    done(r)
    r, av = mk(er=fresh_er)
    u_fresh, st2 = av.router.used(gk, av.router.ladder(gk)[0])
    done(r)
    assert st and not st2 and u_stale > u_fresh + 0.2, (u_stale, u_fresh)


def test_the_cli_is_started_with_the_chosen_model_and_effort_and_the_choice_is_on_the_route_event():
    log = os.path.join(tempfile.gettempdir(), "route_argv_%d.log" % os.getpid())
    open(log, "w").close()
    os.environ["FAKE_LOG"] = log
    try:
        r, av = mk()
        for aid, flagwords in (("claude-code", "--model haiku --effort low"), ("codex-cli", "-m")):
            a = dict(DEFAULTS[aid], id=aid, run=dict(argv=[sys.executable, FAKE, "argv"], stdin_prompt=True, timeout_s=60))
            ad = RealAdapter(r["work"], None, book=av.book)
            ad.hint = dict(difficulty=0.2, kind="step", attempts=0)
            out, err = ad._ask(a, "hi", None)
            assert not err, err
            line = open(log).read().strip().splitlines()[-1]
            if aid == "claude-code":
                assert flagwords in line, line
                assert ad.last_model == "haiku@low", ad.last_model
            else:
                assert 'model_reasoning_effort="low"' in line and "-m gpt-6-luna" in line, line
        assert av.router.argv_for(dict(id="grok-cli"), dict(model=None, effort="high")) == ["--reasoning-effort", "high"]
        assert av.router.argv_for(dict(id="grok-cli"), dict(model="grok-4.7", effort=None)) == ["-m", "grok-4.7"]
        done(r)
    finally:
        os.environ.pop("FAKE_LOG", None)


def test_the_routing_table_and_the_owners_config_override():
    r, av = mk()
    rows = av.router.table(av.jobs and {a: dict(DEFAULTS[a], id=a) for a in ("claude-code", "codex-cli", "grok-cli", "gemini-cli")})
    assert len(rows) == 4 * 4 * 4
    pick = {(x["agent"], x["kind"], x["difficulty"]): (x["model"], x["effort"]) for x in rows}
    assert pick[("claude-code", "step", "easy")] == ("haiku", "low") and pick[("claude-code", "step", "hardest")] == ("opus", "high")
    json.dump(dict(ladders={"claude-code": [dict(model="only", effort="max", skill=0.99, weight=1)]}, retry_bump=0.5), open(os.path.join(r["d"], "routing.json"), "w"))
    av.router.reload()
    cl = dict(DEFAULTS["claude-code"], id="claude-code")
    assert av.router.pick(cl, 0.1)["model"] == "only" and av.router.cfg["retry_bump"] == 0.5 and av.router.cfg["scarcity"] == routing.DEFAULTS["scarcity"]
    done(r)


def test_the_brain_survives_odd_rows_and_a_500_says_what_broke_and_where():
    from agentdispatch import web
    from agentdispatch.agency import brain
    r, av = mk()
    app = r["app"]
    app.availability = av
    snap = brain.snapshot(app, 0, 0, None)
    assert snap.get("ok", True)
    try:
        {}["job_id"]
    except KeyError as e:
        err = web.server_error(e, "/api/brain")
    assert err["error"] == "server_error" and "KeyError" in err["message"] and "job_id" in err["message"] and err["where"].startswith("test_routing.py:"), err
    assert web.fresh(brain) is brain
    done(r)


def test_a_near_duplicate_job_is_refused_for_a_hand_submit_but_not_for_the_systems_own_retries():
    from agentdispatch.store import BoardError
    r, av = mk()
    jobs = r["jobs"]
    a = jobs.create("owner", "Write the parser", "Write a parser for the config format with tests and error messages", "general")
    try:
        jobs.create("owner", "Write the parser", "write a parser for the config format with tests and error messages please", "general", dedupe=True)
        assert False, "the same work twice"
    except BoardError as e:
        assert e.code == "duplicate" and a["id"] in str(e)
    assert jobs.create("owner", "Write the parser", "Write a parser for the config format with tests and error messages", "general")["id"] != a["id"], "the system's own creators do not ask"
    other = jobs.create("owner", "Draw the logo", "Make an SVG logo with a blue brain", "general", dedupe=True)
    assert other["id"]
    jobs.set_state(a["id"], "cancelled", "owner", "x")
    jobs.set_state(jobs.find_duplicate("Write the parser", "Write a parser for the config format with tests and error messages", "", "general"), "cancelled", "owner", "x")
    assert jobs.create("owner", "Write the parser", "Write a parser for the config format with tests and error messages", "general", dedupe=True)["id"], "a finished or cancelled job is not a duplicate"
    done(r)


def test_each_gemini_call_is_reported_to_the_engine_room_and_a_dead_engine_room_never_blocks():
    sent = []
    r = rig({})
    av = Availability(r["jobs"], fetch=lambda u: IDLE, which=lambda b: "x", data_dir=r["d"], poster=lambda url, body: sent.append((url, body)))
    d = r["disp"]
    d.av = av
    d.adapter.last_usage = dict(model="gemini-3.5-flash-lite", chars_in=4000, chars_out=800)
    d.adapter.last_model = "gemini-3.5-flash-lite"
    job = dict(id="J-1")
    d._model_done(job, "step", 1, dict(id="gemini-cli", er_id="gemini"), time.time() - 2, "ok")
    assert sent and sent[0][0].endswith("/llm") and sent[0][1]["backend"] == "gemini" and sent[0][1]["model"] == "gemini-3.5-flash-lite" and sent[0][1]["tokens"] == 1200, sent
    assert d.adapter.last_usage is None
    n = len(sent)
    d.adapter.last_usage = dict(model="haiku", chars_in=10, chars_out=10)                         # not a Gemini agent: nothing is posted
    d._model_done(job, "step", 1, dict(id="claude-code", er_id="claude"), time.time(), "ok")
    assert len(sent) == n
    av2 = Availability(r["jobs"], fetch=lambda u: IDLE, which=lambda b: "x", poster=lambda url, body: (_ for _ in ()).throw(OSError("engine room is down")))
    assert av2.report("gemini", "m", 5) is False
    done(r)


def test_an_acceptance_script_that_is_not_in_the_project_is_refused_when_the_job_is_made():
    import tempfile
    from agentdispatch.store import BoardError
    r = rig({})
    proj = tempfile.mkdtemp(prefix="chk_")
    r["jobs"].target_ok = lambda t: True
    try:
        try:
            r["jobs"].create("owner", "Build x", "Build the x module", "code", proj, dict(check="python check_x.py"))
            assert False, "the script does not exist"
        except BoardError as e:
            assert e.code == "no_check_script" and "check_x.py" in str(e)
        open(os.path.join(proj, "check_x.py"), "w").write("print(1)")
        assert r["jobs"].create("owner", "Build x", "Build the x module", "code", proj, dict(check="python check_x.py"))["id"]
    finally:
        import shutil
        shutil.rmtree(proj, ignore_errors=True)
    done(r)


def test_a_task_includes_the_files_the_check_imports_and_a_retry_is_told_what_failed():
    import tempfile, shutil
    from agentdispatch.agency.workspace import Workspace
    from agentdispatch.agency import prompts
    work, proj = tempfile.mkdtemp(prefix="ctw_"), tempfile.mkdtemp(prefix="ctp_")
    try:
        def put(n, t):
            open(os.path.join(proj, n), "w").write(t)
        put("check.py", "import helper\nimport json\nprint(open('schema.json').read())\n")
        put("helper.py", "X = 1\n")
        put("schema.json", "{}")
        for i in range(30):
            put("a%d.py" % i, "v = %d\n" % i)
        ws = Workspace.ensure(work, "J-C", proj)
        _, picked = ws.context("do it", 80, priority=["check.py"])
        names = [x for x, _ in picked]
        assert set(names[:3]) == {"check.py", "schema.json", "helper.py"}, names
    finally:
        for x in (work, proj):
            shutil.rmtree(x, ignore_errors=True)
    job = dict(goal="g", kind="general", acceptance={}, meta={})
    p = prompts.step_prompt(job, dict(objective="o", attempts=1, evidence="FAILED (x, attempt 1): bad json"), [], [])
    assert "PREVIOUS ATTEMPT FAILED (x, attempt 1): bad json" in p
    assert "PREVIOUS ATTEMPT" not in prompts.step_prompt(job, dict(objective="o", attempts=0, evidence=None), [], [])


def test_local_code_goes_to_the_coding_model_and_an_agent_that_keeps_failing_a_kind_of_task_goes_last():
    r, av = mk()
    seen = []
    ad = RealAdapter(r["work"], lambda prompt, model=None: seen.append(model) or ("{}", model), book=av.book)
    loc = dict(DEFAULTS["ollama-local"], id="ollama-local")
    ad.hint = dict(difficulty=0.3, kind="code", attempts=0)
    ad._ask(loc, "x", None)
    ad.hint = dict(difficulty=0.3, kind="write", attempts=0)
    ad._ask(loc, "x", None)
    assert seen == [av.router.local_model("code"), None], seen          # the local model per kind of task comes from the routing config
    rt = av.router
    for ok in (False, False, False, True, False):
        rt.record("ollama-local", "code", ok)
    assert rt.unreliable("ollama-local", "code") and not rt.unreliable("ollama-local", "step")
    assert not rt.unreliable("codex-cli", "code"), "too few samples to judge"
    done(r)


def test_a_local_model_works_as_an_agent_in_its_own_folder_and_what_it_must_not_touch_is_put_back():
    import tempfile, shutil
    from agentdispatch.agency.workspace import Workspace
    r, av = mk()
    agent_cfg = dict(DEFAULTS["ollama-agent"], id="ollama-agent")
    for mode, ok in (("agentic_fix", True), ("agentic_cheat", False), ("agentic_idle", False)):
        work, proj = tempfile.mkdtemp(prefix="agw_"), tempfile.mkdtemp(prefix="agp_")
        try:
            open(os.path.join(proj, "calc.py"), "w").write("def average(xs):\n    return sum(xs) / (len(xs) - 1)\n")
            open(os.path.join(proj, "test_calc.py"), "w").write("from calc import average\nassert average([2, 4, 6]) == 4\n")
            ws = Workspace.ensure(work, "J-A", proj)
            ad = RealAdapter(work, None, book=av.book)
            ad.workspace = lambda j: ws
            ad.digest = lambda j: "# digest\n- J-9 running"
            a = dict(agent_cfg, run=dict(via="agentic", timeout_s=60, argv=[sys.executable, FAKE, mode]))
            job = dict(id="J-A", kind="code", target=proj, created=None, goal="fix average", acceptance=dict(check="python test_calc.py"), meta={}, steps=[])
            res = ad.run(a, dict(n=1, objective="fix the bug", needs=["code"], attempts=0), job)
            assert res.ok is ok, (mode, res.error, res.text)
            if mode == "agentic_fix":
                assert res.touched == ["calc.py"] and res.diff_lines >= 2 and ad.last_model == "qwen3-coder:30b", (res.touched, res.diff_lines, ad.last_model)
            if mode == "agentic_cheat":
                assert "test_calc.py" in res.error and "reverted" in res.error
                assert "assert average" in open(os.path.join(ws.project, "test_calc.py")).read(), "the test was put back"
            if mode == "agentic_idle":
                assert "without changing any file" in res.error
        finally:
            for x in (work, proj):
                shutil.rmtree(x, ignore_errors=True)
    from agentdispatch.agency.agentic import AgenticRunner
    from agentdispatch.agency.capabilities import Capabilities
    ALLOWED_FLAG = Capabilities(r["d"]).allowed_tools_flag()
    ALLOWED = ALLOWED_FLAG
    cmd = AgenticRunner().command("qwen3-coder:30b", 65536, "D:/x/proj", "D:/x/board", "n1", "tok123", ALLOWED_FLAG)
    assert cmd[:3] == ["docker", "run", "--rm"] and "--cap-drop" in cmd and "-v" in cmd and ":/board:ro" in " ".join(cmd) and ":/work" in " ".join(cmd) and "qwen3-coder:30b" in cmd
    assert not any("TOKEN=sb_" in c or "KEY=sk" in c for c in cmd), "no secrets in the container"
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536" in cmd and "NET_ADMIN" in cmd and "--strict-mcp-config" in cmd and "mcp__agentdispatch" in ALLOWED_FLAG
    assert "Bash(curl" not in ALLOWED and "Bash(pip" not in ALLOWED
    done(r)


def test_the_conductor_swaps_models_by_itself_but_only_within_what_is_real_and_safe():
    from agentdispatch.agency.conductor import Conductor
    r, av = mk()
    app = r["app"]
    app.availability = av
    app.registry.agents["ollama-agent"] = dict(DEFAULTS["ollama-agent"], id="ollama-agent")
    app.registry.agents["claude-code"] = dict(DEFAULTS["claude-code"], id="claude-code")
    fetch = lambda u: dict(models=[dict(name="qwen3-coder:30b", size=19e9), dict(name="nemotron-cascade-2:latest", size=36e9)]) if "tags" in u else dict(models=[dict(name="qwen3-coder:30b")])
    asked = []
    good = json.dumps(dict(swaps=[
        dict(op="set_ladder", agent="ollama-agent", rungs=[dict(model="nemotron-cascade-2:latest", effort=None, skill=0.7, weight=1), dict(model="qwen3-coder:30b", effort=None, skill=0.6, weight=2)], why="nemotron passes more"),
        dict(op="set_local", kind="code", model="qwen3-coder:30b", why="coder model"),
        dict(op="hold_model", agent="claude-code", model="opus", hours=2, why="save the strong model"),
        dict(op="set_ladder", agent="ollama-agent", rungs=[dict(model="made-up:1b", effort=None, skill=0.5, weight=1)], why="invented model"),
        dict(op="set_ladder", agent="claude-code", rungs=[dict(model="haiku", effort="low", skill=0.35, weight=1)], why="drops everything strong"),
        dict(op="delete_everything"),
    ], notes="x"))
    json.dump(dict(apply="auto", require_valid=False), open(os.path.join(app.data, "conductor.json"), "w"))   # the old partial-apply behaviour, now opt-in
    cond = Conductor(app, lambda p: (asked.append(p), good)[1], fetch=fetch)
    out = cond.step(force=True)
    assert out["action"] == "decided" and out["accepted"] == 3 and out["rejected"] == 3, out
    assert "SNAPSHOT" in asked[0] and "qwen3-coder:30b" in asked[0] and "ollama-agent" in asked[0]
    ol = av.router.ladder(app.registry.agents["ollama-agent"])
    assert ol[0]["model"] == "nemotron-cascade-2:latest" and ol[0]["weight"] == 1, ol
    assert av.router.local_model("code") == "qwen3-coder:30b"
    assert av.book.why_not(app.registry.agents["claude-code"], "opus") and "conductor" in av.book.why_not(app.registry.agents["claude-code"], "opus") or "cooling" in (av.book.why_not(app.registry.agents["claude-code"], "opus") or "")
    assert not av.router.pick(dict(app.registry.agents["claude-code"]), 0.97) or av.router.pick(app.registry.agents["claude-code"], 0.97)["model"] != "opus", "a held model is not chosen"
    from agentdispatch.agency.routing import Router
    other = Router(app.data, av.book)                                           # another process (the daemon) reads the same overrides
    assert other.ladder(app.registry.agents["ollama-agent"])[0]["model"] == "nemotron-cascade-2:latest"
    log = open(os.path.join(app.data, "conductor_log.jsonl")).read()
    assert '"kind": "decision"' in log and "made-up:1b" in log
    assert cond.step()["action"] == "not_due"
    open(os.path.join(app.data, "conductor_off"), "w").write("")
    assert cond.step(force=True)["action"] == "off"
    os.remove(os.path.join(app.data, "conductor_off"))
    json.dump(dict(max_calls_per_day=1), open(os.path.join(app.data, "conductor.json"), "w"))
    assert cond.step(force=True)["action"] == "budget"
    done(r)


def test_the_gateway_is_the_only_door_a_container_agent_has_and_the_capability_list_can_grow():
    import http.client, tempfile, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from agentdispatch.agency.gateway import Gateway
    r, av = mk()
    app = r["app"]
    got = []

    class Fake(BaseHTTPRequestHandler):                                                 # a stand-in for Ollama
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.path, json.loads(self.rfile.read(n))))
            b = b'{"type":"message","content":[{"type":"text","text":"hi"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    fake = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    proj = tempfile.mkdtemp(prefix="gwp_")
    gw = Gateway(app, app.capabilities, port=0, ollama="http://127.0.0.1:%d" % fake.server_address[1]).start()
    try:
        tok = gw.open_run("J-1", "ollama-agent", "qwen3-coder:30b-ctx64k", proj)

        def post(path, body, t=None):
            c = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=20)
            c.request("POST", "/g/%s/%s" % (t or tok, path), json.dumps(body), {"Content-Type": "application/json"})
            resp = c.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")

        def rpc(method, params=None):
            return post("mcp", dict(jsonrpc="2.0", id=1, method=method, params=params or {}))[1]
        # its model: whatever it asks for, it gets the one the router chose; other Ollama routes are closed
        st, body = post("ollama/v1/messages", dict(model="claude-haiku-4", messages=[dict(role="user", content="x")]))
        assert st == 200 and got[-1][0] == "/v1/messages" and got[-1][1]["model"] == "qwen3-coder:30b-ctx64k", got
        assert post("ollama/api/tags", {})[0] == 404 and post("ollama/api/pull", {})[0] == 404
        assert post("ollama/v1/messages", {}, "not-a-token")[0] == 401
        # the broker's tools, filtered by the capability list
        names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
        assert {"board_search", "tool_run", "playbook_suggest", "capability_request"} <= names and not names & {"job_submit", "agency_next", "job_cancel", "board_resolve"}, sorted(names)
        res = rpc("tools/call", dict(name="job_submit", arguments=dict(title="t", goal="g")))["result"]
        assert res["isError"] and "not_allowed" in res["content"][0]["text"]
        ok = rpc("tools/call", dict(name="board_search", arguments=dict(query="anything")))
        assert "result" in ok and not ok["result"].get("isError")
        # tool_run is pinned to the run's own project and job
        bad = rpc("tools/call", dict(name="tool_run", arguments=dict(tool="git", args=["status"], cwd="/etc")))["result"]
        assert bad["isError"] and "bad_path" in bad["content"][0]["text"]
        bad = rpc("tools/call", dict(name="tool_run", arguments=dict(tool="git", args=["status"], cwd="../..")))["result"]
        assert bad["isError"]
        run = gw.runs[tok]
        pinned, err = gw.pin_tool_args(run, "tool_run", dict(tool="pytest", cwd="/work/sub", job_id="J-OTHER"))
        assert not err and pinned["cwd"] == os.path.join(os.path.realpath(proj), "sub") and pinned["job_id"] == "J-1", pinned
        # the list grows: a safe request is added at once, a risky one waits for the owner, a forbidden one is refused
        r1 = rpc("tools/call", dict(name="capability_request", arguments=dict(kind="shell", value="mypy:*", why="type check")))
        assert json.loads(r1["result"]["content"][0]["text"])["status"] == "already"
        caps = app.capabilities
        assert caps.request("shell", "black:*", "format", "agent:x")["status"] == "added" and "Bash(black:*)" in caps.allowed_tools_flag()
        assert caps.request("shell", "curl:*", "download", "agent:x")["status"] == "refused"
        assert caps.request("mcp_tool", "job_submit", "make jobs", "agent:x")["status"] == "refused"
        assert caps.request("mcp_tool", "no_such_tool", "x", "agent:x")["status"] == "refused"
        assert caps.request("context", "digest", "x", "a")["status"] == "already"
        # the run ends: the token stops working
        gw.close_run(tok)
        assert post("ollama/v1/messages", {})[0] == 401
    finally:
        gw.stop()
        fake.shutdown()
        import shutil
        shutil.rmtree(proj, ignore_errors=True)
    done(r)


def test_the_agent_gets_a_pinned_window_and_a_context_pack_with_the_job_the_board_and_the_formats():
    import shutil
    from agentdispatch.agency.agentic import AgentEnv
    r, av = mk()
    app = r["app"]
    calls = []

    made = ["qwen3-coder:30b"]

    def fetch(url, body=None, timeout=60):
        calls.append((url.rsplit("/", 1)[-1], body))
        if url.endswith("/api/create"):
            made.append(body["model"])
        return dict(models=[dict(name=m) for m in made]) if url.endswith("/api/tags") else {}
    env = AgentEnv(app, None, app.capabilities, cfg=lambda: dict(default=65536, models={"nemotron-cascade-2:latest": 32768}), fetch=fetch)
    name, ctx = env.variant("qwen3-coder:30b")                                       # BASE-ctxNk:TAG, so the tag stays a tag (":latest-ctx32k" was an "unrecognized_model")
    create = [c for c in calls if c[0] == "create"][-1]
    assert (name, ctx) == ("qwen3-coder-ctx64k:30b", 65536) and create[1]["model"] == name and create[1]["parameters"] == dict(num_ctx=65536) and create[1]["from"] == "qwen3-coder:30b"
    assert env.variant("nemotron-cascade-2:latest") == ("nemotron-cascade-2-ctx32k:latest", 32768)
    n = len(calls)
    assert env.variant("qwen3-coder:30b")[0] == name and not [c for c in calls[n:] if c[0] == "create"], "made once"
    made[:] = ["qwen3-coder:30b"]
    def no_create(url, body=None, timeout=60):                                       # Ollama did not create it: the plain model runs, never a name that does not exist
        return dict(models=[dict(name="qwen3-coder:30b")]) if url.endswith("/api/tags") else {}
    env.fetch = no_create
    assert env.variant("qwen3-coder:30b") == ("qwen3-coder:30b", 65536)
    env.fetch = fetch
    d = env.pack(dict(id="J-7", title="t", goal="fix it", kind="code", acceptance=dict(check="python check.py"), meta=dict(campaign="c26", gate="G1", expected_outputs=["out.json"])),
                 dict(n=2, objective="o", needs=["code"], attempts=1), "FAILED (x): bad", "# digest\n- J-9")
    try:
        files = set(os.listdir(d))
        assert {"BRIEF.md", "job.json", "digest.md", "tools.md", "schemas.md", "playbooks.md"} <= files, files
        j = json.load(open(os.path.join(d, "job.json")))
        assert j["id"] == "J-7" and j["previous_failure"] == "FAILED (x): bad" and j["expected_outputs"] == ["out.json"] and j["meta"]["gate"] == "G1" and j["acceptance"]["check"] == "python check.py"
        assert "capability_request" in open(os.path.join(d, "BRIEF.md")).read() and "INDEPENDENTLY_CONFIRMED" in open(os.path.join(d, "schemas.md")).read()
        assert "calc" in open(os.path.join(d, "tools.md")).read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    done(r)


def test_a_local_model_is_offered_only_if_it_fits_the_free_gpu_memory_and_its_window_shrinks_before_it_is_refused():
    from agentdispatch.agency.vram import VramWatch
    show = {"qwen3moe.block_count": 48, "qwen3moe.attention.head_count": 32, "qwen3moe.attention.head_count_kv": 4, "qwen3moe.embedding_length": 2048, "qwen3moe.attention.key_length": 128}
    state = dict(gpus=[(0, 1.0, 16.0), (1, 1.0, 16.0), (2, 1.0, 16.0)], ps=[])          # 45 GB free over three GPUs

    def fetch(url, timeout=4, body=None):
        if url.endswith("/api/tags"):
            return dict(models=[dict(name="qwen3-coder:30b", size=18.6e9), dict(name="tiny:1b", size=1.5e9), dict(name="huge:120b", size=65e9)])
        if url.endswith("/api/ps"):
            return dict(models=state["ps"])
        if url.endswith("/api/show"):
            return dict(model_info=show)
        raise OSError(url)
    w = VramWatch(fetch=fetch, smi=lambda: state["gpus"], ttl=0)
    kv64 = w.kv_gb("qwen3-coder:30b-ctx64k", 65536)
    assert 6.0 < kv64 < 6.8, kv64                                                       # 2 x 48 layers x 4 kv heads x 128 x 65536 x 2 bytes
    ok, ctx, need, avail, why = w.plan("qwen3-coder:30b", 65536)
    assert ok and ctx == 65536 and 25 < need < 31 and not why, (ok, ctx, need, avail, why)
    state["gpus"] = [(0, 9.0, 16.0), (1, 9.0, 16.0)]                                   # 14 GB free: the window has to shrink, and even then the 30B does not fit
    ok, ctx, need, avail, why = w.plan("qwen3-coder:30b", 65536)
    assert not ok and "only" in why and ctx == 32768, (ok, ctx, why)
    assert w.plan("tiny:1b", 65536)[0], "a small model still fits"
    state["gpus"] = [(0, 6.0, 16.0), (1, 6.0, 16.0), (2, 6.0, 16.0)]                   # 30 GB free: the full window does not fit, a smaller one does
    ok, ctx, need, avail, why = w.plan("qwen3-coder:30b", 131072)
    assert ok and ctx < 131072 and "cut" in why, (ok, ctx, why)
    state["gpus"] = [(0, 15.0, 16.0), (1, 15.0, 16.0)]                                 # a loaded Ollama model with the window already counts as room (and needs nothing more)
    state["ps"] = [dict(name="qwen3-coder:30b-ctx64k", size_vram=30e9, context_length=65536)]
    assert w.fit("qwen3-coder:30b-ctx64k", 65536)[0] and w.fit("qwen3-coder:30b-ctx64k", 65536)[1] == 0.0
    state["ps"] = []
    nogpu = VramWatch(fetch=fetch, smi=lambda: [], ttl=0)                               # cannot read the GPUs: no opinion, never blocks
    assert nogpu.fit("huge:120b", 131072)[0]
    # the book hides a model that does not fit from the agent, with the reason
    r, av = mk()
    av.vram = VramWatch(fetch=fetch, smi=lambda: [(0, 15.0, 16.0), (1, 15.0, 16.0)], ttl=0)
    ag = dict(DEFAULTS["ollama-agent"], id="ollama-agent")
    why = av.book.why_not(ag, "qwen3-coder:30b")
    assert why and "GPU memory" in why, why
    assert "qwen3-coder:30b" not in [x["model"] for x in av.router.order(ag, 0.5)], "the model that does not fit is not offered (a model the poll does not know is not blocked)"
    av.router.cfg["ladders"]["ollama-agent"] = [x for x in av.router.cfg["ladders"]["ollama-agent"] if x["model"] == "qwen3-coder:30b"]
    import agentdispatch.nightshift as NS
    keep, NS.docker_ready = NS.docker_ready, (lambda image: True)                      # this test is about GPU room, not whether Docker Desktop happens to be running
    try:
        ok, reason = av.check(ag)
    finally:
        NS.docker_ready = keep
    assert not ok and "every model is out" in reason and "GPU memory" in reason, reason
    done(r)


if __name__ == "__main__":
    for f in (test_an_easy_task_gets_the_cheap_model_and_low_effort_a_hard_or_retried_one_gets_more, test_gemini_uses_flash_lite_for_easy_work_and_moves_to_the_next_model_on_a_429_with_the_real_reset_time,
              test_a_nearly_used_model_or_plan_costs_more_and_a_stale_reading_counts_as_fuller, test_the_cli_is_started_with_the_chosen_model_and_effort_and_the_choice_is_on_the_route_event,
              test_the_routing_table_and_the_owners_config_override, test_the_brain_survives_odd_rows_and_a_500_says_what_broke_and_where, test_a_near_duplicate_job_is_refused_for_a_hand_submit_but_not_for_the_systems_own_retries, test_each_gemini_call_is_reported_to_the_engine_room_and_a_dead_engine_room_never_blocks, test_an_acceptance_script_that_is_not_in_the_project_is_refused_when_the_job_is_made, test_a_task_includes_the_files_the_check_imports_and_a_retry_is_told_what_failed, test_local_code_goes_to_the_coding_model_and_an_agent_that_keeps_failing_a_kind_of_task_goes_last, test_a_local_model_works_as_an_agent_in_its_own_folder_and_what_it_must_not_touch_is_put_back, test_the_conductor_swaps_models_by_itself_but_only_within_what_is_real_and_safe, test_the_gateway_is_the_only_door_a_container_agent_has_and_the_capability_list_can_grow, test_the_agent_gets_a_pinned_window_and_a_context_pack_with_the_job_the_board_and_the_formats, test_a_local_model_is_offered_only_if_it_fits_the_free_gpu_memory_and_its_window_shrinks_before_it_is_refused):
        f()
        print("ok", f.__name__)
    print("ALL ROUTING TESTS PASSED")
