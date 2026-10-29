"""HTTP front end (127.0.0.1 only): POST /mcp (MCP over HTTP), the JSON API behind the bulletin bar, and the bulletin page itself."""
import json, os, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__

MAX_BODY = 1_000_000
ANSWER_BODY = 16 * 1024 * 1024                                                    # an agent's answer (MCP call or /api/agency/submit) can carry several complete files: it is not cut or refused at 1 MB


def tokens_path(data):
    return os.path.join(data, "tokens.json")


def load_tokens(data):
    try:
        return json.load(open(tokens_path(data), encoding="utf-8"))
    except Exception:
        return {}


def new_token(data, name, role="agent"):
    """role 'agent' (every AI client: may use MCP and make suggestions) or 'owner' (the person: may also accept, reject or cancel jobs, allow project folders and choose models)"""
    t = load_tokens(data)
    tok = "sb_" + secrets.token_urlsafe(24)
    t[tok] = dict(actor=name, role=role, created=time.strftime("%Y-%m-%d %H:%M:%S"))
    os.makedirs(data, exist_ok=True)
    with open(tokens_path(data), "w", encoding="utf-8") as f:
        json.dump(t, f, indent=1)
    try:
        os.chmod(tokens_path(data), 0o600)
    except Exception:
        pass
    return tok


