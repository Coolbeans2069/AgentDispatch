import json, os, shutil, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["AGENTDISPATCH_SAFE_REPOS"] = "D:/repos/app"                       # the test fixtures' repo layout (Windows-style paths are checked on every OS)
os.environ["AGENTDISPATCH_ALLOWED_ROOTS"] = "D:/repos/app,D:/repos/other"
sys.path.insert(0, ROOT)
from agentdispatch.store import Board
from agentdispatch.nightshift import NightShift, Sandbox, CheckError, command_ok, parse_check, is_protected, docker_ready, DEFAULTS
from agentdispatch.orchestrator import LLMUnavailable

# the night shift asks the Engine Room (:8898) whether the machine is busy. On a PC where the real Engine Room runs and the
# machine is loaded (e.g. by the rest of the suite), that made these tests depend on live CPU load. Point the tests at a port
# nothing listens on: an unreachable Engine Room means "not busy". test_window_busy_machine_and_missing_model stubs the busy case.
DEFAULTS["engine_room"] = "http://127.0.0.1:9"


class Clock:
    t = time.mktime((2026, 10, 14, 3, 0, 0, 0, 0, -1))                 # 03:00 local: inside the night window

    def __call__(self):
        return self.t


FAKE_GOOGLE_KEY = "AIza" + "SyA1234567890" + "abcdefghijklmnopqrstuvwx"   # a fake key for the redaction tests, assembled so secret scanners do not flag the source

def mk_project(buggy=True):
    d = tempfile.mkdtemp(prefix="proj_")
    open(os.path.join(d, "calc.py"), "w").write("def add(a, b):\n    return a - b\n" if buggy else "def add(a, b):\n    return a + b\n")
    open(os.path.join(d, "test_calc.py"), "w").write("import calc\nassert calc.add(2, 3) == 5, 'add is wrong'\nprint('ok')\n")
    return d


def scripted(*answers):
    q = list(answers)

    def llm(system, user):
        llm.prompts.append(user)
        if not q:
            raise LLMUnavailable("script exhausted")
        a = q.pop(0)
        return a if isinstance(a, str) else json.dumps(a)
    llm.prompts = []
    return llm


def board(clock=None):
    d = tempfile.mkdtemp(prefix="ns_")
    return Board(d, now=clock or Clock()), d


FIX = {"calc.py": "def add(a, b):\n    return a + b\n"}


def test_command_allowlist_and_check_parsing():
    assert command_ok(["python", "test_calc.py"]) and command_ok(["python.exe", "-m", "pytest", "-q"]) and command_ok(["python", "-m", "unittest"])
    for bad in (["python", "-c", "import os"], ["bash", "x.py"], ["python", "../x.py"], ["python", "C:\\x.py"], ["python"], ["python", "-m", "pip"], ["rm", "-rf", "/"], ["python", "-i"]):
        assert not command_ok(bad), bad
    assert parse_check('{"copy": "D:/x", "command": ["python", "t.py"]}')["copy"] == "D:/x"
    for bad in ("nope", '{"copy": 1}', '{"copy": "x", "command": []}'):
        try:
            parse_check(bad); raise AssertionError(bad)
        except CheckError:
            pass


def test_sandbox_applies_patch_to_a_copy_only_and_enforces_limits():
    proj = mk_project()
    cfg = dict(allowed_roots=[os.path.dirname(proj)], max_copy_mb=5, check_timeout_s=60, isolation="none")
    sb = Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), cfg)
    ok, out = sb.run({})
    assert not ok and "add is wrong" in out                                     # the unpatched copy fails its check
    ok, out = sb.run(FIX)
    assert ok and "ok" in out
    assert "a - b" in open(os.path.join(proj, "calc.py")).read()               # the real project was never touched
    for bad in ({"../escape.py": "x"}, {"sub/../../escape.py": "x"}):
        try:
            sb.run(bad); raise AssertionError("path escape accepted")
        except CheckError:
            pass
    try:
        Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), dict(cfg, allowed_roots=["D:/somewhere/else"]))
        raise AssertionError("outside the allowed roots")
    except CheckError:
        pass
    try:
        Sandbox(dict(copy=proj, command=["python", "-c", "print(1)"]), cfg)
        raise AssertionError("bad command")
    except CheckError:
        pass
    slow = Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), dict(cfg, check_timeout_s=0.5))
    ok, out = slow.run({"calc.py": "import time\ntime.sleep(30)\ndef add(a, b):\n    return a + b\n"})     # really slow (a bare Python run can finish inside a tiny limit on a fast machine)
    assert ok is False and "did not finish" in out, out
    shutil.rmtree(proj, ignore_errors=True)


