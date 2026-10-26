"""a tiny stdio MCP server used to test the broker's backend handling (no REA needed)"""
import json, sys, os

TOOLS = [
    dict(name="inspect_thing", title="Inspect Thing", description="Inspect one local thing and describe it. More text follows here.",
         inputSchema={"type": "object", "$schema": "x", "properties": {"path": {"type": "string", "description": "Where the thing is."}, "mode": {"$ref": "#/$defs/mode"}}, "required": ["path"],
                      "$defs": {"mode": {"type": "string", "enum": ["fast", "deep"], "description": "How hard to look."}}, "additionalProperties": False},
         outputSchema={"type": "object"}, annotations=dict(readOnlyHint=True)),
    dict(name="make_big_report", title="Make Big Report", description="Produce a very large report.", inputSchema={"type": "object", "properties": {"n": {"type": "integer"}}},
         annotations=dict(readOnlyHint=True)),
    dict(name="delete_everything", title="Delete Everything", description="Deletes a lot.", inputSchema={"type": "object", "properties": {}}, annotations=dict(destructiveHint=True)),
    dict(name="crash_now", title="Crash", description="Exits the process.", inputSchema={"type": "object", "properties": {}}, annotations={}),
]


def send(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    m = json.loads(line)
    mid, method, p = m.get("id"), m.get("method"), m.get("params") or {}
    if method == "initialize":
        send(dict(jsonrpc="2.0", id=mid, result=dict(protocolVersion="2025-06-18", capabilities=dict(tools={}), serverInfo=dict(name="fake", version="9.9"))))
    elif method == "tools/list":
        send(dict(jsonrpc="2.0", id=mid, result=dict(tools=TOOLS)))
    elif method == "tools/call":
        n, a = p["name"], p.get("arguments") or {}
        if n == "crash_now":
            if os.environ.get("FAKE_COUNT_FILE"):
                open(os.environ["FAKE_COUNT_FILE"], "a").write("x")
            os._exit(3)
        if n == "make_big_report":
            out = dict(rows=["row %05d lorem ipsum dolor sit amet" % i for i in range(int(a.get("n", 800)))])
        else:
            out = dict(ok=True, echo=a, tool=n)
        send(dict(jsonrpc="2.0", id=mid, result=dict(content=[dict(type="text", text=json.dumps(out))], structuredContent=out)))
    elif mid is not None:
        send(dict(jsonrpc="2.0", id=mid, error=dict(code=-32601, message="no")))
