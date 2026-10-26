"""the night shift: between 00:00 and 06:00 a LOCAL model takes the issues the orchestrator marked for it, tries to solve them in a research -> test -> correct loop, and writes everything on the board.

What it may do is deliberately narrow:
  * it only works on issues whose next tier is 'ollama' (or that nobody has triaged and the ledger says nothing was tried yet);
  * it can run a CHECK - a command from an attachment titled 'check' - but only if the issue's author is on `trusted_authors`, the project copy is under `allowed_roots`, and the command is
    `python <file>` / `python -m pytest|unittest ...`. The check runs in a throw-away COPY of the project; the model's patch is applied to that copy only;
  * a patch that passes its check is attached to the issue as a document and the issue is tagged `patch-ready`: NOTHING is applied to real code here (that is a later, gated step);
  * with no check the model's answer is recorded as an unverified (`partial`) attempt; after `max_attempts` unverified or failed tries the issue is escalated to the next tier.
Configuration: <data>/nightshift.json {trusted_authors, allowed_roots, isolation, model, max_attempts, window, max_cpu_pct}. Out of the box checks work for EVERY agent, because they only ever run inside the container
(`trusted_authors: ["*"]` counts only while the check is confined); with `isolation: none` only the authors you name are trusted. Roots default to the three AgentDispatch repos: add more with `agentdispatch nightshift setup --root PATH`."""
import ntpath, posixpath
from . import paths
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

from .orchestrator import in_night_window, tier_for, LLMUnavailable
from .redact import redact
from .store import BoardError
from .textfit import tail as _ns_tail

DEFAULTS = dict(trusted_authors=["*"], allowed_roots=list(paths.ALLOWED_ROOTS), model="local-qwen3-4b", max_attempts=3, window=[0, 6], max_cpu_pct=70, engine_room=os.environ.get("ENGINEROOM_URL", "http://127.0.0.1:8898"), max_copy_mb=50, check_timeout_s=180,
                isolation="auto", docker_image="python:3.11-slim", max_patch_chars=200000, no_progress_escalate=3)
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "history", ".venv", "venv"}


def skip_here(name, parent, root):
    """a folder name to leave out of a workspace copy: the usual runtime folders anywhere, and `data` only at the top of the broker's own repo (its boards, tokens and databases) - a project's own tests/data or data folder holds fixtures agents and checks need"""
    if name in SKIP_DIRS:
        return True
    return name == "data" and os.path.abspath(parent) == os.path.abspath(root) and os.path.isdir(os.path.join(root, "agentdispatch"))
ALLOWED_RUNNERS = ({"python", "python.exe", "py", "pythonw"},)
SYSTEM = ("You are a careful engineer working at night on one issue from a shared board. Reply with ONE JSON object and nothing else. Never repeat an approach that is listed as a dead end. "
          "Be concrete and short.")


def load_config(data):
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(os.path.join(data, "nightshift.json"), encoding="utf-8")))
    except Exception:
        pass
    return cfg


class CheckError(Exception):
    pass


def parse_check(text):
    try:
        c = json.loads(text)
    except Exception:
        raise CheckError("the check document is not valid JSON")
    if not isinstance(c, dict) or not isinstance(c.get("copy"), str) or not isinstance(c.get("command"), list) or not c["command"] or not all(isinstance(x, str) for x in c["command"]):
        raise CheckError('a check needs {"copy": "<project folder>", "command": ["python", "test_x.py"], "files": [...]}')
    return c


SECRET_NAME = re.compile(r"(^|[\\/._\-])(env|secret|secrets|token|tokens|credential|credentials|passwd|password|auth|auth_config|apikey|api_key|private|pem|pfx|id_rsa|keystore)([\\/._\-]|$)", re.I)


