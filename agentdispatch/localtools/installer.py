"""getting a tool this computer does not have. The recipes are fixed and few (pip packages from a short list into a private environment, the official Lean installer from GitHub, winget packages
from a short list); a model can only REQUEST one. The owner approves each request on the Tools page (or sets `auto_install: ["pip"]` in localtools.json to let the program install the allow-listed
Python packages by itself). Nothing is installed for a request that names something outside the lists."""
import json, os, re, shutil, subprocess, sys, threading, time, urllib.request, zipfile

from ..nightshift import kill_tree
from .registry import NOWIN, SPECS

PIP_ALLOW = {s["pip"] for s in SPECS if s.get("pip")} | {"psutil", "pillow", "opencv-python-headless", "tqdm"}
WINGET_ALLOW = {"Gyan.FFmpeg", "JohnMacFarlane.Pandoc", "UB-Mannheim.TesseractOCR", "jqlang.jq", "yt-dlp.yt-dlp"}
ELAN_URL = "https://github.com/leanprover/elan/releases/latest/download/elan-x86_64-pc-windows-msvc.zip"
URL_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
SIZE_HINT = {"pip": "a few MB to ~100 MB from pypi.org", "elan": "~250 MB from github.com (the Lean toolchain)", "winget": "from the winget source"}


