"""a small, dependency-free MCP server core (JSON-RPC 2.0): initialize, ping, tools/list, tools/call, prompts/list. Used over stdio and over HTTP.

Tools are registered with a compact JSON schema; a profile decides which tools a client sees (so a 4B model is not drowned in tool definitions). Tool results go back as one
compact JSON text block plus structuredContent."""
import json, sys, threading, time

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST = SUPPORTED[0]
JSONRPC_ERRORS = dict(parse=-32700, invalid=-32600, method=-32601, params=-32602, internal=-32603)


class Tool:
    def __init__(self, name, description, schema, handler, profiles=("full",), title=None, read_only=False, destructive=False, open_world=False):
        self.name, self.description, self.schema, self.handler = name, description, schema, handler
        self.profiles, self.title = set(profiles), title or name.replace("_", " ").title()
        self.annotations = dict(readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=read_only, openWorldHint=open_world)

    def listing(self):
        return dict(name=self.name, title=self.title, description=self.description, inputSchema=self.schema, annotations=self.annotations)


def validate(schema, value, path="arguments"):
    """just enough JSON Schema for tool inputs: type, required, enum, minimum/maximum, maxLength, items, properties. Returns an error string or None."""
    t = schema.get("type")
    py = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    if t in py:
        if t == "integer" and isinstance(value, bool):
            return "%s must be an integer" % path
        if not isinstance(value, py[t]) or (t == "number" and isinstance(value, bool)):
            return "%s must be %s" % (path, t)
    if "enum" in schema and value not in schema["enum"]:
        return "%s must be one of %s" % (path, ", ".join(map(str, schema["enum"])))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return "%s must be >= %s" % (path, schema["minimum"])
        if "maximum" in schema and value > schema["maximum"]:
            return "%s must be <= %s" % (path, schema["maximum"])
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"] * 4:     # generous: the board cuts to its own caps and says so
        return "%s is far too long (limit about %d characters)" % (path, schema["maxLength"])
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            e = validate(schema["items"], v, "%s[%d]" % (path, i))
            if e:
                return e
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                return "%s.%s is required" % (path, k)
        for k, v in value.items():
            if k in schema.get("properties", {}):
                e = validate(schema["properties"][k], v, "%s.%s" % (path, k))
                if e:
                    return e
    return None


class McpServer:
    def __init__(self, name, version, instructions="", tools=()):
        self.name, self.version, self.instructions = name, version, instructions
        self.tools = {}
        self.lock = threading.Lock()
        self.log = None                                           # optional callable(dict): one line per tool call, for the audit log
        for t in tools:
            self.add(t)

    def add(self, tool):
        with self.lock:
            self.tools[tool.name] = tool

    def remove(self, name):
        with self.lock:
            self.tools.pop(name, None)

    def visible(self, profile):
        with self.lock:
            return [t for t in self.tools.values() if profile == "full" and "full" in t.profiles or profile in t.profiles]

    # ------------------------------------------------------------ JSON-RPC
    def handle(self, msg, session):
        """session: dict with 'profile' and 'actor' (mutated by initialize). Returns a response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return self._err(msg.get("id") if isinstance(msg, dict) else None, "invalid", "not a JSON-RPC 2.0 request")
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        is_note = "id" not in msg
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                ci = params.get("clientInfo") or {}
                if ci.get("name") and not session.get("actor_fixed"):
                    session["actor"] = str(ci["name"])[:60]
                session["initialized"] = True
                return self._ok(mid, dict(protocolVersion=want if want in SUPPORTED else LATEST, capabilities=dict(tools=dict(listChanged=True), prompts=dict(listChanged=False)),
                                          serverInfo=dict(name=self.name, version=self.version), instructions=self.instructions))
            if method == "ping":
                return self._ok(mid, {})
            if method == "tools/list":
                return self._ok(mid, dict(tools=[t.listing() for t in sorted(self.visible(session.get("profile", "full")), key=lambda t: t.name)]))
            if method == "prompts/list":
                return self._ok(mid, dict(prompts=[]))
            if method == "tools/call":
                return self._call(mid, params, session)
            if is_note:
                return None
            return self._err(mid, "method", "method not found: %s" % method)
        except Exception as e:                                       # a bug in a handler must never kill the server
            return self._err(mid, "internal", "%s: %s" % (e.__class__.__name__, e)) if not is_note else None

    def _call(self, mid, params, session):
        name = params.get("name")
        args = params.get("arguments") or {}
        with self.lock:
            tool = self.tools.get(name)
        t0 = time.time()
        if tool is None or tool not in self.visible(session.get("profile", "full")):
            return self._err(mid, "params", "unknown tool: %s" % name)
        bad = validate(tool.schema, args)
        if bad:
            return self._ok(mid, self._result(dict(ok=False, error="invalid_arguments", message=bad), True))
        try:
            out = tool.handler(args, session)
            res = self._result(out, False if not (isinstance(out, dict) and out.get("ok") is False) else True)
        except Exception as e:
            code = getattr(e, "code", None)
            res = self._result(e.as_dict() if hasattr(e, "as_dict") else dict(ok=False, error=code or "tool_error", message="%s: %s" % (e.__class__.__name__, e)), True)
        if self.log:
            try:
                self.log(dict(ts=t0, actor=session.get("actor"), tool=name, ms=round((time.time() - t0) * 1000), error=res.get("isError", False), arg_keys=sorted(args)))
            except Exception:
                pass
        return self._ok(mid, res)

    @staticmethod
    def _result(out, is_error):
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        r = dict(content=[dict(type="text", text=text)], structuredContent=out if isinstance(out, dict) else dict(result=out))
        if is_error:
            r["isError"] = True
        return r

    @staticmethod
    def _ok(mid, result):
        return dict(jsonrpc="2.0", id=mid, result=result)

    @staticmethod
    def _err(mid, kind, message):
        return dict(jsonrpc="2.0", id=mid, error=dict(code=JSONRPC_ERRORS[kind], message=message))

    # ------------------------------------------------------------ stdio transport
    def serve_stdio(self, profile="full", actor=None, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for stream, kw in ((stdin, dict(encoding="utf-8", errors="replace")), (stdout, dict(encoding="utf-8", newline="\n"))):
            try:                                                      # JSON-RPC over stdio is UTF-8 by definition; the Windows default codepage would corrupt non-ASCII text
                stream.reconfigure(**kw)
            except Exception:
                pass
        session = dict(profile=profile, actor=actor or "stdio-client", actor_fixed=bool(actor))
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                stdout.write(json.dumps(self._err(None, "parse", "invalid JSON")) + "\n"); stdout.flush()
                continue
            batch = msg if isinstance(msg, list) else [msg]
            outs = [r for r in (self.handle(m, session) for m in batch) if r is not None]
            if outs:
                stdout.write(json.dumps(outs if isinstance(msg, list) else outs[0], ensure_ascii=False) + "\n")
                stdout.flush()