def test_trusted_check_verified_patch_is_attached_not_applied():
    proj = mk_project()
    b, d = board()
    i = b.post("dev", "issue", "calc.add subtracts instead of adding", "test_calc.py fails", ["bug"])["id"]
    b.attach("dev", i, "check", json.dumps(dict(copy=proj, command=["python", "test_calc.py"], files=["calc.py", "../outside.txt"])))
    b.note_event(i, "triage", "orchestrator", dict(tier="ollama"))
    llm = scripted(dict(approach="replace the minus with a plus in add()", analysis="a - b should be a + b", expected="test passes", patch=FIX))
    ns = NightShift(b, llm, dict(trusted_authors=["dev"], allowed_roots=[os.path.dirname(proj)], max_attempts=3, isolation="none"), now=b.now)
    assert ns.eligible() == [i]
    r = ns.run_once()
    assert r["ran"] and r["issues"][i]["last"].startswith("verified patch")
    assert "calc.py (current content)" in llm.prompts[0] and "return a - b" in llm.prompts[0] and "outside.txt" not in llm.prompts[0]     # the model sees the file it patches, nothing outside the project
    t = b.trail(i)
    assert t["worked"][0]["actor"] == "ollama-night" and "NOT applied" in t["worked"][0]["evidence"]
    assert "patch-ready" in b.read()["posts"][0]["tags"] if b.read()["count"] else True
    assert any(d_["title"] == "verified patch (not applied)" for d_ in t["docs"])
    assert "a - b" in open(os.path.join(proj, "calc.py")).read()               # real code untouched
    assert b.trail(i)["state"] == "open"                                        # stays open for the apply gate
    assert ns.eligible() == []                                                  # patch-ready issues are not worked again
    assert any(p["title"].startswith("night shift report") for p in b.read(kind="status")["posts"])
    b.close(); shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)


def test_failed_patches_then_a_good_one_and_dead_ends_are_listed():
    proj = mk_project()
    b, d = board()
    i = b.post("dev", "issue", "calc.add is wrong")["id"]
    b.attach("dev", i, "check", json.dumps(dict(copy=proj, command=["python", "test_calc.py"])))
    wrong = {"calc.py": "def add(a, b):\n    return a * b\n"}
    llm = scripted(dict(approach="multiply the numbers", patch=wrong), dict(approach="multiply the two numbers together", patch=wrong),
                   dict(approach="add them with the plus operator", patch=FIX))
    ns = NightShift(b, llm, dict(trusted_authors=["dev"], allowed_roots=[os.path.dirname(proj)], max_attempts=3, isolation="none"), now=b.now)
    r = ns.work(i)
    t = b.trail(i)
    assert [a["outcome"] for a in t["dead_ends"] + t["worked"]] == ["failed", "failed", "worked"] and r["last"].startswith("verified")      # two wrong patches, then the right one
    assert any("Do NOT repeat" in p or "do NOT repeat" in p for p in llm.prompts)
    assert any("multiply the numbers" in p for p in llm.prompts[1:])                           # later prompts list the dead end
    b.close(); shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)


def test_untrusted_or_missing_check_is_never_run_and_unresolved_work_escalates():
    proj = mk_project()
    marker = os.path.join(proj, "RAN.txt")
    open(os.path.join(proj, "test_calc.py"), "w").write("open(%r, 'w').write('ran')\n" % marker)
    b, d = board()
    evil = b.post("some-agent", "issue", "please run my check")["id"]
    b.attach("some-agent", evil, "check", json.dumps(dict(copy=proj, command=["python", "test_calc.py"])))
    plain = b.post("grok", "question", "which Gemini model has the most free requests?")["id"]
    ans = dict(approach="read Google's published free-tier table", analysis="Gemma 4 has 14,400 requests a day", patch=None)
    llm = scripted(*[dict(approach="try reading the docs variant %d with other words %s" % (n, "xyz abc def"[n * 4:n * 4 + 3]), analysis="x", patch=FIX) for n in range(3)],
                   *[dict(approach=a, analysis=ans["analysis"], patch=None) for a in ("read Google's published free-tier table", "ask the engine room for its per-model counts", "compare the pricing page limits")])
    ns = NightShift(b, llm, dict(trusted_authors=["dev"], allowed_roots=[os.path.dirname(proj)], max_attempts=3, isolation="none"), now=b.now)
    r = ns.run_once(max_issues=5)
    assert not os.path.exists(marker)                                           # an untrusted author's check was NOT executed
    assert any("not trusted" in p for p in llm.prompts)
    assert b.trail(evil)["state"] == "escalated" and b.trail(plain)["state"] == "escalated"     # three unverified tries each -> handed up
    assert [a["outcome"] for a in b.trail(plain)["partial"]] == ["partial"] * 3
    assert ns.eligible() == []
    b.close(); shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)