class Server(ThreadingHTTPServer):
    allow_reuse_address = False                                      # on Windows SO_REUSEADDR would let a second copy steal the port
    daemon_threads = True


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # ------------------------------------------------------------ plumbing
        def _host_ok(self):
            """DNS-rebinding guard: only requests addressed to this machine by name or loopback address"""
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", "[::1]") or host in EXTRA_HOSTS

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            """(actor, role) for a valid bearer token, else (None, None)"""
            h = self.headers.get("Authorization", "")
            if h.lower().startswith("bearer "):
                t = load_tokens(app.data).get(h[7:].strip())
                if t:
                    return t["actor"], t.get("role", "agent")
            return None, None

        def _body(self, limit=MAX_BODY):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        # ------------------------------------------------------------ routes
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, dict(error="bad host"))
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            b = app.board
            try:
                if u.path == "/board":
                    from .theme import apply
                    return self._send(200, apply(BULLETIN, "board").encode("utf-8"), "text/html")
                if u.path == "/v1/models":
                    if self._auth()[0] is None:
                        return self._send(401, dict(error=dict(message="invalid or missing API key (use a agentdispatch token)", type="invalid_request_error")))
                    return self._send(200, app.router.models())
                if u.path == "/api/board":
                    return self._send(200, b.read(state=q.get("state"), tag=q.get("tag"), kind=q.get("kind"), limit=int(q.get("limit", 50))))
                if u.path == "/api/stats":
                    s = b.stats()
                    s["version"] = __version__
                    s["tools"] = len(app.mcp.tools)
                    s["pending_approvals"] = b.read(state="open", tag="approval")["count"]
                    return self._send(200, s)
                if u.path == "/models":
                    from .models.page import MODELS_HTML
                    from .theme import apply
                    return self._send(200, apply(MODELS_HTML, "models").encode("utf-8"), "text/html")
                if u.path == "/api/models":
                    from .models.tools import slim
                    rows = app.catalog.list(q.get("function") or None, q.get("modality"), q.get("q"), q.get("runner"), q.get("components") == "1", q.get("hidden") == "1", limit=int(q.get("limit", 500)))
                    return self._send(200, dict(ok=True, count=len(rows), models=[dict(slim(m), hidden=m["hidden"], note=m.get("note", ""), mtime=m.get("mtime"), present=m.get("present", True)) for m in rows]))
                if u.path == "/api/models/status":
                    from .models.catalog import FUNCTIONS, FUNC_LABEL
                    sel = {f: dict(s, name=(app.catalog.get(s["model_id"]) or {}).get("label") or (app.catalog.get(s["model_id"]) or {}).get("name")) for f, s in app.catalog.d["selection"].items()}
                    return self._send(200, dict(ok=True, scan=app.catalog.status, scanned=app.catalog.d.get("scanned"), stats=app.catalog.d.get("stats"), roots=app.catalog.d["roots"], counts=app.catalog.counts(),
                                                selection=sel, functions=FUNC_LABEL, total=len(app.catalog.d["models"])))
                if u.path == "/brain":
                    from .agency.brainpage import BRAIN_HTML
                    from .theme import apply
                    return self._send(200, apply(BRAIN_HTML, "brain").encode("utf-8"), "text/html")
                if u.path == "/api/brain":
                    from .agency import brain
                    brain = fresh(brain)                                       # edited on disk since it was loaded: use the new code (a stale module in a long-running broker raised KeyErrors)
                    return self._send(200, brain.snapshot(app, int(q.get("event", 0)), float(q.get("ts", 0)), q.get("job")))
                if u.path == "/agency":
                    from .agency.page import AGENCY_HTML
                    from .theme import apply
                    return self._send(200, apply(AGENCY_HTML, "agency").encode("utf-8"), "text/html")
                if u.path == "/api/agency":
                    return self._send(200, agency_status(app))
                if u.path.startswith("/api/jobs/") and u.path.endswith("/diff"):
                    return self._send(200, job_diff(app, u.path.split("/")[3]))
                if u.path == "/api/jobs":
                    return self._send(200, dict(ok=True, counts=app.jobs.counts(), jobs=app.jobs.list(q.get("state"), int(q.get("limit", 50)))))
                if u.path.startswith("/api/jobs/"):
                    return self._send(200, dict(ok=True, job=app.jobs.get(u.path.rsplit("/", 1)[1])))
                if u.path.startswith("/api/trail/"):
                    return self._send(200, b.trail(u.path.rsplit("/", 1)[1]))
                if u.path.startswith("/api/doc/"):
                    return self._send(200, b.read_doc(u.path.rsplit("/", 1)[1], int(q.get("page", 1))))
                if u.path == "/api/events":
                    return self._send(200, dict(ok=True, events=b.events_since(int(q.get("since", 0)), int(q.get("limit", 100)))))
                if u.path == "/api/search":
                    return self._send(200, b.search(q.get("q", ""), int(q.get("limit", 10))))
                from .intake import web_api as IW
                html = IW.page(u.path, app)
                if html is not None:
                    return self._send(200, html, "text/html")
                r = IW.get(app, u.path, q)
                if r is not None:
                    return self._send(200, r[1][0], r[1][1]) if r[0] == "file" else self._send(200 if r[1].get("ok", True) else 404, r[1])
                from .campaign import web_api
                html = web_api.page(u.path)
                if html is not None:
                    return self._send(200, html, "text/html")
                r = web_api.get(app, u.path, q)
                if r is not None:
                    return self._send(200 if r.get("ok", True) else 404 if r.get("error") == "not_found" else 400, r)
                return self._send(404, dict(error="not found"))
            except Exception as e:
                code = getattr(e, "code", None)
                return self._send(404 if code == "not_found" else 500, e.as_dict() if hasattr(e, "as_dict") else server_error(e, u.path))

        def do_POST(self):
            if not self._host_ok():
                self.close_connection = True                       # the body is not read: do not let it be parsed as the next request on this connection
                return self._send(403, dict(error="bad host"))
            u = urlparse(self.path)
            try:
                from .intake.web_api import is_upload, UPLOAD_LIMIT
                raw = self._body(UPLOAD_LIMIT if is_upload(u.path) else ANSWER_BODY if (u.path.startswith("/mcp") or u.path == "/api/agency/submit") else MAX_BODY)                # always consume the body first (keep-alive safety), then decide
            except ValueError:
                self.close_connection = True
                return self._send(413, dict(error="too large"))
            actor, role = self._auth()
            if actor is None:
                return self._send(401, dict(error="unauthorized", message="send 'Authorization: Bearer <token>' (create one with: python -m agentdispatch token NAME)"))
            try:
                if u.path == "/v1/chat/completions":
                    from .router import RouteError
                    try:
                        req = json.loads(raw or b"{}")
                        if isinstance(req, dict) and req.get("stream"):
                            gen = app.router.chat_stream(req, actor)               # raises RouteError before any output if no backend opens
                            first = next(gen)
                        else:
                            return self._send(200, app.router.chat(req, actor))
                    except RouteError as e:
                        return self._send(e.status, dict(error=dict(message=str(e), type="router_error", tried=e.tried)))
                    except ValueError:
                        return self._send(400, dict(error=dict(message="invalid JSON", type="invalid_request_error")))
                    self.close_connection = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(first.encode("utf-8"))
                        self.wfile.flush()
                        for chunk in gen:                                         # real streaming: each piece is forwarded as the backend produces it
                            self.wfile.write(chunk.encode("utf-8"))
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionError):
                        gen.close()                                               # the client went away: stop reading from the backend
                    return
                if u.path.startswith("/mcp"):
                    profile = "lite" if u.path.rstrip("/").endswith("/lite") else "full"
                    try:
                        msg = json.loads(raw or b"{}")
                    except Exception:
                        return self._send(400, dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="invalid JSON")))
                    session = dict(profile=profile, actor=actor, actor_fixed=True)           # the token decides the author, not the client's self-description
                    batch = msg if isinstance(msg, list) else [msg]
                    outs = [r for r in (app.mcp.handle(m, session) for m in batch) if r is not None]
                    if not outs:
                        return self._send(202, b"", "application/json")
                    return self._send(200, outs if isinstance(msg, list) else outs[0])
                if u.path.startswith("/api/intake") or u.path.startswith("/api/home") or u.path == "/api/autonomy":
                    from .intake import web_api as IW
                    r = IW.post(app, u.path, raw if IW.is_upload(u.path) else b"", u.query, {} if IW.is_upload(u.path) else json.loads(raw or b"{}"), actor, role)
                    if r is not None:
                        return self._send(r[0], r[1])
                body = json.loads(raw or b"{}")
                if u.path == "/api/suggest":
                    return self._send(200, app.board.suggest(actor, body["post_id"], body["model"], body.get("note", "")))
                if u.path in ("/api/models/override", "/api/models/roots", "/api/models/select") and role != "owner" and not (u.path == "/api/models/select" and body.get("by") == "ai"):
                    return self._send(403, dict(ok=False, error="owner_only", message="choosing, renaming, hiding and adding folders are the owner's: use an owner token"))
                if u.path == "/api/models/scan":
                    return self._send(200, dict(ok=True, started=app.catalog.scan_background(float(body.get("seconds", 60)), bool(body.get("deep", True))), status=app.catalog.status))
                if u.path == "/api/models/select":
                    return self._send(200, app.catalog.select(body["function"], body.get("model_id"), body.get("by", "user")))
                if u.path == "/api/models/override":
                    return self._send(200, dict(ok=True, model=app.catalog.override(body["model_id"], **{k: body[k] for k in ("label", "note", "hidden", "favorite", "modality", "subtype") if k in body})))
                if u.path == "/api/models/roots":
                    return self._send(200, dict(ok=True, roots=app.catalog.add_root(body["path"]) if body.get("action", "add") == "add" else app.catalog.remove_root(body["path"])))
                from .campaign import web_api
                r = web_api.post(app, u.path, body, actor, role)
                if r is not None:
                    return self._send(r[0], r[1])
                if u.path in ("/api/agency/next", "/api/agency/submit"):               # agents that ask for work (pull): the token's name must match the agent they act as
                    from .agency.brain import match_agent
                    named = body.get("agent") or match_agent(app.registry, actor)
                    if not named or named not in app.registry.agents or (match_agent(app.registry, actor) != named and role != "owner"):
                        return self._send(403, dict(ok=False, error="not_you", message="this token (%s) cannot act as %r" % (actor, named)))
                    if u.path.endswith("/next"):
                        return self._send(200, app.dispatcher().offer(named))
                    return self._send(200, app.dispatcher().submit(named, str(body.get("task_id", "")), str(body.get("text", ""))))
                if u.path == "/api/jobs":                                              # submit: any valid token (the token fixes the owner name)
                    from .agency.tools import slim
                    j = app.jobs.create(actor, body["title"], body["goal"], body.get("kind", "code"), body.get("target", ""), body.get("acceptance"), body.get("budget"), body.get("priority", 1), body.get("meta"), dedupe=not body.get("allow_duplicate"))
                    if body.get("steps"):
                        j = app.jobs.add_steps(j["id"], body["steps"], actor)
                    return self._send(200, dict(ok=True, job=slim(j)))
                if u.path == "/api/agency/allow_root":                               # the owner lets agents work on copies of a project folder
                    if role != "owner":
                        return self._send(403, dict(ok=False, error="owner_only", message="only the owner can allow a project folder"))
                    import os as _os
                    from .nightshift import load_config, write_config
                    path = _os.path.normpath(str(body.get("path", "")).strip().strip('"')).replace("\\", "/").rstrip("/")
                    if not path or not _os.path.isdir(path) or len(path) < 4:
                        return self._send(400, dict(ok=False, error="bad_folder", message="that is not a folder on this computer: %r" % path))
                    write_config(app.data, allowed_roots=[path])
                    app.localtools.allowed_roots = list(load_config(app.data).get("allowed_roots", []))
                    return self._send(200, dict(ok=True, allowed_roots=load_config(app.data)["allowed_roots"]))
                if u.path in ("/api/jobs/accept", "/api/jobs/reject", "/api/jobs/unblock", "/api/jobs/cancel", "/api/jobs/retry", "/api/jobs/depends") and role != "owner":
                    return self._send(403, dict(ok=False, error="owner_only", message="only the owner can move a job that is waiting for a person"))
                if u.path == "/api/jobs/accept":                                      # a ready change: the owner accepts it, and it is applied to the real folder (backup + check, undone if the check fails)
                    from .agency.jobs import is_code
                    j0 = app.jobs.get(body["job_id"])
                    why = "accepted by the owner"
                    if is_code(j0) and j0.get("target") and body.get("apply", True):
                        outcome = app.dispatcher().apply_fn(j0)
                        if not outcome.get("ok"):
                            return self._send(400, dict(ok=False, error="apply_failed", message="the change was NOT applied: " + outcome.get("why", "")))
                        why = "accepted by the owner; " + outcome.get("summary", "")
                    return self._send(200, dict(ok=True, job=slim_job(app.jobs.set_state(body["job_id"], "merged", actor, why))))
                if u.path == "/api/jobs/reject":
                    return self._send(200, dict(ok=True, job=slim_job(app.jobs.cancel(body["job_id"], actor, "rejected by the owner: " + str(body.get("why", ""))[:200]))))
                if u.path == "/api/jobs/cancel":
                    return self._send(200, dict(ok=True, job=slim_job(app.jobs.cancel(body["job_id"], actor, "cancelled by the owner"))))
                if u.path == "/api/jobs/retry":                                       # a failed / cancelled job: the same job again, planned afresh
                    old = app.jobs.get(body["job_id"])
                    if old["state"] not in ("failed", "cancelled"):
                        return self._send(400, dict(ok=False, error="illegal", message="only a failed or cancelled job can be retried"))
                    new = app.jobs.create(actor, old["title"], old["goal"], old["kind"], old["target"], old.get("acceptance"), old.get("budget"), old["priority"], old.get("meta") or None)
                    remapped = app.jobs.replace_dependency(old["id"], new["id"])   # waiters (e.g. helix jobs) now depend on the retry
                    return self._send(200, dict(ok=True, job=slim_job(new), replaced=old["id"], remapped=remapped))
                if u.path == "/api/jobs/depends":                                     # re-point a job's dependencies: {job_id, drop: [ids], replace: {old: new}}
                    deps = app.jobs.set_dependencies(body["job_id"], actor, body.get("drop") or [], body.get("replace") or {})
                    return self._send(200, dict(ok=True, job_id=body["job_id"], depends_on_jobs=deps))
                if u.path == "/api/jobs/unblock":
                    return self._send(200, dict(ok=True, job=slim_job(app.jobs.unblock(body["job_id"], actor))))
                return self._send(404, dict(error="not found"))
            except Exception as e:
                return self._send(400, e.as_dict() if hasattr(e, "as_dict") else dict(ok=False, error="bad_request", message=str(e)))

    return H


