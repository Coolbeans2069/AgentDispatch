"""the HTTP side of the intake: /intake (classic), / and /home (Command Centre), /api/intake/... and /api/home/... . Any valid token may use it; creating a job follows the same rules as the agency."""
import base64, json, mimetypes, os, shutil, time
from .. import paths
from urllib.parse import parse_qs, unquote, urlparse

from ..store import BoardError
from ..agency.jobs import CAPS
from . import Intake, chat, media

UPLOAD_LIMIT = 300 * 1024 * 1024


def intake_of(app):
    if getattr(app, "_intake", None) is None:
        app._intake = Intake(app)
    return app._intake


def architect_of(app):
    if getattr(app, "_architect", None) is None:
        from .architect import Architect
        app._architect = Architect(app, intake_of(app))
    return app._architect


def is_upload(path):
    return path.startswith("/api/intake/") and (path.endswith("/upload") or path.endswith("/voice"))


def page(path, app=None):
    from ..theme import apply
    if path in ("/", "/home"):
        from .home_page import HOME_HTML
        html = HOME_HTML
        if app is not None:
            try:
                boot = projects_listing(app)
                # base64 so job "why" / titles can never break the HTML <script> block
                raw = json.dumps(boot, ensure_ascii=False).encode("utf-8")
                b64 = base64.b64encode(raw).decode("ascii")
                html = html.replace("/*__PROJECTS_BOOT__*/", "window.__PROJECTS_BOOT__=JSON.parse(atob('%s'));" % b64, 1)
            except Exception as e:
                html = html.replace("/*__PROJECTS_BOOT__*/", "window.__PROJECTS_BOOT__=null;window.__PROJECTS_BOOT_ERR__=%s;" % json.dumps(str(e)[:200]), 1)
        return apply(html, "home").encode("utf-8")
    if path != "/intake":
        return None
    from .page import INTAKE_HTML
    return apply(INTAKE_HTML, "intake").encode("utf-8")


FRIENDLY = {
    "agentdemo": "Agent demo",
    "agentdemo2": "Agent demo 2",
}


def _readme_title(folder):
    for name in ("README.md", "readme.md", "Readme.md"):
        p = os.path.join(folder, name)
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        return line.lstrip("#").strip()[:120]
                    if line:
                        return line[:120]
        except OSError:
            continue
    return ""


def _norm_path(p):
    return os.path.normpath(p or "").replace("\\", "/").rstrip("/").lower()


def _under_root(path, root):
    a, b = _norm_path(path), _norm_path(root)
    return a == b or a.startswith(b + "/")