def file_allowed(rel):
    """files that may be shown to a model through a check: nothing that looks like a secret, nothing under runtime or VCS folders"""
    parts = os.path.normpath(str(rel)).replace("\\", "/").split("/")
    if any(p in SKIP_DIRS or p.startswith(".git") for p in parts) or any(SECRET_NAME.search(p) for p in parts):
        return False
    if len(parts) > 1 and parts[0] == "data":                                          # a top-level data folder is runtime data (the broker's boards, tokens); tests/data and other nested data folders are fixtures
        return False
    return True


def command_ok(cmd):
    """python <script.py> [args] | python -m pytest|unittest [args]; no shell, no -c, no other programs"""
    exe = os.path.basename(cmd[0]).lower()
    if exe not in ALLOWED_RUNNERS[0]:
        return False
    rest = cmd[1:]
    if not rest or rest[0] in ("-c", "-i", "-"):
        return False
    if rest[0] == "-m":
        return len(rest) >= 2 and rest[1] in ("pytest", "unittest")
    script = rest[0]
    # absolute in EITHER path style (a check written on Windows can be run on Linux and vice versa): C:\x.py, \\srv\x.py, /x.py
    absolute = ntpath.isabs(script) or posixpath.isabs(script) or bool(ntpath.splitdrive(script)[0])
    return script.endswith(".py") and not absolute and ".." not in script.replace("\\", "/").split("/")


def is_protected(rel, check):
    """files a patch must never touch: the script the check runs, anything that looks like a test, and anything the check lists under `protect` - otherwise a patch could simply rewrite the test it has to pass"""
    p = os.path.normpath(rel).replace("\\", "/").lower().lstrip("./")
    parts = p.split("/")
    script = [a for a in check["command"][1:] if a.endswith(".py")]
    if any(p == os.path.normpath(s).replace("\\", "/").lower().lstrip("./") for s in script):
        return True
    if any(x in ("tests", "test", "testing") or x.startswith("test_") or x.endswith("_test.py") or x == "conftest.py" for x in parts):
        return True
    return any(p == os.path.normpath(x).replace("\\", "/").lower().lstrip("./") for x in check.get("protect", []))


def kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20, creationflags=0x08000000)
        else:
            os.killpg(proc.pid, 9)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


_docker_cache = {}


DOCKER_WHY = {}                                                            # image -> why the last check said no (shown on the job instead of a guess)


def docker_ready(image):
    if image in _docker_cache and time.time() - _docker_cache[image][0] < (60 if _docker_cache[image][1] else 8):         # a "no" is remembered only briefly: Docker Desktop can be slow to wake
        return _docker_cache[image][1]
    ok = False
    DOCKER_WHY[image] = ""
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        ok = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=45, creationflags=flags).returncode == 0
        if not ok:                                                         # Docker 29 with the containerd image store can fail `inspect <name>` for an image that is present and runs: ask the list instead
            r = subprocess.run(["docker", "images", "-q", image], capture_output=True, text=True, timeout=45, creationflags=flags)
            ok = r.returncode == 0 and bool((r.stdout or "").strip())
            if not ok:
                DOCKER_WHY[image] = ("docker answered but the image is not in its list" if r.returncode == 0 else "docker said: " + (r.stderr or r.stdout or "").strip()[:160])
    except Exception as e:
        DOCKER_WHY[image] = "could not run docker: %s: %s" % (e.__class__.__name__, str(e)[:120])
    _docker_cache[image] = (time.time(), ok)
    return ok


