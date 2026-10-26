"""live end-to-end with the real REA backend (skipped if REA is not installed): discover -> describe -> call -> read the result by reference, all through the broker's MCP stdio"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REA = os.environ.get("REA_SCRIPT", "")                                       # path to rea.mjs (optional)
FIXTURE = os.environ.get("REA_FIXTURE", "")
DATA = os.environ.get("AGENTDISPATCH_DATA", os.path.join(ROOT, "data"))

if not os.path.isfile(REA) or not os.path.isdir(FIXTURE) or not os.path.isfile(os.path.join(DATA, "index", "rea", "index.json")):
    print("SKIP: REA, the fixture or the built index is not here")
    sys.exit(0)

p = subprocess.Popen([sys.executable, "-m", "agentdispatch", "--data", DATA, "stdio", "--profile", "lite", "--actor", "e2e"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, encoding="utf-8")
n = [0]


def rpc(method, params=None):
    n[0] += 1
    p.stdin.write(json.dumps(dict(jsonrpc="2.0", id=n[0], method=method, params=params or {})) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def call(_tool, **a):
    r = rpc("tools/call", dict(name=_tool, arguments=a))["result"]
    return r["structuredContent"]


rpc("initialize", dict(protocolVersion="2025-06-18", clientInfo=dict(name="e2e")))
tl = rpc("tools/list")["result"]["tools"]
size = len(json.dumps(tl))
print("lite profile exposes %d tools, ~%d tokens of tool definitions (REA's own list is ~489,000)" % (len(tl), size // 4))
assert size // 4 < 5000

r = call("tool_search")
print("L0 groups:", [(g["group"], g["tools"]) for g in r["groups"]][:4], "...  (%d groups)" % len(r["groups"]))
r = call("tool_search", query="reconstruct a JavaScript or Electron application", limit=3)
print("search ->", [(x["tool"], x["risk"]) for x in r["results"]])
assert "analyze_javascript_application" in [x["tool"] for x in r["results"]]
d = call("tool_describe", names=["analyze_javascript_application"])
sch = d["tools"][0]["schema"]
print("schema props:", list(sch["properties"])[:6], "~%d tokens" % (len(json.dumps(sch)) // 4))
assert "input_path" in sch["properties"]
c = call("tool_call", name="analyze_javascript_application", arguments=dict(input_path=FIXTURE), timeout_s=300)
if not c.get("ok"):
    print("CALL FAILED:", json.dumps(c)[:500])
print("call ok=%s inline=%s ref=%s bytes=%s pages=%s" % (c["ok"], "result" in c, c.get("result_ref"), c.get("bytes"), c.get("pages")))
assert c["ok"] and c.get("result_ref")
pg = call("tool_result", ref=c["result_ref"], page=1)
assert pg["ok"] and "evidence_id" in pg["text"]
full = []
for i in range(1, min(c["pages"], 400) + 1):
    full.append(call("tool_result", ref=c["result_ref"], page=i)["text"])
text = "".join(full)
for needle in ("notes:search", "clipboard:write", "contextBridge"):
    assert needle in text, needle
print("found the planted IPC channels through the broker: OK")
risk = call("tool_call", name="export_evidence_bundle", arguments={})
print("a high-risk tool without approval ->", risk.get("error"))
assert risk.get("error") == "approval_required"
p.stdin.close()
p.wait(timeout=30)
print("E2E REA OK")