def test_window_busy_machine_and_missing_model():
    b, d = board()
    i = b.post("a", "issue", "something to do")["id"]
    noon = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, -1))
    ns = NightShift(b, scripted(), dict(), now=lambda: noon)
    assert ns.run_once() == dict(ok=True, ran=False, why="outside the night window")
    ns2 = NightShift(b, scripted(), dict(), now=b.now)                           # night, but the model is unreachable: the issue is released, the run stops
    r = ns2.run_once()
    assert "no language model" in r["stopped"] and b.trail(i)["state"] == "open" and b.read(state="claimed")["count"] == 0
    ns3 = NightShift(b, scripted(), dict(max_cpu_pct=-1), now=b.now)
    ns3.machine_ok = lambda: False
    assert "busy" in ns3.run_once()["stopped"]
    assert ns2.run_once(force=True)["ran"] and NightShift(b, scripted(), dict(), now=noon.__class__ and (lambda: noon)).run_once(force=True)["ran"]
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_patches_cannot_rewrite_the_check_or_its_tests():
    chk = dict(copy="x", command=["python", "test_calc.py"], protect=["data/golden.json"])
    for rel in ("test_calc.py", "./test_calc.py", "TEST_CALC.PY", "tests/test_other.py", "pkg/test_x.py", "conftest.py", "pkg/conftest.py", "foo_test.py", "data/golden.json"):
        assert is_protected(rel, chk), rel
    for rel in ("calc.py", "pkg/util.py", "contest.py", "latest.py"):
        assert not is_protected(rel, chk), rel
    proj = mk_project()
    sb = Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), dict(allowed_roots=[os.path.dirname(proj)], isolation="none"))
    try:
        sb.run({"test_calc.py": "print('ok')\n"})                                # the forged "pass"
        raise AssertionError("a patch rewrote the test")
    except CheckError as e:
        assert "check itself" in str(e)
    assert sb.run({})[0] is False                                                # and the unpatched copy still fails honestly
    shutil.rmtree(proj, ignore_errors=True)


def test_timeout_returns_promptly_and_auto_isolation_refuses_without_docker():
    proj = mk_project()
    open(os.path.join(proj, "slow.py"), "w").write("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\ntime.sleep(60)\n")
    cfg = dict(allowed_roots=[os.path.dirname(proj)], isolation="none", check_timeout_s=3)
    t0 = time.time()
    ok, out = Sandbox(dict(copy=proj, command=["python", "slow.py"]), cfg).run({})
    assert not ok and "did not finish" in out and time.time() - t0 < 30                     # the whole tree is killed, so cleanup and the call both return
    import agentdispatch.nightshift as N
    N._docker_cache["no-such-image:0"] = (time.time(), False)
    try:
        Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), dict(allowed_roots=[os.path.dirname(proj)], isolation="auto", docker_image="no-such-image:0"))
        raise AssertionError("auto isolation must refuse when no container image exists")
    except CheckError as e:
        assert "no isolation available" in str(e)
    shutil.rmtree(proj, ignore_errors=True)


def test_docker_isolation_blocks_network_and_host_files():
    if not docker_ready(DEFAULTS["docker_image"]):
        print("skip: no docker image"); return
    proj = mk_project(buggy=False)
    open(os.path.join(proj, "probe.py"), "w").write(
        "import os, urllib.request\nassert not os.path.exists('C:/Windows') and not os.path.exists('/mnt/c'), 'host files visible'\n"
        "try:\n    urllib.request.urlopen('http://1.1.1.1', timeout=3)\n    raise SystemExit('network is open')\nexcept OSError:\n    pass\nprint('isolated')\n")
    sb = Sandbox(dict(copy=proj, command=["python", "probe.py"]), dict(allowed_roots=[os.path.dirname(proj)], isolation="docker", check_timeout_s=120))
    ok, out = sb.run({})
    assert ok and "isolated" in out, out
    assert Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), dict(allowed_roots=[os.path.dirname(proj)], isolation="docker", check_timeout_s=120)).run({})[0]    # and a normal check passes inside it
    shutil.rmtree(proj, ignore_errors=True)


