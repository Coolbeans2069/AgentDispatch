"""the lean front door to external tool backends: four small tools (tool_search, tool_describe, tool_call, tool_result) instead of a 500,000-token catalog.
Results come back by reference when large; risky tools need the owner's approval (asked for on the board)."""
import json, os, time, hashlib

from ..backends import BackendError, load_backends
from ..mcp_server import Tool
from .build import build_index, save_index
from .search import Index

MAX_INLINE = 6000            # characters of a tool result returned inline; more is stored and returned by reference
PAGE = 4000
DEFAULT_POLICY = dict(high="ask", medium="allow", low="allow")
RESULTS_MAX_BYTES = 300 * 1024 * 1024
RESULTS_MAX_AGE_S = 7 * 86400


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class IndexService:
    def __init__(self, app):
        self.app = app
        self.data = app.data
        self.backends = load_backends(self.data)
        self.indexes = {}
        self.results_dir = os.path.join(self.data, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        self.reload()

    # ------------------------------------------------------------ index files
    def reload(self):
        base = os.path.join(self.data, "index")
        self.indexes = {}
        if os.path.isdir(base):
            for b in sorted(os.listdir(base)):
                idx = _read(os.path.join(base, b, "index.json"))
                if idx:
                    self.indexes[b] = Index(idx, _read(os.path.join(base, b, "schemas.json"), {}), _read(os.path.join(base, b, "full_schemas.json"), {}), b)

    def locate(self, tool):
        for b, ix in self.indexes.items():
            if tool in ix.index["tools"]:
                return b, ix
        return None, None

    # ------------------------------------------------------------ policy and approvals
    def policy(self):
        return dict(DEFAULT_POLICY, **(_read(os.path.join(self.data, "policy.json"), {}) or {}))

    def approvals(self):
        return _read(os.path.join(self.data, "approvals.json"), {}) or {}

    def approve(self, tool, actor="*", minutes=60):
        a = self.approvals()
        a["%s|%s" % (tool, actor)] = time.time() + 60 * minutes
        tmp = os.path.join(self.data, "approvals.json.tmp")
        json.dump(a, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, os.path.join(self.data, "approvals.json"))
        return a

    def approved(self, tool, actor):
        a, now = self.approvals(), time.time()
        return any(a.get(k, 0) > now for k in ("%s|%s" % (tool, actor), "%s|*" % tool))

    def _need_approval(self, tool, actor, risk):
        pol = self.policy().get(risk, "allow")
        if pol == "deny":
            return dict(ok=False, error="denied", message="policy denies %s-risk tools" % risk)
        if pol == "ask" and not self.approved(tool, actor):
            title = "approval needed: %s (%s risk)" % (tool, risk)
            existing = self.app.board.search(title, 3)["results"]
            pid = next((h["id"] for h in existing if h["type"] == "post" and h.get("state") == "open" and h["title"] == title), None)
            if not pid:
                pid = self.app.board.post("agentdispatch", "issue", title, "%s asked to run `%s`. Owner: approve with `agentdispatch approve %s` or the bulletin page." % (actor, tool, tool), ["approval", risk], severity=2)["id"]
            return dict(ok=False, error="approval_required", message="this %s-risk tool needs the owner's approval; a request is on the board" % risk, request=pid)
        return None

    # ------------------------------------------------------------ results by reference
    def store_result(self, payload):
        raw = json.dumps(payload, ensure_ascii=False)
        rid = "R-" + hashlib.sha1(("%s%s" % (time.time(), raw[:200])).encode()).hexdigest()[:8]
        with open(os.path.join(self.results_dir, rid + ".json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
        self.prune_results()
        return rid, raw

    def prune_results(self, max_bytes=None, max_age_s=None):
        """stored results are a cache, not an archive: drop the old ones, then the oldest until the folder fits the budget"""
        max_bytes = RESULTS_MAX_BYTES if max_bytes is None else max_bytes
        max_age_s = RESULTS_MAX_AGE_S if max_age_s is None else max_age_s
        try:
            files = [(os.path.getmtime(p), os.path.getsize(p), p) for p in (os.path.join(self.results_dir, f) for f in os.listdir(self.results_dir)) if p.endswith(".json")]
        except OSError:
            return 0
        files.sort()
        now, total, dropped = time.time(), sum(f[1] for f in files), 0
        for mt, size, p in files:
            if now - mt > max_age_s or total > max_bytes:
                try:
                    os.remove(p)
                    total -= size
                    dropped += 1
                except OSError:
                    pass
        return dropped

    def read_result(self, ref, page=1):
        if not ref or not ref.startswith("R-") or not ref[2:].isalnum():
            return dict(ok=False, error="bad_ref", message="not a result reference")
        p = os.path.join(self.results_dir, ref + ".json")
        if not os.path.isfile(p):
            return dict(ok=False, error="not_found", message="no such result")
        text = open(p, encoding="utf-8").read()
        pages = max(1, -(-len(text) // PAGE))
        page = max(1, min(pages, int(page or 1)))
        return dict(ok=True, ref=ref, page=page, pages=pages, text=text[(page - 1) * PAGE: page * PAGE])

    # ------------------------------------------------------------ the tools
    def tool_search(self, a, s):
        if not self.indexes:
            return dict(ok=False, error="no_index", message="no tool index built yet: run `agentdispatch index build`")
        b = a.get("backend") or next(iter(self.indexes))
        ix = self.indexes.get(b)
        if not ix:
            return dict(ok=False, error="unknown_backend", message="backends: %s" % ", ".join(self.indexes))
        q, g = a.get("query", ""), a.get("group")
        out = dict(ok=True, backend=b, results=ix.search(q, g, a.get("limit", 8)))
        if not q and not g:
            out["groups"] = ix.groups()
        return out

    def tool_describe(self, a, s):
        res, missing = [], []
        for n in a["names"][:6]:
            b, ix = self.locate(n)
            d = ix.describe(n, bool(a.get("full"))) if ix else None
            (res if d else missing).append(d or n)
        return dict(ok=not missing or bool(res), tools=res, unknown=missing)

    def tool_call(self, a, s):
        name, args = a["name"], a.get("arguments") or {}
        actor = (s or {}).get("actor", "unknown")
        b, ix = self.locate(name)
        if not ix:
            return dict(ok=False, error="unknown_tool", message="no such tool in the index: %s (use tool_search)" % name)
        risk = ix.index["tools"][name]["risk"]
        blocked = self._need_approval(name, actor, risk)
        if blocked:
            self._audit(actor, b, name, risk, 0, blocked["error"])
            return blocked
        be = self.backends.get(b)
        if not be:
            return dict(ok=False, error="no_backend", message="backend %s is not configured (data/backends.json)" % b)
        t0 = time.time()
        try:
            r = be.call_tool(name, args, int(a.get("timeout_s", 300)))
        except BackendError as e:
            self._audit(actor, b, name, risk, time.time() - t0, e.code)
            return e.as_dict()
        self._audit(actor, b, name, risk, time.time() - t0, "error" if (r or {}).get("isError") else "")
        payload = (r or {}).get("structuredContent")
        if payload is None:
            payload = [c.get("text") for c in (r or {}).get("content", []) if c.get("type") == "text"]
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        out = dict(ok=not (r or {}).get("isError"), tool=name, backend=b)
        if len(raw) <= MAX_INLINE:
            out["result"] = payload
        else:
            rid, full = self.store_result(payload)
            out.update(result_ref=rid, bytes=len(full), pages=max(1, -(-len(full) // PAGE)), head=full[:1500], note="large result stored: read it with tool_result(ref, page)")
        return out

    def tool_result(self, a, s):
        return self.read_result(a["ref"], a.get("page", 1))

    def _audit(self, actor, backend, tool, risk, secs, err):
        if self.app.mcp.log:
            try:
                self.app.mcp.log(dict(ts=time.time(), actor=actor, tool="%s:%s" % (backend, tool), risk=risk, ms=round(secs * 1000), error=err or False, kind="backend-call"))
            except Exception:
                pass

    def tools(self):
        obj = lambda props, req=(): dict(type="object", properties=props, required=list(req), additionalProperties=False)
        S = lambda **k: dict(type="string", **k)
        return [
            Tool("tool_search", "Find a tool in the big external catalog (reverse engineering etc.) WITHOUT loading it. Empty query = list the groups. Then tool_describe the one you want.",
                 obj(dict(query=S(maxLength=200), group=S(maxLength=40), limit=dict(type="integer", minimum=1, maximum=30), backend=S(maxLength=30))), self.tool_search, ("lite", "full"), read_only=True),
            Tool("tool_describe", "Get the compact input schema of up to 6 tools found with tool_search. full=true gives the original schema of a tool whose schema was shortened.",
                 obj(dict(names=dict(type="array", items=S(maxLength=80)), full=dict(type="boolean")), ["names"]), self.tool_describe, ("lite", "full"), read_only=True),
            Tool("tool_call", "Run a catalog tool by name with its arguments. Large results come back as a reference (read with tool_result). Risky tools need the owner's approval (a request is posted on the board).",
                 obj(dict(name=S(maxLength=80), arguments=dict(type="object"), timeout_s=dict(type="integer", minimum=1, maximum=1800)), ["name"]), self.tool_call, ("lite", "full"), open_world=True),
            Tool("tool_result", "Read a stored large tool result, one page at a time.", obj(dict(ref=S(maxLength=20), page=dict(type="integer", minimum=1)), ["ref"]), self.tool_result, ("lite", "full"), read_only=True),
        ]


def attach(app):
    """the four meta-tools appear only when there is an index to search (a lite client is not offered tools that cannot work)"""
    svc = IndexService(app)
    if svc.indexes:
        for t in svc.tools():
            app.mcp.add(t)
    return svc


def cli_index(app, args):
    svc = IndexService(app)
    if args.action == "status":
        for b, ix in svc.indexes.items():
            i = ix.index
            print("%s: %d tools in %d groups, built %s, llm=%s, catalog %s..., est. %d tokens compact (raw %d)" % (b, len(i["tools"]), len(i["groups"]), i["built"], i["llm"].get("model"),
                  i["source"]["catalog_sha256"][:10], i["stats"]["compact_tokens_est"], i["stats"]["raw_tokens_est"]))
        return 0 if svc.indexes else 1
    # build
    backend = "rea"
    if args.catalog:
        tools = json.load(open(args.catalog, encoding="utf-8"))
        version = "from-file"
    else:
        be = svc.backends.get(backend)
        if not be:
            print("no backend configured: create data/backends.json or pass --catalog")
            return 2
        tools = be.list_tools()
        version = (be.server_info or {}).get("version", "")
        be.stop()
    old = svc.indexes.get(backend)
    hinters = []
    if not args.no_llm:
        from .llm import GemmaHinter, GEMMA_MODELS
        from .build import default_hinters
        if args.llm in ("auto", "gemma"):                                 # Gemma 4 on the free Gemini tier first (the stronger model), the local model as the fallback
            hinters.append(GemmaHinter(models=(args.model,) if (args.llm == "gemma" and args.model) else GEMMA_MODELS))
        if args.llm in ("auto", "ollama"):
            hinters += default_hinters(args.model if args.llm == "ollama" else None)
    index, schemas = build_index(tools, backend, version, hinters=hinters, use_llm=not args.no_llm, previous=(old.index if old and not args.fresh else None))
    if old and old.index["llm"].get("model") and not index["llm"].get("model") and not args.no_llm:
        print("refusing to replace the existing model-assisted index with a deterministic one (no local model was reachable). Retry, or pass --no-llm to force it.")
        return 3
    d = save_index(app.data, backend, index, schemas, tools)
    print("index saved to %s: %d tools, %d groups; est. tokens raw %d -> compact %d; llm=%s failed_chunks=%d reused_hints=%d" % (
        d, len(index["tools"]), len(index["groups"]), index["stats"]["raw_tokens_est"], index["stats"]["compact_tokens_est"], index["llm"]["model"], index["llm"]["failed_chunks"], index["llm"].get("reused", 0)))
    if index["llm"].get("models"):
        print("hints written by:", ", ".join("%s: %d tools" % kv for kv in index["llm"]["models"].items()))
    return 0