def server_error(e, path=""):
    """a 500 that says what broke and where (class, message, the innermost frame), and is logged: a bare KeyError('job_id') was undiagnosable from the page"""
    import traceback
    tb = traceback.extract_tb(e.__traceback__)
    where = "%s:%d in %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].name) if tb else ""
    msg = "%s: %s" % (e.__class__.__name__, str(e) or "(no message)")
    print("500 %s -> %s at %s" % (path, msg, where), flush=True)
    return dict(ok=False, error="server_error", message=msg, where=where, path=path)


def fresh(module):
    """reload a module when its file changed after it was imported (returns the module to use)"""
    import importlib
    try:
        mt = os.path.getmtime(module.__file__)
        if mt > getattr(module, "_loaded_mtime", 0):
            if hasattr(module, "_loaded_mtime"):
                module = importlib.reload(module)
            module._loaded_mtime = mt
    except Exception:
        pass
    return module


def slim_job(j):
    from .agency.tools import slim
    return slim(j)


def job_diff(app, jid):
    """the workspace copy's change for a job: changed files and a unified diff (secrets redacted); empty when the job has not run or the workspace was cleaned"""
    from .agency import policy
    from .agency.workspace import Workspace
    job = app.jobs.get(jid)
    ws = Workspace(policy.load(app.data)["work_dir"], jid, job["created"])
    import os
    if not os.path.isdir(ws.project):
        return dict(ok=True, changes={}, diff="", note="no workspace yet: the job has not produced changes, or it was cleaned up")
    return dict(ok=True, changes=ws.changes(), diff=ws.diff(60000))