class Sandbox:
    """a throw-away COPY of a project; the patch goes in the copy (never over the check's own files); the check runs there.
    Isolation: `docker` (default when the image is present): no network, no host files except the copy, memory/CPU/process limits, all capabilities dropped.
    `none` runs it as a plain child process with a clean environment - model-written code then has the owner's rights, so it is opt-in (nightshift.json "isolation": "none").
    With `auto` and no Docker image the check is refused (CheckError) and the proposal is recorded unverified."""
    def __init__(self, check, cfg):
        cfg = dict(DEFAULTS, **cfg)
        self.check, self.cfg = check, cfg
        src = os.path.abspath(check["copy"])
        roots = [os.path.abspath(r) for r in cfg["allowed_roots"]]
        if not any(src == r or src.startswith(r + os.sep) for r in roots):
            raise CheckError("the project folder is not under an allowed root")
        if not command_ok(check["command"]):
            raise CheckError("that command is not allowed (only `python script.py` or `python -m pytest|unittest`)")
        mode = cfg.get("isolation", "auto")
        if mode == "auto":
            mode = "docker" if docker_ready(cfg["docker_image"]) else "refuse"
        if mode == "refuse":
            raise CheckError("no isolation available (Docker image %s is missing): set \"isolation\": \"none\" in nightshift.json to run unconfined at your own risk" % cfg["docker_image"])
        if mode == "docker" and not docker_ready(cfg["docker_image"]):
            raise CheckError("Docker image %s is not available" % cfg["docker_image"])
        self.mode, self.src = mode, src

    def run(self, patch):
        files = patch or {}
        for rel in files:
            if is_protected(rel, self.check):
                raise CheckError("a patch may not change %r: it is the check itself or a test file" % rel)
        tmp = tempfile.mkdtemp(prefix="nightshift_")
        work = os.path.join(tmp, "project")
        try:
            total = [0]

            def ign(d, names):
                out = []
                for n in names:
                    p = os.path.join(d, n)
                    if skip_here(n, d, self.src) or n.endswith((".pyc", ".sqlite", ".db", ".log")):
                        out.append(n)
                    elif os.path.isfile(p):
                        total[0] += os.path.getsize(p)
                        if total[0] > self.cfg["max_copy_mb"] * 1048576:
                            raise CheckError("the project copy is larger than %d MB" % self.cfg["max_copy_mb"])
                return out
            shutil.copytree(self.src, work, ignore=ign)
            for rel, content in files.items():
                tgt = os.path.abspath(os.path.join(work, rel))
                if not tgt.startswith(os.path.abspath(work) + os.sep) or not isinstance(content, str) or len(content) > self.cfg["max_patch_chars"]:
                    raise CheckError("a patch file path escapes the project copy, or the file is too large: %r" % rel)
                os.makedirs(os.path.dirname(tgt), exist_ok=True)
                with open(tgt, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
            cmd = self.check["command"]
            name = None
            if self.mode == "docker":
                name = "nightshift_%d_%d" % (os.getpid(), int(time.time() * 1000) % 10 ** 9)
                full = ["docker", "run", "--rm", "--name", name, "--network", "none", "--memory", "1g", "--cpus", "2", "--pids-limit", "256", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges", "-v", "%s:/work" % work, "-w", "/work", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONIOENCODING=utf-8",
                        self.cfg["docker_image"], "python"] + cmd[1:]
                if hasattr(os, "getuid"):                                       # Linux / macOS host: with every capability dropped, root in the container cannot read the copy (it is
                    full[2:2] = ["--user", "%d:%d" % (os.getuid(), os.getgid()), "-e", "HOME=/tmp"]       # this user's, mode 700): run as this user instead (Docker Desktop on Windows has no uids)
                env = dict(os.environ, MSYS_NO_PATHCONV="1")
            else:
                env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR") if k in os.environ}
                env.update(TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
                full = [sys.executable if os.path.basename(cmd[0]).lower().startswith("python") else cmd[0]] + cmd[1:]
            flags = (0x00004000 | 0x08000000 | 0x00000200) if os.name == "nt" else 0         # below-normal priority, no window, its own process group
            p = subprocess.Popen(full, cwd=work if self.mode != "docker" else None, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", creationflags=flags, start_new_session=(os.name != "nt"))
            try:
                out, _ = p.communicate(timeout=self.cfg["check_timeout_s"])
                return p.returncode == 0, _ns_tail(out or "", 3800, "check output")      # the end, with a marker when the start was left out (was a silent 800, then 500)
            except subprocess.TimeoutExpired:
                if name:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0)
                kill_tree(p)                                                    # the whole tree, not just the direct child
                try:
                    p.communicate(timeout=10)
                except Exception:
                    pass
                return False, "the check did not finish within %s s" % self.cfg["check_timeout_s"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def propose_prompt(trail, check_info, note=""):
    from .textfit import fit                                                       # whole tries and evidence (they were cut to 160 characters, the issue body to 900): a marker shows any real cut
    dead = "\n".join("- %s: %s -> %s" % (a["attempt"], fit(a["approach"] or "", 2000, "approach"), fit(a["evidence"] or "", 2000, "evidence")) for a in trail["dead_ends"]) or "(none)"
    part = "\n".join("- %s (unverified): %s" % (a["attempt"], fit(a["approach"] or "", 2000, "approach")) for a in trail["partial"]) or "(none)"
    i = trail["issue"]
    return ("Issue %s: %s\n%s\n\nDead ends (do NOT repeat):\n%s\n\nEarlier unverified tries:\n%s\n\n%s%s\n"
            'Return JSON: {"approach":"one or two sentences: the step you will take","analysis":"short reasoning or the answer if this is a question","expected":"what would show it worked",'
            '"patch":null or {"relative/path.py":"the COMPLETE new content of that file"}}' % (i["id"], i["title"], fit(i.get("body") or "", 20000, "issue"), dead, part, check_info, note))


class NightShift:
    def __init__(self, board, llm, cfg=None, actor="ollama-night", now=time.time):
        self.board, self.llm, self.actor, self.now = board, llm, actor, now
        self.cfg = dict(DEFAULTS, **(cfg or {}))

    # ------------------------------------------------------------ what to work on
    def eligible(self, limit=10):
        out = []
        for st in ("open", "escalated"):
            for p in self.board.read(state=st, limit=100)["posts"]:
                if p["kind"] not in ("issue", "question") or p["claimed_by"] or "approval" in p["tags"] or "patch-ready" in p["tags"]:
                    continue
                tr = self.board.trail(p["id"])
                tri = self.board.last_event(p["id"], "triage")
                tier = (tri["data"].get("tier") if tri else None) or tier_for(tr)
                if st == "escalated" or tier != "ollama":
                    continue
                if len([a for a in tr["dead_ends"] + tr["partial"] if self.actor in a["actor"]]) >= self.cfg["max_attempts"]:
                    continue
                out.append(p["id"])
        return out[:limit]

    def machine_ok(self):
        try:
            with urllib.request.urlopen(self.cfg["engine_room"] + "/now", timeout=3) as r:
                d = json.loads(r.read())
            return not d.get("over") and (d.get("cpu") or 0) < self.cfg["max_cpu_pct"]
        except Exception:
            return True

    def find_check(self, trail):
        """-> (document id, who attached it) of the newest document titled 'check', or (None, None)"""
        for d in reversed(trail["docs"]):
            if d["title"].strip().lower() == "check":
                return d["doc"], d.get("actor")
        return None, None

    def confined(self):
        """will a check run inside the container right now (so that running ANY agent's check is safe enough)? Evaluated every time (docker_ready has its own 60 s cache)."""
        mode = self.cfg.get("isolation", "auto")
        return mode == "docker" or (mode == "auto" and docker_ready(self.cfg["docker_image"]))

    def trusted(self, actor):
        names = self.cfg["trusted_authors"]
        return actor in names or ("*" in names and self.confined())

    # ------------------------------------------------------------ the loop
    def run_once(self, force=False, max_issues=3):
        if not force and not in_night_window(self.now(), tuple(self.cfg["window"])):
            return dict(ok=True, ran=False, why="outside the night window")
        report = dict(ok=True, ran=True, issues={})
        for pid in self.eligible(max_issues):
            if not self.machine_ok():
                report["stopped"] = "the machine is busy (engine room limit)"
                break
            try:
                self.board.claim(self.actor, pid, 60)
            except BoardError:
                continue
            try:
                report["issues"][pid] = self.work(pid)
            except LLMUnavailable as e:
                report["stopped"] = "no language model: %s" % e
                self.board.release(self.actor, pid)
                break
            except Exception as e:                                           # one bad issue must not stop the night
                report["issues"][pid] = dict(error="%s: %s" % (e.__class__.__name__, e))
                try:
                    self.board.release(self.actor, pid)
                except Exception:
                    pass
        if report["issues"]:
            self.board.post(self.actor, "status", "night shift report: %d issue(s) worked" % len(report["issues"]),
                            "; ".join("%s -> %s" % (k, (v.get("last") or v.get("error") or "")) for k, v in report["issues"].items())[:1400], ["night-shift"])
        return report

    def work(self, pid):
        res = dict(attempts=0, last="")
        for n in range(self.cfg["max_attempts"]):
            tr = self.board.trail(pid)
            chk_doc, chk_by = self.find_check(tr)
            check, why_no = None, "no check attached: this answer cannot be verified"
            if chk_doc:
                if not self.trusted(chk_by):
                    why_no = "a check is attached but its author (%s) is not trusted to run commands%s" % (chk_by, "" if self.confined() else " outside a container")
                else:
                    try:
                        check = parse_check(self.board.read_doc(chk_doc, 1)["text"])
                        sb = Sandbox(check, self.cfg)
                    except CheckError as e:
                        check, why_no = None, "the check was refused: %s" % e
            info = ("A runnable check exists: it runs `%s` in a copy of the project with your patch applied. Provide a patch.\n" % " ".join(check["command"])) if check else (why_no + ". Answer or propose; it will be recorded as unverified.\n")
            if check:
                info += self.file_context(check, sb)
            raw = self.llm(SYSTEM, propose_prompt(tr, info, "" if n == 0 else "Try a clearly different approach from every earlier one.\n"))
            prop = self.parse(raw)
            if not prop:
                res["last"] = "the model did not return a usable proposal"
                continue
            att = self.board.attempt(self.actor, pid, prop["approach"], prop.get("expected", ""))
            if not att.get("ok"):
                res["last"] = "model repeated a known approach: %s" % att.get("error")
                continue
            res["attempts"] += 1
            if check and prop.get("patch"):
                ok, out = sb.run(prop["patch"])
                if ok:
                    doc = self.board.attach(self.actor, pid, "verified patch (not applied)", json.dumps(dict(check=check, patch=prop["patch"], analysis=prop.get("analysis", "")), indent=1))
                    self.board.result(self.actor, att["attempt"], "worked", "check passed in a sandbox copy; patch attached as %s and NOT applied" % doc["doc"])
                    self.board.reply(self.actor, pid, "Patch verified in a sandbox copy: the check `%s` passes. Attached as %s. Not applied: it needs the apply gate." % (" ".join(check["command"]), doc["doc"]), kind="solution")
                    self.board.add_tag(pid, "patch-ready", self.actor)
                    res["last"] = "verified patch %s" % doc["doc"]
                    self.board.release(self.actor, pid)
                    return res
                self.board.result(self.actor, att["attempt"], "failed", "check failed in the sandbox copy: " + out)
                res["last"] = "check failed"
            else:
                body = "%s\n\n%s" % (prop.get("analysis", ""), why_no)
                self.board.attach(self.actor, pid, "unverified answer %s" % att["attempt"], body)
                self.board.result(self.actor, att["attempt"], "partial", "unverified: %s" % why_no)
                res["last"] = "unverified answer recorded"
        # nothing verified: hand it up
        if res["attempts"] == 0:                                                 # a whole pass without one usable attempt: count it, and give up after a few nights
            self.board.note_event(pid, "night_no_progress", self.actor, dict(last=res["last"][:120]))
            if self.board.count_events(pid, "night_no_progress") >= self.cfg["no_progress_escalate"]:
                self.board.escalate(pid, self.actor, "the night shift could not produce a usable attempt on %d nights" % self.cfg["no_progress_escalate"])
                res["last"] += "; escalated (no progress)"
        tr = self.board.trail(pid)
        mine = [a for a in tr["dead_ends"] + tr["partial"] if self.actor in a["actor"]]
        if len(mine) >= self.cfg["max_attempts"]:
            self.board.escalate(pid, self.actor, "local night shift made %d tries without a verified result" % len(mine))
            res["last"] += "; escalated"
        self.board.release(self.actor, pid)
        return res

    @staticmethod
    def file_context(check, sandbox, budget=6000):
        """the current contents of the files the check names (read from the project, inside its folder only), so the model sees what it is patching"""
        out, used = [], 0
        for rel in (check.get("files") or [])[:6]:
            p = os.path.abspath(os.path.join(sandbox.src, str(rel)))
            if not p.startswith(sandbox.src + os.sep) or not os.path.isfile(p) or not file_allowed(rel):
                continue
            text = redact(open(p, encoding="utf-8", errors="replace").read())[0][:max(0, budget - used)]
            used += len(text)
            out.append("--- %s (current content) ---\n%s" % (rel, text))
        return ("\n".join(out) + "\n") if out else ""

    @staticmethod
    def parse(text):
        try:
            o = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text or "", re.S)
            try:
                o = json.loads(m.group(0)) if m else None
            except Exception:
                o = None
        if not isinstance(o, dict) or not str(o.get("approach") or "").strip():
            return None
        p = o.get("patch")
        if p is not None and not (isinstance(p, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in p.items())):
            p = None
        from .textfit import fit                                                       # the model's own words, kept whole up to generous caps with a visible marker (were silent 600 / 1200 / 300 cuts)
        return dict(approach=fit(str(o["approach"]), 2000, "approach"), analysis=fit(str(o.get("analysis") or ""), 20000, "analysis"), expected=fit(str(o.get("expected") or ""), 2000, "expected"), patch=p)


def daemon(shift, every_s=600, log=print):
    """check every `every_s`: run once per night window (not again until the next day)"""
    last_day = None
    while True:
        day = time.strftime("%Y-%m-%d", time.localtime())
        if day != last_day and in_night_window(time.time(), tuple(shift.cfg["window"])):
            try:
                r = shift.run_once()
                log("night shift: %s" % json.dumps(r)[:300])
            except Exception as e:                                                  # one bad night must not end the daemon
                log("night shift error: %s: %s" % (e.__class__.__name__, e))
            last_day = day
        time.sleep(every_s)


def write_config(data, **changes):
    """update <data>/nightshift.json (keeps what is there, adds roots / trusted names)"""
    p = os.path.join(data, "nightshift.json")
    try:
        cur = json.load(open(p, encoding="utf-8"))
    except Exception:
        cur = {}
    for k in ("allowed_roots", "trusted_authors"):
        add = changes.pop(k, None)
        if add:
            base = list(cur.get(k) or DEFAULTS[k])
            cur[k] = base + [x for x in add if x not in base]
    cur.update({k: v for k, v in changes.items() if v is not None})
    os.makedirs(data, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cur, f, indent=1)
    return cur


def status(board, data):
    cfg = load_config(data)
    shift = NightShift(board, lambda s, u: "", cfg)
    return dict(window="%02d:00-%02d:00" % tuple(cfg["window"]), model=cfg["model"], isolation=cfg["isolation"], docker_image=cfg["docker_image"], docker_ready=docker_ready(cfg["docker_image"]),
                confined=shift.confined(), trusted_authors=cfg["trusted_authors"], allowed_roots=cfg["allowed_roots"], eligible_now=shift.eligible(), in_window_now=in_night_window(time.time(), tuple(cfg["window"])))
