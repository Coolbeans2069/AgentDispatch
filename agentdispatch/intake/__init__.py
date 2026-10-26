"""the intake: where a person describes the work they want. A chat that fills in the job form in detail and keeps adding to it as they talk, pictures / video / audio dropped in or recorded with the
microphone and camera (looked at afterwards, not live: speech is transcribed and pictures described by models already on this computer), and a button that turns the finished form into a job."""
import hashlib, json, os, re, threading, time, uuid

from ..store import BoardError

MAX_FILE = 300 * 1024 * 1024
MAX_SESSION = 1024 * 1024 * 1024
KINDS = {"image": (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"), "video": (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"), "audio": (".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".weba"),
         "text": (".txt", ".md", ".json", ".csv", ".py", ".log", ".yaml", ".yml", ".toml")}
EMPTY_FORM = dict(title="", goal="", kind="general", target="", check="", rubric="", priority=1, requirements=[], steps=[], template="", gate="", campaign="", notes="")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\- ]")


def kind_of(name, mime=""):
    ext = os.path.splitext(name.lower())[1]
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    for k in ("image", "video", "audio"):
        if (mime or "").startswith(k + "/"):
            return k
    return "other"


class Intake:
    def __init__(self, app, now=time.time):
        self.app, self.now = app, now
        self.dir = os.path.join(app.data, "intake")
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.RLock()

    # ------------------------------------------------------------ sessions
    def _path(self, sid):
        if not re.match(r"^S-[0-9a-f]{8}$", sid or ""):
            raise BoardError("bad_session", "not an intake session id")
        return os.path.join(self.dir, sid)

    def new(self, autonomous=None):
        sid = "S-" + uuid.uuid4().hex[:8]
        if autonomous is None:
            autonomous = bool(self.app.usage.autonomy.load()["enabled"])
        d = self._path(sid)
        os.makedirs(os.path.join(d, "files"), exist_ok=True)
        s = dict(id=sid, created=self.now(), messages=[], ops_messages=[], form=dict(EMPTY_FORM), files=[], status="open", job=None, blueprint={}, phase="interview", work={}, thinking=[], critique=None, readiness=None, autonomous=bool(autonomous), repairs={}, made={})
        self._save(s)
        return s

    def get(self, sid):
        try:
            s = json.load(open(os.path.join(self._path(sid), "session.json"), encoding="utf-8"))
            for k, v in (("blueprint", {}), ("phase", "interview"), ("work", {}), ("thinking", []), ("critique", None), ("readiness", None), ("ops_messages", [])):
                s.setdefault(k, v)
            return s
        except (OSError, ValueError):
            raise BoardError("not_found", "no such intake session: %s" % sid)

    def _save(self, s):
        p = os.path.join(self._path(s["id"]), "session.json")
        tmp = p + ".tmp"
        with self.lock:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(s, f, indent=1, ensure_ascii=False)
            os.replace(tmp, p)

    def update(self, sid, fn):
        with self.lock:
            s = self.get(sid)
            fn(s)
            self._save(s)
            return s

    def public(self, s):
        """the session as the page sees it (no server paths), with the state of every job the plan made"""
        status = {}
        for key, jid in (s.get("made") or {}).items():
            try:
                j = self.app.jobs.get(jid)
                status[key] = dict(job=jid, state=j["state"], why=(j.get("why") or "")[:140], title=j["title"][:70])
            except Exception:
                pass
        return dict(s, files=[{k: v for k, v in f.items() if k != "path"} for f in s["files"]], plan_status=status)

    def say(self, sid, role, text, **extra):
        self.update(sid, lambda s: s["messages"].append(dict(role=role, text=text[:40000], t=self.now(), **extra)))

    def say_ops(self, sid, role, text, **extra):
        self.update(sid, lambda s: s.setdefault("ops_messages", []).append(dict(role=role, text=text[:40000], t=self.now(), **extra)))

    # ------------------------------------------------------------ files
    def add_file(self, sid, name, data, mime=""):
        s = self.get(sid)
        if s["status"] != "open":
            raise BoardError("closed", "this intake already became a job")
        if len(data) > MAX_FILE:
            raise BoardError("too_big", "a file may be at most %d MB" % (MAX_FILE // 2 ** 20))
        if sum(f["size"] for f in s["files"]) + len(data) > MAX_SESSION:
            raise BoardError("too_big", "this intake already holds %d MB of files" % (MAX_SESSION // 2 ** 20))
        name = SAFE_NAME.sub("_", os.path.basename(name or "file"))[:80] or "file"
        fid = "F%d" % (len(s["files"]) + 1)
        path = os.path.join(self._path(sid), "files", "%s_%s" % (fid, name))
        with open(path, "wb") as f:
            f.write(data)
        kind = kind_of(name, mime)
        entry = dict(id=fid, name=name, kind=kind, mime=mime[:60], size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=path, created=self.now(),
                     analysis=dict(state="pending" if kind in ("image", "video", "audio", "text") else "skipped", summary="", details={}))
        self.update(sid, lambda s: s["files"].append(entry))
        return entry

    def file(self, sid, fid):
        f = next((x for x in self.get(sid)["files"] if x["id"] == fid), None)
        if not f:
            raise BoardError("not_found", "no such file")
        return f

    def set_analysis(self, sid, fid, **kv):
        def fn(s):
            for f in s["files"]:
                if f["id"] == fid:
                    f["analysis"].update(kv)
        self.update(sid, fn)
