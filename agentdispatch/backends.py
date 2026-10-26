"""external MCP servers (REA, later others) that the broker runs and talks to over stdio. Started lazily, restarted if they die, stopped when idle, always with a deadline."""
import json, os, subprocess, threading, time


class BackendError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return dict(ok=False, error=self.code, message=str(self))


class StdioMcpClient:
    def __init__(self, name, command, env=None, cwd=None, startup_timeout=90, idle_stop_s=600):
        self.name, self.command, self.env, self.cwd = name, list(command), env or {}, cwd
        self.startup_timeout, self.idle_stop_s = startup_timeout, idle_stop_s
        self.proc = None
        self.lock = threading.RLock()
        self.pending = {}
        self.next_id = 0
        self.last_used = 0.0
        self.server_info = {}
        self.stderr_tail = []
        self._reaper = None

    # ------------------------------------------------------------ lifecycle
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        with self.lock:
            if self.alive():
                return
            env = dict(os.environ)
            env.update(self.env)
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.cwd, env=env,
                                         creationflags=0x08000000 if os.name == "nt" else 0)
            self.pending = {}
            threading.Thread(target=self._read_out, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._read_err, args=(self.proc,), daemon=True).start()
            r = self._rpc("initialize", dict(protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="agentdispatch", version="0.1")), self.startup_timeout)
            self.server_info = (r or {}).get("serverInfo", {})
            self._send(dict(jsonrpc="2.0", method="notifications/initialized"))
            if self._reaper is None:
                self._reaper = threading.Thread(target=self._reap_idle, daemon=True)
                self._reaper.start()

    def stop(self):
        with self.lock:
            p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()

    def _reap_idle(self):
        while True:
            time.sleep(15)
            if self.alive() and self.idle_stop_s and time.time() - self.last_used > self.idle_stop_s and not self.pending:
                self.stop()

    # ------------------------------------------------------------ io
    def _read_out(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            slot = self.pending.get(msg.get("id")) if isinstance(msg, dict) else None
            if slot:
                slot["msg"] = msg
                slot["ev"].set()
        for slot in list(self.pending.values()):                    # the process ended: wake everyone waiting
            slot.setdefault("msg", None)
            slot["ev"].set()

    def _read_err(self, proc):
        for line in proc.stderr:
            self.stderr_tail = (self.stderr_tail + [line.decode("utf-8", "replace").rstrip()[:300]])[-20:]

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _rpc(self, method, params, timeout):
        with self.lock:
            self.next_id += 1
            mid = self.next_id
            slot = dict(ev=threading.Event(), msg=None)
            self.pending[mid] = slot
        try:
            self._send(dict(jsonrpc="2.0", id=mid, method=method, params=params or {}))
            if not slot["ev"].wait(timeout):
                raise BackendError("timeout", "%s did not answer %s within %ds" % (self.name, method, timeout))
            msg = slot["msg"]
            if msg is None:
                raise BackendError("backend_died", "%s exited: %s" % (self.name, " | ".join(self.stderr_tail[-3:])))
            if "error" in msg:
                raise BackendError("backend_error", "%s: %s" % (self.name, msg["error"].get("message", msg["error"])))
            return msg.get("result")
        finally:
            self.pending.pop(mid, None)

    def request(self, method, params=None, timeout=60, retry=True):
        """retry=False for tools/call: a call that may already have had its effect must never be run a second time automatically"""
        self.last_used = time.time()
        for attempt in (1, 2) if retry else (1,):
            try:
                self.start()
                return self._rpc(method, params, timeout)
            except (BrokenPipeError, OSError):
                self.stop()
                if attempt == 2 or not retry:
                    raise BackendError("backend_died", "%s could not be reached: %s" % (self.name, " | ".join(self.stderr_tail[-3:])))
            except BackendError as e:
                if e.code == "backend_died":
                    self.stop()
                    if retry and attempt == 1:
                        continue
                raise
        return None

    def list_tools(self, timeout=120):
        tools, cursor = [], None
        for _ in range(50):
            r = self.request("tools/list", ({"cursor": cursor} if cursor else {}), timeout)
            tools += r.get("tools", [])
            cursor = r.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name, arguments, timeout=300):
        return self.request("tools/call", dict(name=name, arguments=arguments or {}), timeout, retry=False)


def load_backends(data_dir):
    """data/backends.json: {"rea": {"command": [...], "env": {...}, "startup_timeout": 90, "idle_stop_s": 600}}"""
    p = os.path.join(data_dir, "backends.json")
    try:
        cfg = json.load(open(p, encoding="utf-8"))
    except Exception:
        cfg = {}
    return {n: StdioMcpClient(n, c["command"], c.get("env"), c.get("cwd"), c.get("startup_timeout", 90), c.get("idle_stop_s", 600)) for n, c in cfg.items()}
