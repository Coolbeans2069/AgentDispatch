"""a throw-away working copy for one job: `project/` is what agents change, `base/` is the untouched copy it started from, so the agency can say exactly what changed (files, lines, a unified diff)
without ever touching the real repo. Applying anything to the real repo is a separate, owner-controlled step (Stage 2)."""
import difflib, hashlib, os, posixpath, re, shutil, subprocess, tarfile

from ..nightshift import SKIP_DIRS, file_allowed, skip_here
from ..redact import redact

BINARY_EXT = (".wav", ".mp3", ".ogg", ".flac", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".zip", ".gz", ".7z", ".exe", ".dll", ".so", ".pyc", ".pdf", ".mp4", ".mkv", ".avi", ".stl", ".step", ".fcstd", ".onnx", ".pt", ".safetensors", ".bin")
DEFAULT_MAX_MB = 50
MAX_FILE_CHARS = 150000


class WorkspaceError(Exception):
    pass


def _norm(rel):
    return os.path.normpath(str(rel)).replace("\\", "/")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Workspace:
    def __init__(self, root, job_id, stamp=None):
        """`stamp` (the job's creation time) makes the folder unique to THIS job: ids restart if a board is ever reset or a second board shares the folder, and a stale copy must never be reused"""
        self.dir = os.path.join(root, "%s_%d" % (job_id, int(stamp)) if stamp else job_id)
        self.project, self.base = os.path.join(self.dir, "project"), os.path.join(self.dir, "base")

    @classmethod
    def ensure(cls, root, job_id, source, max_mb=DEFAULT_MAX_MB, stamp=None):
        """copy the source folder into project/ and base/ once; later steps reuse (and build on) the same copy"""
        ws = cls(root, job_id, stamp)
        if os.path.isdir(ws.project) and os.path.isdir(ws.base):
            return ws
        src = os.path.abspath(source)
        if not os.path.isdir(src):
            raise WorkspaceError("the target folder does not exist: %s" % source)
        total = [0]

        def ign(d, names):
            out = []
            for n in names:
                p = os.path.join(d, n)
                if skip_here(n, d, src) or n.endswith((".pyc", ".sqlite", ".db", ".log")):
                    out.append(n)
                elif os.path.isfile(p):
                    total[0] += os.path.getsize(p)
                    if total[0] > max_mb * 1048576:
                        raise WorkspaceError("the target is larger than %d MB" % max_mb)
            return out
        try:
            shutil.copytree(src, ws.project, ignore=ign)
            shutil.copytree(ws.project, ws.base)
        except Exception:
            shutil.rmtree(ws.dir, ignore_errors=True)
            raise
        return ws

    @classmethod
    def ensure_git(cls, root, job_id, repo, commit, allow=None, max_mb=DEFAULT_MAX_MB, stamp=None, git="git"):
        """the workspace as the repository was AT A COMMIT (an evidence cutoff): `git archive` of that commit, only the paths `allow(rel)` accepts. Nothing newer can appear in it, whatever
        the job's scope says. -> (workspace, full commit hash)"""
        ws = cls(root, job_id, stamp)
        flags = ["-c", "safe.directory=*", "-C", repo]
        r = subprocess.run([git] + flags + ["rev-parse", "--verify", commit + "^{commit}"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise WorkspaceError("the evidence cutoff %r is not a commit of %s: %s" % (commit, repo, (r.stderr or r.stdout).strip()[:200]))
        full = r.stdout.strip()
        if os.path.isdir(ws.project) and os.path.isdir(ws.base):
            return ws, full
        total = 0
        os.makedirs(ws.project, exist_ok=True)
        p = subprocess.Popen([git] + flags + ["archive", "--format=tar", full], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=p.stdout, mode="r|") as tf:
                for m in tf:
                    if not m.isfile():
                        continue
                    rel = _norm(m.name)
                    if os.path.isabs(rel) or rel.startswith("../") or "/../" in "/" + rel or any(part in SKIP_DIRS for part in rel.split("/")[:-1]) or rel.endswith((".pyc", ".sqlite", ".db", ".log")):
                        continue
                    if allow and not allow(rel):
                        continue
                    total += m.size
                    if total > max_mb * 1048576:
                        raise WorkspaceError("the cutoff snapshot is larger than %d MB: narrow the read scope" % max_mb)
                    dst = os.path.join(ws.project, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        shutil.copyfileobj(tf.extractfile(m), f)
            p.wait(timeout=60)
            shutil.copytree(ws.project, ws.base)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
            shutil.rmtree(ws.dir, ignore_errors=True)
            raise
        return ws, full

    def files(self, folder=None):
        root = folder or self.project
        out = []
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for n in names:
                p = os.path.join(d, n)
                out.append(_norm(os.path.relpath(p, root)))
        return sorted(out)

    def read(self, rel, folder=None):
        with open(os.path.join(folder or self.project, rel), encoding="utf-8", errors="replace") as f:
            return f.read()

    def context(self, objective="", budget=24000, priority=(), skip=()):
        """(tree listing, [(path, content)]) within `budget` characters: files named in the objective first, then `priority` (the check script, tests), then the rest smallest first.
        Secret-looking files are never included; secrets inside included files are redacted."""
        paths = [p for p in self.files() if file_allowed(p) and p not in skip]
        low = (objective or "").lower()
        named = [p for p in paths if os.path.basename(p).lower() in low or p.lower() in low]
        prio = [p for p in paths if p in set(_norm(x) for x in priority) and p not in named]
        rest = sorted((p for p in paths if p not in named and p not in prio), key=lambda p: os.path.getsize(os.path.join(self.project, p)))
        picked, used, seen, queue_followed = [], 0, set(), set()
        queue = list(named + prio)
        textual = (".py", ".md", ".txt", ".json", ".html", ".js", ".css", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".bat", ".ps1", ".sh")
        order_rest = iter(rest)
        while True:
            if queue:
                p = queue.pop(0)
            else:
                p = next(order_rest, None)
                if p is None:
                    break
            if p in seen or p in skip:
                continue
            seen.add(p)
            if not p.lower().endswith(textual):
                continue
            try:
                text = redact(self.read(p))[0]
            except OSError:
                continue
            if len(text) > MAX_FILE_CHARS or used + len(text) > budget:
                continue
            picked.append((p, text))
            used += len(text)
            if p in named or p in prio or p in queue_followed:                     # what the important files depend on comes next: the modules the check imports, the data files it opens
                for dep in self.depends_on(p, text, paths):
                    if dep not in seen:
                        queue_followed.add(dep)
                        queue.insert(0, dep)
        return paths, picked

    def depends_on(self, rel, text, paths):
        """the project files a file refers to: modules it imports (import x / from x import y / from . import z) and quoted relative paths that exist. Only files in the project; no execution."""
        have, out = set(paths), []
        base = posixpath.dirname(rel)
        mods = set()
        for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, *]+)|import\s+([\w., ]+))", text, re.M):
            if m.group(1):
                mods.add(m.group(1))
                for n in re.split(r"[ ,]+", m.group(2).strip()):
                    if n and n != "*":
                        mods.add(m.group(1) + "." + n)
            else:
                mods.update(x.strip().split(" as ")[0] for x in m.group(3).split(","))
        for mod in mods:
            rel_mod = mod.lstrip(".").replace(".", "/")
            for cand in (rel_mod + ".py", rel_mod + "/__init__.py", posixpath.join(base, rel_mod) + ".py"):
                c = posixpath.normpath(cand)
                if c in have and c != rel and c not in out:
                    out.append(c)
        for m in re.finditer(r"""["']([\w./-]{3,80}\.(?:json|ya?ml|toml|txt|md|csv|py))["']""", text):
            c = posixpath.normpath(posixpath.join(base, m.group(1)))
            for cand in (c, posixpath.normpath(m.group(1))):
                if cand in have and cand != rel and cand not in out:
                    out.append(cand)
        return out[:12]

    def write(self, rel, content, protected=lambda rel: False):
        """write one file into project/; -> (lines added + removed vs what was there). Refuses paths that leave the folder, protected files and huge files."""
        n = _norm(rel)
        if os.path.isabs(n) or n.startswith("../") or "/../" in "/" + n or n in ("", "."):
            raise WorkspaceError("path escapes the project: %r" % rel)
        if protected(n):
            raise WorkspaceError("%r is protected (the check itself or a test file)" % rel)
        if isinstance(content, str) and (("\x00" in content) or n.lower().endswith(BINARY_EXT)):
            raise WorkspaceError("%r looks like a binary file: agents write text only (generate binary files with a tool at run time instead)" % rel)
        if not isinstance(content, str) or len(content) > MAX_FILE_CHARS:
            raise WorkspaceError("%r: content must be text of at most %d characters" % (rel, MAX_FILE_CHARS))
        tgt = os.path.abspath(os.path.join(self.project, n))
        if not tgt.startswith(os.path.abspath(self.project) + os.sep):
            raise WorkspaceError("path escapes the project: %r" % rel)
        old = ""
        if os.path.isfile(tgt):
            with open(tgt, encoding="utf-8", errors="replace") as f:
                old = f.read()
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        with open(tgt, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return sum(1 for l in difflib.ndiff(old.splitlines(), content.splitlines()) if l[:1] in "+-")

    def changes(self):
        """-> {rel: 'added' | 'modified' | 'removed'} comparing project/ with base/"""
        a, b = set(self.files(self.project)), set(self.files(self.base))
        out = {}
        for p in a - b:
            out[p] = "added"
        for p in b - a:
            out[p] = "removed"
        for p in a & b:
            if _sha(os.path.join(self.project, p)) != _sha(os.path.join(self.base, p)):
                out[p] = "modified"
        return out

    def diff(self, max_chars=8000):
        parts = []
        for p, kind in sorted(self.changes().items()):
            old = self.read(p, self.base).splitlines(True) if kind != "added" else []
            new = self.read(p).splitlines(True) if kind != "removed" else []
            parts.append("".join(difflib.unified_diff(old, new, "a/" + p, "b/" + p, n=2)))
        text = redact("\n".join(parts))[0].replace("\x00", "")
        return text if len(text) <= max_chars else text[:max_chars] + "\n... (diff cut)"

    def diff_budget(self, max_chars):
        """-> (text, cut): the change as unified diffs that fit in `max_chars`. Small files go in whole first; a file that does not fit is NOT truncated mid-way (half a module is worse than none for a
        reviewer): it is named in `cut` with its size, so the reviewer knows exactly what it did not see."""
        parts = []
        for p, kind in sorted(self.changes().items()):
            old = self.read(p, self.base).splitlines(True) if kind != "added" else []
            new = self.read(p).splitlines(True) if kind != "removed" else []
            text = redact("".join(difflib.unified_diff(old, new, "a/" + p, "b/" + p, n=2)))[0].replace("\x00", "")
            parts.append((p, kind, text))
        out, cut, used = [], [], 0
        for p, kind, text in sorted(parts, key=lambda x: len(x[2])):
            if used + len(text) <= max_chars:
                out.append((p, text))
                used += len(text)
            else:
                cut.append("%s (%s, %d lines)" % (p, kind, text.count("\n")))
        order = {p: i for i, (p, _, _) in enumerate(parts)}
        out.sort(key=lambda x: order[x[0]])
        return "\n".join(t for _, t in out), cut

    def diff_lines(self):
        n = 0
        for p, kind in self.changes().items():
            old = self.read(p, self.base).splitlines() if kind != "added" else []
            new = self.read(p).splitlines() if kind != "removed" else []
            n += sum(1 for l in difflib.ndiff(old, new) if l[:1] in "+-")
        return n

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