def agency_status(app):
    """what the engine room's Jobs panel shows: counts, what needs the owner, which agents could run right now"""
    from .agency import policy
    cfg = policy.load(app.data)
    agents = []
    for a in app.registry.agents.values():
        ok, why = app.availability.check(a)
        agents.append(dict(id=a["id"], name=a.get("name", a["id"]), cost=a["cost"], available=ok, why=why, runs_today=app.jobs.runs_today(a["id"]), daily_cap=a["daily_cap"]))
    return dict(ok=True, counts=app.jobs.counts(), needs_you=app.jobs.needs_you(), agents=agents, killed=policy.killed(app.data), auto_merge=bool(cfg["auto_merge"]),
                quiet_hours=cfg["quiet_hours"], merges_today=app.jobs.merges_today())


def _start_supervisor(app):
    try:
        from .intake.autonomy import Supervisor
        app.supervisor = Supervisor(app)
        app.supervisor.start()
    except Exception:
        pass


# AGENTDISPATCH_HOST: the address to listen on (default 127.0.0.1). The Docker image sets 0.0.0.0 so a published port reaches it; publish it on 127.0.0.1 only.
# AGENTDISPATCH_ALLOWED_HOSTS: extra Host-header names the DNS-rebinding guard accepts, comma-separated (e.g. the compose service name "agentdispatch").
BIND_HOST = os.environ.get("AGENTDISPATCH_HOST", "").strip() or "127.0.0.1"
EXTRA_HOSTS = frozenset(x.strip().lower() for x in os.environ.get("AGENTDISPATCH_ALLOWED_HOSTS", "").split(",") if x.strip())