def test_no_progress_nights_escalate():
    b, d = board()
    i = b.post("a", "question", "something the model cannot handle")["id"]
    ns = NightShift(b, scripted(*(["not json at all"] * 30)), dict(max_attempts=2, no_progress_escalate=3), now=b.now)
    for night in range(3):
        assert b.trail(i)["state"] == "open"
        ns.run_once(force=True)
    assert b.trail(i)["state"] == "escalated" and b.count_events(i, "night_no_progress") == 3
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_default_trust_is_everyone_but_only_inside_the_container():
    proj = mk_project()
    b, d = board()
    i = b.post("some-agent", "issue", "calc.add subtracts")["id"]
    b.attach("some-agent", i, "check", json.dumps(dict(copy=proj, command=["python", "test_calc.py"], files=["calc.py"])))
    roots = [os.path.dirname(proj)]
    # unconfined (isolation none): "*" is NOT enough, only named authors are trusted
    ns = NightShift(b, scripted(), dict(allowed_roots=roots, isolation="none"), now=b.now)
    assert ns.trusted("dev") is False and ns.confined() is False
    assert NightShift(b, scripted(), dict(allowed_roots=roots, isolation="none", trusted_authors=["some-agent"]), now=b.now).trusted("some-agent")
    if docker_ready(DEFAULTS["docker_image"]):
        ns = NightShift(b, scripted(dict(approach="use a plus", patch=FIX)), dict(allowed_roots=roots, isolation="docker"), now=b.now)     # defaults: trusted_authors ["*"]
        assert ns.confined() and ns.trusted("some-agent") and ns.trusted("anyone-else")
        r = ns.work(i)
        assert r["last"].startswith("verified patch"), r                                                      # an agent nobody listed got its check run, in the container
    # the check is judged by who ATTACHED it, not by who wrote the issue
    j = b.post("dev", "issue", "another bug")["id"]
    b.attach("mallory", j, "check", json.dumps(dict(copy=proj, command=["python", "test_calc.py"])))
    ns2 = NightShift(b, scripted(*[dict(approach="guess number %s with words %s" % (n, w), patch=None) for n, w in enumerate(("alpha beta gamma", "delta epsilon zeta", "eta theta iota"))]), dict(allowed_roots=roots, isolation="none", trusted_authors=["dev"]), now=b.now)
    ns2.work(j)
    assert any("not trusted" in pr and "mallory" in pr for pr in ns2.llm.prompts)
    assert b.trail(j)["docs"][0]["actor"] == "mallory"
    b.close(); shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)


def test_setup_status_and_the_board_add_check_tool():
    from agentdispatch.nightshift import write_config, load_config, status
    from agentdispatch.app import App
    d = tempfile.mkdtemp(prefix="nsapp_")
    proj = mk_project()
    try:
        app = App(d, with_index=False)
        # out of the box: the three repos are allowed, a random folder is not; the tool says why
        sess = dict(profile="full", actor="claude-code")
        call = lambda n, **a: app.mcp.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name=n, arguments=a)), sess)["result"]["structuredContent"]
        pid = call("board_post", kind="issue", title="calc is wrong")["id"]
        r = call("board_add_check", post_id=pid, project=proj, command=["python", "test_calc.py"])
        assert r["ok"] is False and r["error"] == "check_refused" and "allowed root" in r["message"]
        write_config(d, allowed_roots=[os.path.dirname(proj).replace("\\", "/")])
        write_config(d, allowed_roots=[os.path.dirname(proj).replace("\\", "/")])                                 # idempotent
        cfg = load_config(d)
        assert cfg["allowed_roots"].count(os.path.dirname(proj).replace("\\", "/")) == 1 and "D:/repos/app" in cfg["allowed_roots"]
        bad = call("board_add_check", post_id=pid, project=proj, command=["bash", "x.sh"])
        assert bad["ok"] is False and "not allowed" in bad["message"]
        assert call("board_add_check", post_id=pid, project=proj + "_missing", command=["python", "t.py"])["ok"] is False
        ok = call("board_add_check", post_id=pid, project=proj, command=["python", "test_calc.py"], files=["calc.py"])
        assert ok["ok"] and ok["doc"].startswith("D-")
        doc = json.loads(app.board.read_doc(ok["doc"])["text"])
        assert doc["command"] == ["python", "test_calc.py"] and doc["files"] == ["calc.py"]
        assert app.board.trail(pid)["docs"][0]["actor"] == "claude-code"
        st = status(app.board, d)
        assert st["window"] == "00:00-06:00" and st["isolation"] == "auto" and "docker_ready" in st and st["trusted_authors"] == ["*"]
        write_config(d, trusted_authors=["dev"], isolation="none")
        assert load_config(d)["trusted_authors"] == ["*", "dev"] and load_config(d)["isolation"] == "none"
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)