class Installer:
    def __init__(self, box, spawn=None, fetch=None):
        """`spawn(argv, env, timeout) -> (rc, text)` and `fetch(url, dest)` are injectable so the flow is testable without touching the network"""
        self.box, self.lock = box, threading.RLock()
        self.path = os.path.join(box.data, "installs.json")
        self.spawn, self.fetch = spawn or self._spawn, fetch or self._fetch
        self.items = []
        try:
            self.items = json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ places
    def home(self):
        h = self.box.cfg.get("tools_home") or ""
        if not h:
            h = os.path.join(self.box.data, "tools_home")
        os.makedirs(h, exist_ok=True)
        return h

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.items[-200:], f, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ plan: what installing a tool would do
    def plan(self, tool_id):
        s = self.box.reg.spec(tool_id)
        if not s:
            return None
        if s.get("pip"):
            if s["pip"] not in PIP_ALLOW:
                return None
            return dict(kind="pip", packages=[s["pip"]], size=SIZE_HINT["pip"], source="pypi.org", into=os.path.join(self.home(), "venv"))
        r = s.get("recipe") or ""
        if r == "elan":
            return dict(kind="elan", url=ELAN_URL, size=SIZE_HINT["elan"], source="github.com/leanprover/elan", into=os.path.join(self.home(), "elan"))
        if r.startswith("winget:") and r.split(":", 1)[1] in WINGET_ALLOW:
            return dict(kind="winget", id=r.split(":", 1)[1], size=SIZE_HINT["winget"], source="winget")
        return None

    # ------------------------------------------------------------ requests and approvals
    def request(self, tool_id, who, reason="", python=None):
        """a model (or the owner) asks for a tool. -> dict(ok, state, id). Already installed / already asked / not installable are answered, not queued."""
        s = self.box.reg.spec(tool_id)
        if not s:
            return dict(ok=False, error="unknown_tool", message="no such tool: %s" % tool_id)
        if self.box.reg.scan()["tools"].get(tool_id, {}).get("available"):
            return dict(ok=True, state="installed", message="%s is already installed" % s["name"])
        pl = self.plan(tool_id)
        if pl and python and pl["kind"] == "pip":
            if not re.match(r"^3\.\d{1,2}$", str(python)):
                return dict(ok=False, error="bad_python", message="python must look like 3.12")
            pl["python"] = str(python)
        if not pl:
            return dict(ok=False, error="not_installable", message="%s has no install recipe; use tool_need to have an agent engineer it" % s["name"])
        with self.lock:
            for it in self.items:
                if it["tool"] == tool_id and it["state"] in ("pending", "approved", "running"):
                    return dict(ok=True, state=it["state"], id=it["id"], message="already requested")
            it = dict(id="I-%04d" % (len(self.items) + 1), tool=tool_id, name=s["name"], plan=pl, state="pending", requested_by=who, reason=(reason or "")[:300], created=time.time(), log="")
            self.items.append(it)
            self._save()
        auto = self.box.cfg.get("auto_install") or []
        if pl["kind"] in auto:
            return self.approve(it["id"], "auto (owner's setting: auto_install %s)" % pl["kind"])
        return dict(ok=True, state="pending", id=it["id"], message="waiting for the owner's approval on the Tools page")

    def pending(self):
        return [i for i in self.items if i["state"] == "pending"]

    def get(self, iid):
        return next((i for i in self.items if i["id"] == iid), None)

    def approve(self, iid, who="owner", wait=False):
        with self.lock:
            it = self.get(iid)
            if not it:
                return dict(ok=False, error="not_found", message="no such request")
            if it["state"] != "pending":
                return dict(ok=False, error="not_pending", message="this request is %s" % it["state"])
            it.update(state="running", approved_by=who, started=time.time())
            self._save()
        t = threading.Thread(target=self._do, args=(iid,), daemon=True)
        t.start()
        if wait:
            t.join()
        return dict(ok=True, state=self.get(iid)["state"], id=iid)

    def deny(self, iid, who="owner"):
        with self.lock:
            it = self.get(iid)
            if not it or it["state"] != "pending":
                return dict(ok=False, error="not_pending", message="no such pending request")
            it.update(state="denied", denied_by=who)
            self._save()
        return dict(ok=True, state="denied")

    # ------------------------------------------------------------ doing it
    def _do(self, iid):
        it = self.get(iid)
        pl, log = it["plan"], []
        try:
            if pl["kind"] == "pip":
                ok = self._pip(pl, log)
            elif pl["kind"] == "elan":
                ok = self._elan(pl, log)
            elif pl["kind"] == "winget":
                rc, out = self.spawn(["winget", "install", "--id", pl["id"], "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"], None, 1800)
                log.append(out[-800:])
                ok = rc == 0
            else:
                ok = False
        except Exception as e:
            ok = False
            log.append("%s: %s" % (e.__class__.__name__, str(e)[:300]))
        from .toolbox import save_cfg
        save_cfg(self.box.data, self.box.cfg)
        st = self.box.reg.scan(force=True)
        have = st["tools"].get(it["tool"], {}).get("available")
        with self.lock:
            it.update(state="done" if ok and have else "failed", finished=time.time(), log="\n".join(log)[-1500:])
            if ok and not have:
                it["log"] += "\ninstalled, but the tool is still not detected"
            self._save()

    # ------------------------------------------------------------ pip: staged, reviewed in isolation, then promoted
    SMOKE = {"z3-solver": "import z3; s = z3.Solver(); x = z3.Int('x'); s.add(x > 1, x < 3); assert s.check() == z3.sat and s.model()[x] == 2",
             "python-flint": "import flint; assert flint.fmpz(3) * 2 == 6",
             "gmpy2": "import gmpy2; assert gmpy2.mpz(2) ** 10 == 1024",
             "networkx": "import networkx as nx; g = nx.path_graph(4); assert nx.is_connected(g)",
             "sympy": "import sympy as sp; x = sp.symbols('x'); assert sp.integrate(x, x) == x**2 / 2",
             "mpmath": "import mpmath as m; m.mp.dps = 30; assert str(m.pi).startswith('3.14159265358979')",
             "numpy": "import numpy as np; assert int(np.arange(4).sum()) == 6",
             "scipy": "import scipy.integrate as i; assert abs(i.quad(lambda t: t, 0, 1)[0] - 0.5) < 1e-9",
             "hypothesis": "import hypothesis", "pytest": "import pytest", "psutil": "import psutil; assert psutil.cpu_count()", "tqdm": "import tqdm", "pillow": "import PIL",
             "opencv-python-headless": "import cv2"}
    MODULE = {"z3-solver": "z3", "python-flint": "flint", "pillow": "PIL", "opencv-python-headless": "cv2"}

    @staticmethod
    def _is_venv(path):
        d = os.path.dirname(os.path.dirname(os.path.abspath(path)))
        return os.path.isfile(os.path.join(d, "pyvenv.cfg"))

    def base_python(self, version=None):
        """the interpreter new virtual environments are made from: the owner's chosen main install (localtools.json `python`, else the first system Python found); `version` ('3.12') asks for another version
        through the Windows py launcher / python3.x. -> path or None"""
        if version:
            for e in self.box.reg.state.get("pythons", []):
                if (e["version"].startswith(version + ".") or e["version"] == version) and not self._is_venv(e["path"]):
                    return e["path"]
            r = self.spawn(["py", "-%s" % version, "-c", "import sys;print(sys.executable)"], None, 30)
            if r[0] == 0 and os.path.isfile(r[1].strip().splitlines()[-1]):
                return r[1].strip().splitlines()[-1]
            return None
        want = self.box.cfg.get("python")
        if want and os.path.isfile(want) and not self._is_venv(want):
            return want
        for e in self.box.reg.state.get("pythons", []):                                  # a system install, never another virtual environment: a venv made from a venv would not see its packages
            if not self._is_venv(e["path"]):
                return e["path"]
        return sys.executable

    def _manifest_path(self):
        return os.path.join(self.home(), "live_packages.json")

    def _manifest(self, key):
        try:
            return json.load(open(self._manifest_path(), encoding="utf-8")).get(key, [])
        except (OSError, ValueError):
            return []

    def _promote_manifest(self, key, pkgs, py):
        try:
            d = json.load(open(self._manifest_path(), encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d[key] = sorted(set(pkgs))
        d[key + ":python"] = py
        with open(self._manifest_path(), "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, indent=1)

    def _broken(self, py, env, cwd):
        rc, out = self.spawn([py, "-m", "pip", "check"], dict(env), 120, cwd=cwd)
        return [l.strip() for l in out.splitlines() if " has requirement " in l or "no module named" in l.lower()] if rc != 0 else []

    def review_python(self, py, packages, log, base=None):
        """the test review an install must pass BEFORE it is used: in a throw-away folder, with a scrubbed environment and a timeout, every package imports, passes its own functional smoke test, `pip check`
        finds no broken requirements, and nothing was written outside the scratch folder. -> True when everything passed"""
        import tempfile
        scratch = tempfile.mkdtemp(prefix="review_", dir=self.box.scratch)
        env = {k: v for k, v in __import__("agentdispatch.localtools.policy", fromlist=["x"]).clean_env().items()}
        env.update(TEMP=scratch, TMP=scratch)
        ok = True
        try:
            for pkg in packages:
                code = self.SMOKE.get(pkg) or "import %s" % self.MODULE.get(pkg, pkg.replace("-", "_"))
                rc, out = self.spawn([py, "-I", "-B", "-c", code], dict(env), 120, cwd=scratch)
                log.append("review %s: %s" % (pkg, "passed" if rc == 0 else "FAILED " + out[-200:]))
                ok &= rc == 0
            before = set(self._broken(base or sys.executable, env, scratch))                           # conflicts the main install already had are not this install's fault
            after = set(self._broken(py, env, scratch))
            new = sorted(after - before)
            log.append("pip check: " + ("no new broken requirements (%d older ones were already there)" % len(before) if not new else "NEW conflicts: " + "; ".join(new)[:300]))
            ok &= not new
        finally:
            leftovers = [x for x in os.listdir(scratch) if not x.startswith("__")] if os.path.isdir(scratch) else []
            if leftovers:
                log.append("the review wrote files into its scratch folder: %s (cleaned up)" % ", ".join(leftovers[:4]))
            shutil.rmtree(scratch, ignore_errors=True)
        return ok

    def _pip(self, pl, log):
        bad = [p for p in pl["packages"] if p not in PIP_ALLOW or not re.match(r"^[A-Za-z0-9_.\-]+$", p)]
        if bad:
            log.append("refused packages: %s" % bad)
            return False
        ver = pl.get("python")
        key = ver or "base"
        base = self.base_python(ver)
        if not base:
            log.append("no Python %s found (install it, or leave the version empty to use the main install)" % ver)
            return False
        pkgs = sorted(set(self._manifest(key) + pl["packages"]))
        stage = os.path.join(self.home(), "staging", "%s-%d" % (key, int(time.time())))
        os.makedirs(os.path.dirname(stage), exist_ok=True)
        rc, out = self.spawn([base, "-m", "venv", "--system-site-packages", stage], None, 300)
        if rc != 0:
            log.append("could not create the staging environment: " + out[-300:])
            return False
        py = os.path.join(stage, "Scripts", "python.exe") if os.name == "nt" else os.path.join(stage, "bin", "python")
        rc, out = self.spawn([py, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:"] + pkgs, None, 1500)
        log.append("staged install of %s into %s: %s" % (", ".join(pkgs), os.path.basename(stage), "ok" if rc == 0 else out[-400:]))
        if rc != 0 or not self.review_python(py, pl["packages"], log, base):
            shutil.rmtree(stage, ignore_errors=True)
            log.append("REVIEW FAILED: nothing was put into use; the staging environment was removed")
            return False
        self._promote_manifest(key, pkgs, py)                                           # the review passed: this environment is now the live one for this Python version
        self.box.cfg["extra_pythons"] = sorted(set([p for p in (self.box.cfg.get("extra_pythons") or []) if "staging" not in p] + [py]))
        log.append("review passed: %s is live" % os.path.basename(stage))
        return True

    def _elan(self, pl, log):
        if os.name != "nt":
            log.append("the automatic Lean install is only written for Windows; install elan from https://github.com/leanprover/elan")
            return False
        host = re.match(r"https://([^/]+)/", pl["url"]).group(1)
        if host not in URL_HOSTS:
            log.append("refused download host %s" % host)
            return False
        d = os.path.join(self.home(), "dl")
        os.makedirs(d, exist_ok=True)
        z = os.path.join(d, "elan.zip")
        self.fetch(pl["url"], z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(d)
        init = os.path.join(d, "elan-init.exe")
        if not os.path.isfile(init):
            log.append("elan-init.exe not found in the download")
            return False
        home = pl["into"]
        rc, out = self.spawn([init, "-y", "--default-toolchain", "stable", "--no-modify-path"], dict(os.environ, ELAN_HOME=home, RUSTUP_HOME=home), 3000)
        log.append(out[-800:])
        bindir = os.path.join(home, "bin")
        lean = os.path.join(bindir, "lean.exe")
        if rc != 0 or not os.path.isfile(lean):
            log.append("REVIEW FAILED: the installer did not produce lean.exe; nothing was put into use")
            return False
        import tempfile
        scratch = tempfile.mkdtemp(prefix="review_", dir=self.box.scratch)
        try:
            open(os.path.join(scratch, "T.lean"), "w", encoding="utf-8").write("theorem t (a b : Nat) : a + b = b + a := Nat.add_comm a b\n")
            rc2, out2 = self.spawn([lean, "T.lean"], dict(os.environ, ELAN_HOME=home), 300, cwd=scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        log.append("review: Lean compiled a test theorem: " + ("passed" if rc2 == 0 else "FAILED " + out2[-200:]))
        if rc2 != 0:
            log.append("REVIEW FAILED: nothing was put into use")
            return False
        self.box.cfg["extra_dirs"] = sorted(set((self.box.cfg.get("extra_dirs") or []) + [bindir]))
        return True

    @staticmethod
    def _spawn(argv, env, timeout, cwd=None):
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd, creationflags=NOWIN)
            try:
                out, _ = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(p)
                return 1, "timed out"
            return p.returncode, out or ""
        except OSError as e:
            return 1, str(e)

    @staticmethod
    def _fetch(url, dest):
        req = urllib.request.Request(url, headers={"User-Agent": "agentdispatch"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
