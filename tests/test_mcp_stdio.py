"""end to end: launch `python -m agentdispatch stdio` exactly like an agent would, speak MCP over pipes, drive the board through tools/call"""
import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Client:
    def __init__(self, data, profile="full", actor=None):
        cmd = [sys.executable, "-m", "agentdispatch", "--data", data, "stdio", "--profile", profile] + (["--actor", actor] if actor else [])
        self.p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.n = 0

    def rpc(self, method, params=None, note=False):
        msg = dict(jsonrpc="2.0", method=method, params=params or {})
        if not note:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        if note:
            return None
        return json.loads(self.p.stdout.readline())

    def call(self, name, **args):
        r = self.rpc("tools/call", dict(name=name, arguments=args))
        return r["result"]["structuredContent"], r["result"].get("isError", False)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def test_handshake_profiles_and_flow():
    d = tempfile.mkdtemp(prefix="mcp_")
    try:
        c = Client(d, "full")
        r = c.rpc("initialize", dict(protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="claude-code", version="1")))
        assert r["result"]["serverInfo"]["name"] == "agentdispatch" and r["result"]["protocolVersion"] == "2025-06-18"
        assert "notice board" in r["result"]["instructions"]
        c.rpc("notifications/initialized", note=True)
        names = [t["name"] for t in c.rpc("tools/list")["result"]["tools"]]
        assert "board_attempt" in names and "board_post" in names and len(names) >= 10
        assert c.rpc("ping")["result"] == {}
        # the board over MCP, authored by the client name
        out, err = c.call("board_post", kind="issue", title="mixer exits 1 on failed gate", body="sys.exit(str)", tags=["mixer"])
        assert not err and out["id"] == "P-000001"
        a1, _ = c.call("board_attempt", post_id="P-000001", approach="call sys.exit(2) after printing the gate message")
        assert a1["ok"]
        c.call("board_result", attempt_id=a1["attempt"], outcome="failed", evidence="media_studio still read it as a crash")
        a2, _ = c.call("board_attempt", post_id="P-000001", approach="print the gate message then call sys.exit(2)")
        assert a2["ok"] is False and a2["error"] == "already_tried"
        t, _ = c.call("board_trail", post_id="P-000001")
        assert t["issue"]["author"] == "claude-code" and t["dead_ends"][0]["attempt"] == a1["attempt"]
        # validation and errors come back as tool errors, not crashes
        out, err = c.call("board_post", kind="nonsense", title="x")
        assert err and out["error"] == "invalid_arguments"
        out, err = c.call("board_trail", post_id="P-424242")
        assert err and out["error"] == "not_found"
        bad = c.rpc("tools/call", dict(name="no_such_tool", arguments={}))
        assert bad["error"]["code"] == -32602
        assert c.rpc("nope")["error"]["code"] == -32601
        c.close()
        lines = open(os.path.join(d, "audit.jsonl"), encoding="utf-8").read().splitlines()
        assert any(json.loads(l)["tool"] == "board_attempt" and json.loads(l)["actor"] == "claude-code" for l in lines)
        # a second client (lite profile) sees the same board but only the small tool set
        c2 = Client(d, "lite", actor="gemma-4b")
        c2.rpc("initialize", dict(protocolVersion="2025-03-26", clientInfo=dict(name="ignored")))
        lite = [t["name"] for t in c2.rpc("tools/list")["result"]["tools"]]
        assert lite == ["board_post", "board_read", "board_reply", "board_search"]
        rd, _ = c2.call("board_read")
        assert rd["count"] == 1 and rd["posts"][0]["id"] == "P-000001"
        sr, _ = c2.call("board_search", query="sys.exit gate")
        assert sr["count"] >= 1
        r = c2.rpc("tools/call", dict(name="board_attempt", arguments=dict(post_id="P-000001", approach="x")))
        assert "error" in r                                               # not in the lite profile
        p, _ = c2.call("board_post", kind="question", title="which exit code means gate failed?")
        assert p["id"] == "P-000002"
        c2.close()
        t2 = Client(d, "full")
        t2.rpc("initialize", dict(protocolVersion="2025-06-18", clientInfo=dict(name="x")))
        assert t2.call("board_trail", post_id="P-000002")[0]["issue"]["author"] == "gemma-4b"       # --actor fixed the author
        t2.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_utf8_round_trip():
    d = tempfile.mkdtemp(prefix="mcp8_")
    try:
        p = subprocess.Popen([sys.executable, "-m", "agentdispatch", "--data", d, "stdio"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        title = "arrow \u2192 \u4e2d\u6587 \u2713"
        for n, (name, args) in enumerate((("board_post", dict(kind="issue", title=title)), ("board_read", {})), 1):
            p.stdin.write((json.dumps(dict(jsonrpc="2.0", id=n, method="tools/call", params=dict(name=name, arguments=args)), ensure_ascii=False) + "\n").encode("utf-8"))
            p.stdin.flush()
            reply = json.loads(p.stdout.readline().decode("utf-8"))
        assert reply["result"]["structuredContent"]["posts"][0]["title"] == title        # non-ASCII survives the Windows default codepage both ways
        p.stdin.close(); p.wait(timeout=10)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_handshake_profiles_and_flow()
    test_utf8_round_trip()
    print("ALL MCP STDIO TESTS PASSED")
