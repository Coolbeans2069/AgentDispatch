"""the one door a containerised agent has to the rest of the machine. The container is firewalled to this address only (see agent_entry.sh); every request carries a per-run token in its path and is
handled here:
  POST /g/<token>/ollama/v1/messages   -> Ollama's Anthropic-compatible endpoint, with the model forced to the one the router chose for this run (the agent cannot pick another or reach anything else of Ollama)
  POST /g/<token>/mcp                  -> the broker's MCP server, in-process, as an agent identity: only the tools on the capability list, and tool_run is pinned to the run's own workspace copy and job
No key or token of the broker is ever inside the container, and the run token stops working when the run ends."""
import fnmatch, http.client, json, os, posixpath, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MAX_BODY = 16 * 1024 * 1024
MAX_REQUESTS = 3000


class Gateway:
    def __init__(self, app, capabilities, port=8871, ollama="http://127.0.0.1:11434", now=time.time):
        self.app, self.caps, self.port, self.now = app, capabilities, port, now
        u = urlparse(ollama)
        self.ollama_host, self.ollama_port = u.hostname, u.port or 11434
        self.runs, self.lock, self.httpd = {}, threading.Lock(), None

    # ---------------------------------------------------------------- runs
    def open_run(self, job_id, agent_id, model, project, ttl=3600):
        """-> the run token. `model` is the Ollama model the agent's requests are sent to; `project` the host folder of the job's workspace copy (tool_run is confined to it)"""
        tok = secrets.token_hex(16)
        with self.lock:
            self.runs[tok] = dict(job=job_id, agent=agent_id, model=model, project=os.path.realpath(project) if project else None, expires=self.now() + ttl, n=0, actor="agent:%s" % agent_id)
        return tok

    def close_run(self, tok):
        with self.lock:
            self.runs.pop(tok, None)

    def _run(self, tok):
        with self.lock:
            r = self.runs.get(tok)
            if not r or r["expires"] < self.now() or r["n"] >= MAX_REQUESTS:
                return None
            r["n"] += 1
            return r

    def log(self, run, what, **kw):
        try:
            with open(os.path.join(self.app.data, "gateway_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(t=self.now(), job=run["job"], agent=run["agent"], what=what, **kw), default=str) + "\n")
        except OSError:
            pass

    # ---------------------------------------------------------------- MCP
    def pin_tool_args(self, run, name, args):
        """tool_run works on the host: it is confined to this run's workspace copy and job. -> (args, error)"""
        if name != "tool_run":
            return args, None
        args = dict(args or {})
        root = run["project"]
        for k in ("cwd", "project", "file"):
            v = args.get(k)
            if v in (None, ""):
                continue
            rel = str(v).replace("\\", "/")
            if rel.startswith("/work"):                                                  # the container's view of the project
                rel = rel[len("/work"):].lstrip("/")
            if not root or os.path.isabs(rel) or rel.startswith("..") or ".." in rel.split("/"):
                return args, "%s must be a path inside the project (relative, or under /work)" % k
            full = os.path.realpath(os.path.join(root, rel))
            if not (full == root or full.startswith(root + os.sep)):
                return args, "%s leaves the project" % k
            args[k] = full
        if root and not args.get("cwd"):
            args["cwd"] = root
        args["job_id"] = run["job"]
        return args, None

    def mcp(self, run, msg):
        caps = self.caps.load()
        session = dict(profile="full", actor=run["actor"], actor_fixed=True)
        method = msg.get("method") if isinstance(msg, dict) else None
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            if not self.caps.tool_allowed(name, caps):
                self.log(run, "mcp_refused", tool=name)
                return dict(jsonrpc="2.0", id=msg.get("id"), result=dict(isError=True, content=[dict(type="text", text=json.dumps(dict(ok=False, error="not_allowed", message="%s is not on your capability list. Ask with capability_request(kind=mcp_tool, value=%s, why=...)" % (name, name)))) ]))
            args, err = self.pin_tool_args(run, name, params.get("arguments"))
            if err:
                return dict(jsonrpc="2.0", id=msg.get("id"), result=dict(isError=True, content=[dict(type="text", text=json.dumps(dict(ok=False, error="bad_path", message=err)))]))
            msg = dict(msg, params=dict(params, arguments=args))
            self.log(run, "mcp_call", tool=name)
        out = self.app.mcp.handle(msg, session)
        if method == "tools/list" and out and "result" in out:
            out["result"]["tools"] = [t for t in out["result"].get("tools", []) if self.caps.tool_allowed(t.get("name"), caps)]
        return out

    # ---------------------------------------------------------------- the server
    def start(self):
        gw = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"                                               # the end of a streamed body is the end of the connection

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(200, dict(ok=True)) if self.path == "/health" else self._send(404, dict(ok=False))

            def do_POST(self):
                parts = urlparse(self.path).path.split("/")                              # ['', 'g', token, 'ollama'|'mcp', ...]
                if len(parts) < 4 or parts[1] != "g":
                    return self._send(404, dict(ok=False, error="not found"))
                run = gw._run(parts[2])
                if not run:
                    return self._send(401, dict(ok=False, error="this run is over or the token is wrong"))
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    return self._send(413, dict(ok=False, error="request too large"))
                raw = self.rfile.read(n)
                try:
                    if parts[3] == "mcp":
                        msg = json.loads(raw or b"{}")
                        batch = msg if isinstance(msg, list) else [msg]
                        outs = [o for o in (gw.mcp(run, m) for m in batch) if o is not None]
                        if not outs:
                            self.send_response(202)
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        return self._send(200, outs if isinstance(msg, list) else outs[0])
                    if parts[3] == "ollama" and "/".join(parts[4:]) in ("v1/messages", "v1/messages/count_tokens"):
                        return self._ollama(run, "/" + "/".join(parts[4:]), raw)
                except Exception as e:
                    return self._send(500, dict(ok=False, error="%s: %s" % (e.__class__.__name__, str(e)[:200])))
                self._send(404, dict(ok=False, error="that route is not open to agents"))

            def _ollama(self, run, path, raw):
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._send(400, dict(ok=False, error="invalid JSON"))
                body["model"] = run["model"]                                              # whatever the agent asked for, it gets the model the router chose
                data = json.dumps(body).encode("utf-8")
                c = http.client.HTTPConnection(gw.ollama_host, gw.ollama_port, timeout=1800)
                try:
                    c.request("POST", path, data, {"Content-Type": "application/json", "x-api-key": "ollama", "anthropic-version": self.headers.get("anthropic-version", "2023-06-01")})
                    r = c.getresponse()
                    self.send_response(r.status)
                    self.send_header("Content-Type", r.getheader("Content-Type", "application/json"))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    while True:
                        chunk = r.read1(8192) if hasattr(r, "read1") else r.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError):
                    pass
                finally:
                    c.close()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="agent-gateway").start()
        return self

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