def projects_listing(app):
    """folders under the autonomous projects root, with titles, job tallies, and intake sessions."""
    cfg = app.usage.autonomy.load()
    root = os.path.normpath(cfg.get("projects_root") or paths.PROJECTS_ROOT)
    root_n = _norm_path(root)
    # jobs keyed by target folder
    by_target = {}
    try:
        for j in app.jobs.list(None, 200):
            t = _norm_path(j.get("target") or "")
            if not t:
                continue
            by_target.setdefault(t, []).append(dict(id=j["id"], state=j.get("state"), title=(j.get("title") or "")[:70], why=(j.get("why") or "")[:100]))
    except Exception:
        pass
    # intake sessions that mention a project folder
    sessions = []
    try:
        it = intake_of(app)
        for name in os.listdir(it.dir):
            if not name.startswith("S-"):
                continue
            try:
                s = it.get(name)
            except BoardError:
                continue
            target = _norm_path((s.get("form") or {}).get("target") or "")
            title = ((s.get("form") or {}).get("title") or "")[:80]
            sessions.append(dict(id=s["id"], target=target, title=title, status=s.get("status"), phase=s.get("phase"), made=s.get("made") or {}))
    except Exception:
        sessions = []
    projects = []
    try:
        names = sorted(os.listdir(root), key=lambda n: os.path.getmtime(os.path.join(root, n)), reverse=True)
    except OSError:
        names = []
    for name in names[:60]:
        path = os.path.join(root, name)
        if not os.path.isdir(path) or name.startswith("."):
            continue
        nfiles = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules", ".venv", ".tox", "dist", "build")][:30]
                nfiles += len(filenames)
                if nfiles > 2000 or dirpath.count(os.sep) - path.count(os.sep) > 4:
                    break
        except OSError:
            pass
        try:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
        except OSError:
            mtime = ""
        path_n = _norm_path(path)
        jobs = by_target.get(path_n, [])
        # also match jobs whose target is a subfolder of this project
        for t, js in by_target.items():
            if t != path_n and t.startswith(path_n + "/"):
                jobs = jobs + js
        counts = {}
        for j in jobs:
            counts[j["state"] or "?"] = counts.get(j["state"] or "?", 0) + 1
        active = [j for j in jobs if j["state"] in ("running", "queued", "planning", "reviewing", "ready", "blocked", "failed")]
        active.sort(key=lambda j: (0 if j["state"] == "running" else 1 if j["state"] == "failed" else 2, j["id"]))
        slim_jobs = [dict(id=j["id"], state=j["state"], title=(j.get("title") or "")[:70], why=(j.get("why") or "")[:80]) for j in active[:8]]
        sess = [x for x in sessions if x["target"] == path_n or (x["target"] and x["target"].startswith(path_n + "/"))]
        sess.sort(key=lambda x: x["id"], reverse=True)
        title = FRIENDLY.get(name) or _readme_title(path) or name
        blurb = _readme_title(path) if FRIENDLY.get(name) else ""
        projects.append(dict(
            name=name, title=title, blurb=blurb[:160], path=path.replace("\\", "/"),
            files=nfiles, mtime=mtime, job_counts=counts, jobs=slim_jobs,
            sessions=[{"id": x["id"], "title": x["title"], "status": x["status"], "phase": x["phase"]} for x in sess[:5]],
            pct=_project_pct(jobs), open_url="file:///" + path.replace("\\", "/"),
        ))
    return dict(ok=True, root=root.replace("\\", "/"), projects=projects)


def _title_family(title):
    """Strip retry/repair suffixes so an old failed job matches its later merged sibling."""
    t = (title or "").strip().lower()
    for marker in (" (retry", " (live repair", " (repair", " — retry", " - retry"):
        i = t.find(marker)
        if i > 0:
            t = t[:i]
    # keep a stable head (punctuation-insensitive) for fuzzy family match
    head = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in t)
    return " ".join(head.split())[:72]


def _family_covered(fam, done_families):
    """True if this failed title is covered by a merged sibling (exact or shared head)."""
    if not fam:
        return False
    if fam in done_families:
        return True
    for d in done_families:
        if not d:
            continue
        # "project skeleton…" variants, "fold-down wall bracket…" variants, etc.
        a, b = (fam, d) if len(fam) <= len(d) else (d, fam)
        if b.startswith(a) and len(a) >= 18:
            return True
        aw, bw = a.split(), b.split()
        if len(aw) >= 3 and bw[:3] == aw[:3]:
            return True
    return False


def _project_pct(jobs):
    """Share of finished work. Failed/cancelled jobs that were superseded by a later
    merged/delivered job with the same title family do not drag the bar down — otherwise
    a successful retry leaves the project stuck at e.g. 74% forever with nothing to Continue."""
    if isinstance(jobs, dict):
        # legacy callers passing counts only
        counts = jobs
        done = counts.get("merged", 0) + counts.get("delivered", 0)
        fail = counts.get("failed", 0) + counts.get("cancelled", 0) + counts.get("blocked", 0)
        live = counts.get("running", 0) + counts.get("queued", 0) + counts.get("planning", 0) + counts.get("reviewing", 0) + counts.get("ready", 0)
        total = done + fail + live
        return int(round(100.0 * done / total)) if total else 0
    done_jobs = [j for j in jobs if (j.get("state") or "") in ("merged", "delivered")]
    live_jobs = [j for j in jobs if (j.get("state") or "") in ("running", "queued", "planning", "reviewing", "ready")]
    families = {_title_family(j.get("title")) for j in done_jobs}
    fail_jobs = []
    for j in jobs:
        st = j.get("state") or ""
        if st not in ("failed", "cancelled", "blocked"):
            continue
        fam = _title_family(j.get("title"))
        if _family_covered(fam, families):
            continue  # superseded by a successful sibling
        fail_jobs.append(j)
    done, fail, live = len(done_jobs), len(fail_jobs), len(live_jobs)
    total = done + fail + live
    if not total:
        return 0
    return int(round(100.0 * done / total))