def test_secret_files_never_reach_a_prompt_and_confinement_is_not_cached():
    from agentdispatch.nightshift import file_allowed
    from agentdispatch.app import App
    import agentdispatch.nightshift as N
    for ok_ in ("calc.py", "pkg/util.py", "config.json", "keyboard.py", "pkg/author.py"):
        assert file_allowed(ok_), ok_
    for bad in (".env", "auth_config.json", "secrets.json", "src/api_key.txt", "token_count.py", "keys/private.pem", "data/x.py", ".git/config", "__pycache__/x.py"):
        assert not file_allowed(bad), bad
    proj = mk_project()
    open(os.path.join(proj, "settings.py"), "w").write("API_KEY = '" + FAKE_GOOGLE_KEY + "'\npassword = 'hunter2hunter2'\nVALUE = 1\n")
    open(os.path.join(proj, "secrets.json"), "w").write('{"k": "top-secret-value"}')
    cfg = dict(allowed_roots=[os.path.dirname(proj)], isolation="none")
    sb = Sandbox(dict(copy=proj, command=["python", "test_calc.py"]), cfg)
    ctx = NightShift.file_context(dict(files=["settings.py", "secrets.json", "calc.py"]), sb)
    assert "AIzaSy" not in ctx and "hunter2hunter2" not in ctx and "top-secret-value" not in ctx and "VALUE = 1" in ctx and "calc.py (current content)" in ctx
    # the tool refuses secret-looking files up front
    d = tempfile.mkdtemp(prefix="nssec_")
    try:
        app = App(d, with_index=False)
        from agentdispatch.nightshift import write_config
        write_config(d, allowed_roots=[os.path.dirname(proj).replace("\\", "/")])
        sess = dict(profile="full", actor="agent")
        call = lambda n, **a: app.mcp.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name=n, arguments=a)), sess)["result"]["structuredContent"]
        pid = call("board_post", kind="issue", title="x")["id"]
        r = call("board_add_check", post_id=pid, project=proj, command=["python", "test_calc.py"], files=["secrets.json"])
        assert r["ok"] is False and "cannot be shown" in r["message"]
        assert call("board_add_check", post_id=pid, project=proj, command=["python", "test_calc.py"], files=["calc.py"])["ok"]
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True); shutil.rmtree(proj, ignore_errors=True)
    # confinement is re-evaluated each time, not remembered
    b, d2 = board()
    ns = NightShift(b, scripted(), dict(isolation="auto"), now=b.now)
    N._docker_cache[ns.cfg["docker_image"]] = (time.time(), True)
    assert ns.confined() is True
    N._docker_cache[ns.cfg["docker_image"]] = (time.time(), False)
    assert ns.confined() is False and ns.trusted("anyone") is False
    N._docker_cache.clear()
    b.close(); shutil.rmtree(d2, ignore_errors=True)


def test_docker_ready_falls_back_to_the_image_list():
    import subprocess as sp
    import agentdispatch.nightshift as N

    class R:
        def __init__(self, code, out=""):
            self.returncode, self.stdout = code, out
    real = sp.run
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd[:3])
        if cmd[1:3] == ["image", "inspect"]:
            return R(1)                                                         # the quirk: inspect by name fails although the image is there
        return R(0, "e88e9763f943\n") if cmd[1] == "images" else R(1)
    try:
        sp.run = fake
        N._docker_cache.clear()
        assert N.docker_ready("python:3.11-slim") is True and calls[0][1:3] == ["image", "inspect"] and calls[1][1] == "images"
        N._docker_cache.clear()

        def fake_missing(cmd, **kw):
            return R(1) if cmd[1:3] == ["image", "inspect"] else R(0, "")      # really absent: the list is empty too
        sp.run = fake_missing
        assert N.docker_ready("nope:1") is False
    finally:
        sp.run = real
        N._docker_cache.clear()


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL NIGHTSHIFT TESTS PASSED")
