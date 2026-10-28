import http.client, json, os, shutil, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.app import App
from agentdispatch.web import Server, make_handler, new_token


def call(port, method, path, body=None, token=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    if host:
        h["Host"] = host
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data and r.getheader("Content-Type", "").startswith("application/json") else data)


def test_http_mcp_api_and_guards():
    d = tempfile.mkdtemp(prefix="web_")
    try:
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tok = new_token(d, "codex-cli")
        # no token: refused; wrong host header: refused (DNS rebinding)
        assert call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 401
        assert call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, tok, host="evil.example.com")[0] == 403
        assert call(port, "GET", "/api/stats", host="evil.example.com")[0] == 403
        # MCP over HTTP: the token fixes the author whatever the client claims
        st, r = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "liar"}}}, tok)
        assert st == 200 and r["result"]["serverInfo"]["name"] == "agentdispatch"
        st, r = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "board_post", "arguments": {"kind": "issue", "title": "http issue", "tags": ["web"]}}}, tok)
        pid = r["result"]["structuredContent"]["id"]
        assert app.board.trail(pid)["issue"]["author"] == "codex-cli"
        assert call(port, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, tok)[0] == 202
        st, lite = call(port, "POST", "/mcp/lite", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, tok)
        assert [t["name"] for t in lite["result"]["tools"]] == ["board_post", "board_read", "board_reply", "board_search"]
        st, batch = call(port, "POST", "/mcp", [{"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"jsonrpc": "2.0", "id": 5, "method": "ping"}], tok)
        assert st == 200 and [x["id"] for x in batch] == [4, 5]
        assert call(port, "POST", "/mcp", None, tok)[0] == 200                  # empty body: a harmless JSON-RPC error, not a crash
        # the bulletin page and read API need no token (loopback only)
        st, html = call(port, "GET", "/board")                       # the bulletin moved to /board: / is the Command Centre
        assert st == 200 and b"AGENTDISPATCH BULLETIN" in html
        st, b = call(port, "GET", "/api/board")
        assert b["count"] == 1 and b["posts"][0]["id"] == pid
        st, t = call(port, "GET", "/api/trail/" + pid)
        assert t["issue"]["title"] == "http issue"
        assert call(port, "GET", "/api/trail/P-999999")[0] == 404
        assert call(port, "GET", "/api/stats")[1]["issues"] == {"open": 1}
        # a human suggestion through the API needs a token too
        assert call(port, "POST", "/api/suggest", {"post_id": pid, "model": "opus"})[0] == 401
        st, r = call(port, "POST", "/api/suggest", {"post_id": pid, "model": "opus", "note": "tricky"}, tok)
        assert st == 200 and app.board.trail(pid)["suggestions"][0]["model"] == "opus"
        assert call(port, "GET", "/api/events?since=0")[1]["events"][0]["type"] == "post"
        assert call(port, "POST", "/api/suggest", {"post_id": "P-404", "model": "x"}, tok)[0] == 400
        assert call(port, "GET", "/api/search?q=http+issue")[1]["count"] >= 1
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_owner_role_and_keepalive_safety():
    d = tempfile.mkdtemp(prefix="web2_")
    try:
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent, owner = new_token(d, "pull-agent"), new_token(d, "dev", "owner")
        # an agent token cannot move a job that waits for a person or allow a project folder; the owner token gets past the role check
        assert call(port, "POST", "/api/jobs/cancel", {"job_id": "J-404"}, agent)[0] == 403
        assert call(port, "POST", "/api/agency/allow_root", {"root": d}, agent)[0] == 403
        assert call(port, "POST", "/api/jobs/cancel", {"job_id": "J-404"}, owner)[0] != 403
        # a rejected POST with a body must not poison the next request on the same keep-alive connection
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/mcp", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), {"Content-Type": "application/json"})
        r1 = c.getresponse(); r1.read()
        assert r1.status == 401
        c.request("GET", "/api/stats")
        r2 = c.getresponse(); data = json.loads(r2.read())
        assert r2.status == 200 and data["ok"] is True
        c.close()
        srv.shutdown(); app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_agency_api_tools_and_owner_only_actions():
    d = tempfile.mkdtemp(prefix="web_ag_")
    try:
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent, owner = new_token(d, "codex-cli"), new_token(d, "dev", "owner")
        proj = os.path.join(d, "proj"); os.makedirs(proj)                       # a real allowed project folder, not the owner's D:/agentdispatch, so this runs anywhere
        json.dump(dict(allowed_roots=[proj.replace("\\", "/")]), open(os.path.join(d, "nightshift.json"), "w"))
        mcp = lambda name, args, tok=agent: call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, tok)[1]["result"]["structuredContent"]
        r = mcp("job_submit", dict(title="tidy the readme", goal="fix typos", kind="code", target=proj, acceptance=dict(check="python -m pytest -q"),
                                   steps=[dict(objective="fix typos in README.md", needs=["code"])]))
        assert r["ok"] and r["job"]["state"] == "running"
        jid = r["job"]["id"]
        assert mcp("job_status", dict(job_id=jid))["job"]["steps"][0]["state"] == "todo"
        assert mcp("job_list", {})["counts"] == {"running": 1}
        assert mcp("job_submit", dict(title="x", goal="y", kind="code"))["error"] == "no_acceptance"      # refused clearly, not a crash
        assert any(a["id"] == "ollama-local" for a in mcp("agency_agents", {})["agents"])
        st, ag = call(port, "GET", "/api/agency")
        assert st == 200 and ag["counts"] == {"running": 1} and ag["auto_merge"] is False and ag["killed"] is False
        assert call(port, "GET", "/api/jobs/" + jid)[1]["job"]["id"] == jid
        # an agent token cannot accept / reject / unblock; the owner can
        app.jobs.set_state(jid, "reviewing", "t")
        app.jobs.set_state(jid, "ready", "t", "passed the gate")
        assert call(port, "POST", "/api/jobs/accept", dict(job_id=jid), agent)[0] == 403
        assert [n["id"] for n in call(port, "GET", "/api/agency")[1]["needs_you"]] == [jid]
        st, r = call(port, "POST", "/api/jobs/accept", dict(job_id=jid), owner)
        assert st == 200 and r["job"]["state"] == "merged"
        assert call(port, "GET", "/api/agency")[1]["needs_you"] == []
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_agency_page_submit_diff_cancel_and_retry():
    d = tempfile.mkdtemp(prefix="web_ui_")
    try:
        json.dump(dict(work_dir=os.path.join(d, "work")), open(os.path.join(d, "agency.json"), "w"))             # never the live work folder (job ids restart per database)
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent, owner = new_token(d, "codex-cli"), new_token(d, "dev", "owner")
        st, html = call(port, "GET", "/agency")
        assert st == 200 and b"AGENCY" in html and b"/api/jobs" in html and b"innerHTML" not in html and b"v===false||v==null" in html          # served; no value is put in as HTML; a false boolean attribute is skipped (disabled=\"false\" would still disable a button)
        body = dict(title="tidy", goal="fix typos", kind="general")
        assert call(port, "POST", "/api/jobs", body)[0] == 401                                               # a token is needed to submit
        st, r = call(port, "POST", "/api/jobs", body, agent)
        assert st == 200 and r["job"]["state"] == "queued"
        jid = r["job"]["id"]
        st, r = call(port, "POST", "/api/jobs", dict(title="x", goal="y", kind="code"), owner)
        assert st == 400 and r["error"] == "no_acceptance"                                                   # refused with a reason the page can show
        st, dd = call(port, "GET", "/api/jobs/%s/diff" % jid)
        assert st == 200 and dd["changes"] == {} and "no workspace" in dd["note"]
        assert call(port, "POST", "/api/jobs/cancel", dict(job_id=jid), agent)[0] == 403
        assert call(port, "POST", "/api/jobs/retry", dict(job_id=jid), owner)[0] == 400                      # only failed / cancelled jobs
        st, r = call(port, "POST", "/api/jobs/cancel", dict(job_id=jid), owner)
        assert st == 200 and r["job"]["state"] == "cancelled"
        st, r = call(port, "POST", "/api/jobs/retry", dict(job_id=jid), owner)
        assert st == 200 and r["job"]["id"] != jid and r["job"]["state"] == "queued" and r["job"]["title"] == "tidy"
        assert call(port, "GET", "/api/jobs/J-999999/diff")[0] in (404, 500)
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_brain_page_and_feed():
    d = tempfile.mkdtemp(prefix="web_brain_")
    try:
        json.dump(dict(work_dir=os.path.join(d, "work")), open(os.path.join(d, "agency.json"), "w"))
        from agentdispatch.agency.registry import DEFAULTS
        agents = json.loads(json.dumps(DEFAULTS))                                                          # a brand-new kind of agent: one registry entry, no code change
        agents["svd-video"] = dict(kind="app", name="Video model", strengths=["video"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"))
        agents["voice"] = dict(kind="app", name="Speech model", strengths=["speech"], cost="local", concurrency=1, daily_cap=0, sandbox="none", enabled=False, run=dict(via="pull"), brain=dict(color="#ffd34d"))
        json.dump(agents, open(os.path.join(d, "agents.json"), "w"))
        app = App(d)
        # this test is about the page and how agents are placed, not about what runs on the host: on a real PC the first tool scan (dozens of `--version` probes), the Ollama / ComfyUI
        # ports and the engine room took over 20 s and timed the request out. Stub those three probes so the answer does not depend on the machine.
        from agentdispatch.agency import brain
        def offline(*a, **k):
            raise OSError("offline in this test")
        app.localtools.reg.scan = lambda force=False: dict(tools={}, scanned=time.time())
        app.availability.fetch = offline
        real_get_json, brain._get_json = brain._get_json, offline
        brain._svc["data"] = None
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st, html = call(port, "GET", "/brain")
        assert st == 200 and b"NEURAL ENGINE" in html and b"/api/brain" in html and b'get("demo")' in html
        st, r = call(port, "GET", "/api/brain")
        assert st == 200 and r["ok"] and {a["id"] for a in r["agents"]} >= {"claude-code", "codex-cli", "gemini-cli", "ollama-local", "grok-cli", "pull-agent"}
        assert all(a["region"] and a["color"].startswith("#") for a in r["agents"]) and r["strand"] is None and "cursor" in r
        by = {a["id"]: a for a in r["agents"]}
        assert by["svd-video"]["region"] == "occipital" and by["voice"]["region"] == "broca" and by["voice"]["color"] == "#ffd34d"
        srv.shutdown()
        app.board.close()
    finally:
        if "real_get_json" in locals():
            brain._get_json = real_get_json
            brain._svc["data"] = None
        shutil.rmtree(d, ignore_errors=True)


def test_pull_endpoints_and_mcp_tools_and_no_acting_in_anothers_name():
    d = tempfile.mkdtemp(prefix="web_pull_")
    try:
        json.dump(dict(work_dir=os.path.join(d, "work")), open(os.path.join(d, "agency.json"), "w"))
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        bot, other, owner = new_token(d, "pull-agent"), new_token(d, "codex-mcp-client"), new_token(d, "dev", "owner")
        g = app.jobs.create("dev", "haiku", "write a haiku about rain", "general")
        app.jobs.add_steps(g["id"], [dict(objective="draft three lines about rain", needs=["write"])])
        assert call(port, "POST", "/api/agency/next", dict(agent="pull-agent"), other)[0] == 403                # codex's token cannot ask for work as pull agent
        assert call(port, "POST", "/api/agency/next", dict(agent="pull-agent"))[0] == 401                       # no token
        st, r = call(port, "POST", "/api/agency/next", {}, bot)                                              # the token's own name is enough
        t = r["task"]
        assert st == 200 and r["ok"] and t["kind"] == "step" and "draft three lines" in t["prompt"] and "answer" in t["answer_format"]
        st, r = call(port, "POST", "/api/agency/submit", dict(task_id=t["task_id"], text="I would rather not"), bot)
        assert r["ok"] and r["result"]["action"] == "format_error"                                            # not the requested JSON: one free re-try on the same task, with the problem told
        st, r = call(port, "POST", "/api/agency/submit", dict(task_id=t["task_id"], text="I still would rather not"), bot)
        assert r["ok"] and r["result"]["action"] == "failed"                                                  # twice: a failed attempt, not a crash
        st, r = call(port, "POST", "/api/agency/next", {}, bot)
        t = r["task"]
        st, r = call(port, "POST", "/api/agency/submit", dict(task_id=t["task_id"], text=json.dumps(dict(answer="rain taps the roof / slow silver on the tin / the dusk drinks it in"))), other)
        assert st == 200 and r["ok"] is False and r["error"] == "no_such_task"                                # nobody else can answer it
        mcp = lambda name, args, tok: call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, tok)[1]["result"]["structuredContent"]
        assert mcp("agency_next", dict(agent="pull-agent"), other)["error"] == "not_you"                        # MCP: the same rule
        r = mcp("agency_submit", dict(task_id=t["task_id"], text=json.dumps(dict(answer="rain taps the roof / slow silver on the tin / the dusk drinks it in"))), bot)
        assert r["ok"] and r["result"]["action"] == "done" and app.jobs.get(g["id"])["state"] == "reviewing"
        assert mcp("agency_next", {}, bot)["task"] is None                                                    # it wrote the step, so nothing to review for it
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_models_page_api_and_owner_pins():
    d, lib = tempfile.mkdtemp(prefix="web_mod_"), tempfile.mkdtemp(prefix="web_lib_")
    try:
        os.makedirs(os.path.join(lib, "speech"))
        with open(os.path.join(lib, "speech", "personaplex-7b-v1.safetensors"), "wb") as f:
            f.truncate(70 * 1024 * 1024)
        app = App(d)
        srv = Server(("127.0.0.1", 0), make_handler(app))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        agent, owner = new_token(d, "codex-cli"), new_token(d, "dev", "owner")
        assert call(port, "GET", "/models")[0] == 200
        for path, tab in (("/", "board"), ("/agency", "agency"), ("/brain", "brain"), ("/models", "models")):                  # every page has the shared nav bar with its own tab lit
            html = call(port, "GET", path)[1]
            assert b'id="sbnav"' in html and (b'class="act" title' in html) and b"--cy:#00e5ff" in html, path
        assert call(port, "POST", "/api/models/roots", dict(path=lib), agent)[0] == 403                      # only the owner adds folders
        assert call(port, "POST", "/api/models/roots", dict(path=lib), owner)[0] == 200
        app.catalog.scan(20, deep=False)
        rows = call(port, "GET", "/api/models?q=personaplex")[1]["models"]
        mine = [r for r in rows if r["path"].startswith(os.path.normpath(lib))]
        assert mine and mine[0]["function"] == "speech-duplex", rows
        mid, fn = mine[0]["id"], mine[0]["function"]
        assert call(port, "GET", "/api/models/status")[1]["total"] >= 1
        # an AI may choose (as the AI) and the owner's pin then wins over it
        st, r = call(port, "POST", "/api/models/select", dict(function=fn, model_id=mid, by="ai"), agent)
        assert st == 200 and r.get("ok"), r
        assert call(port, "POST", "/api/models/select", dict(function=fn, model_id=mid, by="user"), agent)[0] == 403
        assert call(port, "POST", "/api/models/override", dict(model_id=mid, favorite=True), agent)[0] == 403
        st, r = call(port, "POST", "/api/models/select", dict(function=fn, model_id=mid, by="user"), owner)
        assert st == 200 and app.catalog.d["selection"][fn]["by"] == "user"
        r = call(port, "POST", "/api/models/select", dict(function=fn, model_id=mid, by="ai"), agent)[1]
        assert not r.get("ok") and app.catalog.d["selection"][fn]["by"] == "user"                            # never overridden
        assert call(port, "POST", "/api/models/override", dict(model_id=mid, label="Voice"), owner)[0] == 200
        assert call(port, "GET", "/api/models?q=voice")[1]["models"][0]["id"] == mid
        # MCP tools
        mcp = lambda name, args: call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, agent)[1]["result"]["structuredContent"]
        assert mcp("model_pick", dict(function=fn))["by"] == "user"
        assert any(m["id"] == mid for m in mcp("model_list", dict(function=fn))["models"])
        srv.shutdown()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(lib, ignore_errors=True)


if __name__ == "__main__":
    test_models_page_api_and_owner_pins()
    test_http_mcp_api_and_guards()
    test_owner_role_and_keepalive_safety()
    test_agency_api_tools_and_owner_only_actions()
    test_agency_page_submit_diff_cancel_and_retry()
    test_brain_page_and_feed()
    test_pull_endpoints_and_mcp_tools_and_no_acting_in_anothers_name()
    print("ALL WEB TESTS PASSED")