def open_project_folder(app, path, role):
    """open a project folder in the OS file manager (Explorer). Only under the projects root. Any valid token may do this on this local broker."""
    cfg = app.usage.autonomy.load()
    root = os.path.normpath(cfg.get("projects_root") or paths.PROJECTS_ROOT)
    raw = os.path.normpath(str(path or "").strip())
    if not raw or not _under_root(raw, root):
        raise BoardError("bad_path", "folder must be inside the projects root (%s)" % root.replace("\\", "/"))
    if not os.path.isdir(raw):
        raise BoardError("not_found", "no such folder: %s" % raw.replace("\\", "/"))
    try:
        if os.name == "nt":
            os.startfile(raw)  # noqa: S606 - path checked under projects root; local broker only
        else:
            import subprocess
            subprocess.Popen(["xdg-open", raw], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise BoardError("open_failed", "could not open folder: %s" % e)
    return dict(ok=True, path=raw.replace("\\", "/"), opened=True)


def get(app, path, q):
    """-> ('json', dict) | ('file', (bytes, mime)) | None"""
    if path == "/api/usage":
        return "json", app.usage.summary()
    if path == "/api/autonomy":
        return "json", dict(ok=True, config=app.usage.autonomy.load(), usage=app.usage.summary())
    if path == "/api/home/projects":
        return "json", projects_listing(app)
    if not path.startswith("/api/intake/"):
        return None
    it = intake_of(app)
    parts = path.split("/")
    try:
        if len(parts) == 4:                                                              # /api/intake/<sid>
            return "json", dict(ok=True, session=it.public(it.get(parts[3])))
        if len(parts) == 6 and parts[4] == "file":                                       # /api/intake/<sid>/file/<fid>  (a preview of what was attached)
            f = it.file(parts[3], parts[5])
            return "file", (open(f["path"], "rb").read(), f["mime"] or mimetypes.guess_type(f["name"])[0] or "application/octet-stream")
    except BoardError as e:
        return "json", e.as_dict()
    return None


def submit(app, sid, actor):
    it = intake_of(app)
    s = it.get(sid)
    f = s["form"]
    if s["status"] != "open":
        raise BoardError("closed", "this intake already became job %s" % s["job"])
    if not f["title"].strip() or not f["goal"].strip():
        raise BoardError("empty", "give the job a title and a goal first")
    goal = f["goal"].strip()
    if f["requirements"]:
        goal += "\n\nRequirements:\n" + "\n".join("- " + r for r in f["requirements"])
    note = "\n".join("- %s (%s): %s" % (x["name"], x["kind"], (x["analysis"].get("summary") or "not looked at yet").replace("\n", " ")[:2000]) for x in s["files"])
    if note:
        goal += "\n\nAttached by the person (looked at by models on this computer):\n" + note
    if len(goal) > CAPS["goal"]:                                                     # never a silent cut: the requirements and attachments appended above used to be dropped at 4000 characters
        raise BoardError("too_long", "the goal with its requirements is %d characters; the limit is %d" % (len(goal), CAPS["goal"]))
    acc = {k: v for k, v in (("check", f["check"]), ("rubric", f["rubric"])) if v}
    if f["template"]:
        j = app.campaign.governor.create_from_template(f["template"], dict(gate=f["gate"], campaign=f["campaign"], goal=goal, title=f["title"], target=f["target"], check=f["check"], priority=f["priority"]), actor)
    else:
        j = app.jobs.create(actor, f["title"].strip(), goal, f["kind"], f["target"].strip(), acc, None, f["priority"], dedupe=not f.get("allow_duplicate"))
        if f["steps"]:
            j = app.jobs.add_steps(j["id"], f["steps"], actor)
    d = os.path.join(app.campaign.evidence.job_dir(j["id"]), "inputs")
    os.makedirs(d, exist_ok=True)
    for x in s["files"]:
        shutil.copy2(x["path"], os.path.join(d, "%s_%s" % (x["id"], x["name"])))
    if s["files"] and j.get("post_id"):
        body = "\n\n".join("%s (%s, %d bytes, sha256 %s)\n%s" % (x["name"], x["kind"], x["size"], x["sha256"][:16], x["analysis"].get("summary", "")) for x in s["files"])
        try:
            app.board.attach(actor, j["post_id"], "intake attachments and what the models saw (files are in %s)" % d, body)
        except BoardError:
            pass
    it.update(sid, lambda se: se.update(status="submitted", job=j["id"]))
    return j


def post(app, path, raw, query, body_json, actor, role):
    """-> (status, dict) or None. `raw` is the uploaded bytes for /upload and /voice."""
    if path == "/api/autonomy":
        if role != "owner":
            return 403, dict(ok=False, error="owner_only", message="only the owner can switch autonomous mode: it lets a ready plan run without asking")
        from . import autonomy
        if body_json.get("enabled") is True:
            cfg = autonomy.enable(app, body_json.get("projects_root"), **{k: body_json.get(k) for k in ("strong_calls_per_session", "strong_calls_per_day", "plan_pct_stop", "max_repairs_per_plan", "supervise_every_s") if isinstance(body_json.get(k), int)})
            sup = getattr(app, "supervisor", None)
            if sup is None:
                app.supervisor = sup = autonomy.Supervisor(app)
            sup.start()
        elif body_json.get("enabled") is False:
            cfg = autonomy.disable(app)
        else:
            cfg = app.usage.autonomy.save(**{k: v for k, v in body_json.items() if k not in ("enabled", "projects_root")})
        return 200, dict(ok=True, config=cfg)
    if path == "/api/home/open_folder":
        try:
            return 200, open_project_folder(app, body_json.get("path"), role)
        except BoardError as e:
            code = 404 if e.code == "not_found" else 400
            return code, e.as_dict()
    if not path.startswith("/api/intake/") and path != "/api/intake":
        return None
    it = intake_of(app)
    parts = path.rstrip("/").split("/")
    try:
        if path.rstrip("/") == "/api/intake/session":
            return 200, dict(ok=True, session=it.public(it.new(body_json.get("autonomous") if isinstance(body_json.get("autonomous"), bool) else None)))
        if path.rstrip("/") == "/api/intake/trial":                                          # the overnight trial: a ready-made, demanding job given to the architect in autonomous mode
            from . import autonomy
            text = autonomy.trial_text(str(body_json.get("name", "csv_report_service")))
            if not text:
                raise BoardError("not_found", "no such trial")
            if not app.usage.autonomy.load()["enabled"]:
                raise BoardError("not_autonomous", "turn autonomous mode on first (the Autonomous switch): the trial runs without anyone approving it")
            s = it.new(True)
            it.say(s["id"], "user", text)
            architect_of(app).start(s["id"], "auto", text)
            return 200, dict(ok=True, session=it.public(it.get(s["id"])))
        sid, action = parts[3], parts[4] if len(parts) > 4 else ""
        if action == "auto":
            text = str(body_json.get("text", "")).strip()[:12000]
            if not text:
                raise BoardError("empty", "say what you want first")
            it.say(sid, "user", text)
            architect_of(app).start(sid, "auto", text)
            return 200, dict(ok=True, session=it.public(it.get(sid)))
        if action in ("think", "plan_submit", "blueprint"):
            from .architect import Architect
            arch = architect_of(app)
            if action == "think":
                arch.start(sid, "think")
                return 200, dict(ok=True, session=it.public(it.get(sid)))
            if action == "blueprint":                                                          # the person edited the blueprint by hand (answers to questions, a changed requirement)
                from . import blueprint as BP

                def fn(se):
                    se["blueprint"] = BP.normalize(body_json.get("blueprint") or {}, se["blueprint"])
                    se["form"] = BP.form_from_blueprint(se["blueprint"], se["form"])
                    se["readiness"] = BP.validate(se["blueprint"], app)
                return 200, dict(ok=True, session=it.public(it.update(sid, fn)))
            from ..agency.tools import slim
            made = arch.submit_plan(sid, actor)
            return 200, dict(ok=True, jobs=[slim(j) for j in made])
        if action == "ops":
            text = str(body_json.get("text", "")).strip()[:2000]
            if not text:
                raise BoardError("empty", "say something first")
            it.say_ops(sid, "user", text)
            s = it.get(sid)
            reply, note, act = chat.derive_ops(app, s, text)
            it.say_ops(sid, "assistant", reply, action=act, note=note)
            if note:
                it.update(sid, lambda se: se.setdefault("thinking", []).append(dict(text="ops: " + note, t=it.now(), phase=se.get("phase") or "interview")))
            return 200, dict(ok=True, session=it.public(it.get(sid)), action=act, note=note)
        if action == "chat":
            text = str(body_json.get("text", "")).strip()[:CAPS["goal"]]
            if not text:
                raise BoardError("empty", "say something first")
            mode = body_json.get("mode", "architect")
            if mode == "architect":
                from .architect import Architect
                arch = architect_of(app)
                s0 = it.get(sid)
                if (s0.get("work") or {}).get("state") == "running":
                    raise BoardError("busy", "I am still thinking about the last thing: wait for it to finish")
                it.say(sid, "user", text)
                arch.start(sid, "interview", text)
                return 200, dict(ok=True, session=it.public(it.get(sid)), changed=[])
            it.say(sid, "user", text)
            s = it.get(sid)
            reply, form, changed = chat.derive(app, s, text, mode="command" if mode == "command" else "local")
            it.update(sid, lambda se: se.update(form=form))
            it.say(sid, "assistant", reply, changed=changed)
            return 200, dict(ok=True, session=it.public(it.get(sid)), changed=changed)
        if action == "form":
            f = body_json.get("form") or {}
            def fn(se):
                """a person's own edits replace what is there (they may also clear a field); each value is still validated"""
                new = dict(se["form"])
                for k, cap in (("title", 120), ("goal", CAPS["goal"]), ("target", 300), ("rubric", CAPS["rubric"]), ("gate", 200), ("campaign", 200)):
                    if isinstance(f.get(k), str):
                        new[k] = f[k].strip()[:cap]
                if f.get("kind") in chat.KINDS:
                    new["kind"] = f["kind"]
                if isinstance(f.get("priority"), int) and 0 <= f["priority"] <= 3:
                    new["priority"] = f["priority"]
                if isinstance(f.get("check"), str):
                    c = chat.merge(dict(se["form"], check=""), dict(check=f["check"]), app.data)[0]["check"] if f["check"].strip() else ""
                    new["check"] = c
                if isinstance(f.get("requirements"), list):
                    new["requirements"] = [str(x).strip()[:chat.REQ_MAX] for x in f["requirements"] if str(x).strip()][:40]
                if isinstance(f.get("steps"), list):
                    new["steps"] = chat.valid_steps(f["steps"])
                if isinstance(f.get("template"), str):
                    new["template"] = chat.merge(dict(se["form"], template=""), dict(template=f["template"]), app.data)[0]["template"] if f["template"] else ""
                se["form"] = new
            return 200, dict(ok=True, session=it.public(it.update(sid, fn)))
        if action in ("upload", "voice"):
            q = {k: v[0] for k, v in parse_qs(query).items()}
            name = unquote(q.get("name", "upload.bin"))
            e = it.add_file(sid, name, raw, q.get("mime", ""))
            if action == "upload":
                if e["analysis"]["state"] == "pending":
                    media.start(app, it, sid, e["id"])
                return 200, dict(ok=True, file={k: v for k, v in e.items() if k != "path"})
            text, note = media.transcribe(app, e["path"], q.get("language") or None)             # voice: transcribe now, then it is a chat message
            it.set_analysis(sid, e["id"], state="done", summary=("Said: " + text) if text else note, details=dict(transcript=text))
            if not text:
                return 200, dict(ok=False, error="no_speech", message="I could not hear any speech: " + note, session=it.public(it.get(sid)))
            return post(app, "/api/intake/%s/chat" % sid, b"", "", dict(text=text, mode=q.get("mode", "architect")), actor, role)
        if action == "analyze":
            fid = str(body_json.get("fid", ""))
            it.file(sid, fid)
            media.start(app, it, sid, fid)
            return 200, dict(ok=True)
        if action == "submit":
            from ..agency.tools import slim
            j = submit(app, sid, actor)
            return 200, dict(ok=True, job=slim(j))
    except BoardError as e:
        return (404 if e.code == "not_found" else 400), e.as_dict()
    return None