def serve(app, port=8870, host=None):
    host = host or BIND_HOST
    srv = Server((host, port), make_handler(app))
    print("agentdispatch %s on http://%s:%d  (MCP: POST /mcp, /mcp/lite  |  home: /  |  board: /board)" % (__version__, "127.0.0.1" if host in ("0.0.0.0", "") else host, port), flush=True)
    _start_supervisor(app)                                                    # repairs failed jobs of autonomous plans (only when autonomous mode is on)

    def housekeeping():
        while True:
            time.sleep(60)
            try:
                app.board.expire_leases()
            except Exception:
                pass
    threading.Thread(target=housekeeping, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


BULLETIN = r"""<!doctype html><meta charset=utf-8><title>AgentDispatch bulletin</title>
<style>
body{margin:0;background:#06121a;color:#d8f8ff;font:13px/1.45 ui-monospace,Consolas,monospace}
header{display:flex;gap:14px;align-items:center;padding:8px 14px;background:#071b29;border-bottom:1px solid #0e6f8e;position:sticky;top:44px;z-index:5}
h1{font-size:14px;letter-spacing:.2em;margin:0;color:#00e5ff}.dim{color:#6aa9bd}
select,input,button{background:#04121c;color:#d8f8ff;border:1px solid #0e6f8e;border-radius:3px;padding:3px 7px;font:inherit}
button{cursor:pointer}button:hover{border-color:#00e5ff}
#bar{display:flex;gap:8px;flex-wrap:wrap;padding:8px 14px}
.item{border:1px solid #0e4a63;border-radius:6px;padding:6px 10px;margin:4px 14px;cursor:pointer;background:#08202e}.item:hover{border-color:#00e5ff}
.st{display:inline-block;min-width:92px;font-weight:700}.open{color:#19f0c8}.claimed,.investigating{color:#f0a83a}.escalated{color:#ff7a1a}.solved{color:#6aa9bd}
#detail{margin:8px 14px;padding:10px;border:1px solid #0e6f8e;border-radius:6px;white-space:pre-wrap;display:none;background:#04121c}
</style>
<header><h1>AGENTDISPATCH BULLETIN</h1><span id=stats class=dim>loading</span>
<span style="flex:1"></span><select id=filter><option value="">open + working</option><option>escalated</option><option>solved</option></select><button id=reload>refresh</button></header>
<div id=detail></div><div id=list></div>
<script>
const $=s=>document.querySelector(s);
const esc=t=>String(t==null?"":t).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function j(u,o){const r=await fetch(u,o);return r.json()}
async function load(){
 const s=await j("/api/stats");$("#stats").textContent="v"+s.version+"  |  "+Object.entries(s.issues||{}).map(([k,v])=>k+" "+v).join("  ")+"  |  "+s.attempts+" attempts  "+s.docs+" docs"+(s.pending_approvals?"  |  "+s.pending_approvals+" approvals waiting":"");
 const st=$("#filter").value;const d=await j("/api/board"+(st?"?state="+st:""));
 $("#list").innerHTML=d.posts.map(p=>'<div class=item data-id="'+p.id+'"><span class="st '+p.state+'">'+p.state+'</span> <b>'+esc(p.id)+'</b> '+esc(p.title)+' <span class=dim>'+esc(p.author)+' | '+p.age_min+' min | tries '+p.attempts+'/'+p.fails+' failed '+(p.tags.length?'| '+esc(p.tags.join(", ")):"")+'</span></div>').join("")||'<div class=item>nothing here</div>';
 document.querySelectorAll(".item[data-id]").forEach(e=>e.onclick=()=>show(e.dataset.id));
}
async function show(id){
 const t=await j("/api/trail/"+id),i=t.issue;let h="<b>"+esc(i.id)+"  "+esc(i.title)+"</b>   ["+t.state+"]\n"+esc(i.body||"")+"\n";
 const sec=(n,a,f)=>a.length?"\n<b>"+n+"</b>\n"+a.map(f).join("\n")+"\n":"";
 h+=sec("Dead ends (do not repeat)",t.dead_ends,a=>"  "+a.attempt+" "+esc(a.actor)+": "+esc(a.approach)+" -> "+esc(a.evidence))+sec("Worked",t.worked,a=>"  "+a.attempt+" "+esc(a.actor)+": "+esc(a.approach))+sec("In progress",t.in_progress,a=>"  "+a.attempt+" "+esc(a.actor)+": "+esc(a.approach))+sec("Documents",t.docs,d=>"  "+d.doc+" "+esc(d.title)+": "+esc(d.summary))+sec("Suggested by people",t.suggestions,s=>"  "+esc(s.by)+" suggests "+esc(s.model)+" "+esc(s.note||""));
 const walk=(ns,dp)=>ns.map(n=>"  ".repeat(dp+1)+"["+esc(n.kind)+"] "+esc(n.id)+" "+esc(n.author)+": "+esc((n.body||n.title).slice(0,300))+"\n"+walk(n.children,dp+1)).join("");
 if(t.threads.length)h+="\n<b>Thread</b>\n"+walk(t.threads,0);
 h+='\n<input id=sm placeholder="model you think should take it (optional)" size=34> <input id=sn placeholder="note" size=30> <button id=sg>suggest</button> <button id=cl>close</button>';
 const el=$("#detail");el.innerHTML=h;el.style.display="block";window.scrollTo(0,0);
 $("#cl").onclick=()=>el.style.display="none";
 $("#sg").onclick=async()=>{const m=$("#sm").value.trim();if(!m)return;const tok=localStorage.getItem("sb_owner_token")||prompt("owner key (create with: python -m agentdispatch token --owner you)");if(!tok)return;localStorage.setItem("sb_owner_token",tok);
  const r=await j("/api/suggest",{method:"POST",headers:{"Authorization":"Bearer "+tok,"Content-Type":"application/json"},body:JSON.stringify({post_id:id,model:m,note:$("#sn").value})});if(r.ok)show(id);else{alert(r.message||r.error);localStorage.removeItem("sb_owner_token")}}
}
$("#reload").onclick=load;$("#filter").onchange=load;load();setInterval(load,15000);
</script>"""
