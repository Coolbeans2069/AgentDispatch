"""the toolbox: run a tool on this computer on behalf of a model, under the policy in policy.py, and keep a record of every run (what, where, how long, a hash of the output) so the work can be
audited and replayed. Models send a request, the computer does the work, only the short result goes back: that is what keeps the subscription models' token use low."""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time

from ..nightshift import kill_tree
from . import policy as P
from .registry import Registry, NOWIN

MAX_OUT = 20000
DEFAULT_CFG = dict(trusted_roots=[], python=None, extra_dirs=[], auto_install=[], max_mb=4096, gpu="", tools_home="", extra_pythons=[], timeout_s=120, max_timeout_s=1800)


def load_cfg(data_dir):
    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.load(open(os.path.join(data_dir, "localtools.json"), encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(data_dir, cfg):
    tmp = os.path.join(data_dir, "localtools.json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: cfg.get(k) for k in DEFAULT_CFG}, f, indent=1)
    os.replace(tmp, os.path.join(data_dir, "localtools.json"))


def _cap(text):
    text = (text or "").replace("\r\n", "\n")
    return text if len(text) <= MAX_OUT else text[:MAX_OUT // 2] + "\n... (%d characters cut) ...\n" % (len(text) - MAX_OUT) + text[-MAX_OUT // 2:]


class ToolBox:
    def __init__(self, data_dir, allowed_roots=(), work_root=None, now=time.time):
        self.data, self.now = data_dir, now
        os.makedirs(data_dir, exist_ok=True)
        self.cfg = load_cfg(data_dir)
        self.allowed_roots = list(allowed_roots)
        self.work_root = work_root or os.path.join(data_dir, "work")
        self.scratch = os.path.join(data_dir, "tools", "scratch")
        os.makedirs(self.scratch, exist_ok=True)
        self.intake_dir = os.path.join(data_dir, "intake")
        self.local_chat = None                                                  # set by the app: (prompt) -> text from a model on this computer
        self.resolve_model = lambda function: None                              # set by the app: the catalog's choice of a model for a function (e.g. speech-stt)
        self.reg = Registry(data_dir, self.cfg)
        self.runs_path = os.path.join(data_dir, "toolruns.jsonl")
        self.active = {}
        self.lock = threading.RLock()
        self._seq = 0

    # ------------------------------------------------------------ places
    def trusted(self):
        return [r for r in list(self.allowed_roots) + list(self.cfg.get("trusted_roots") or []) if r]

    def safe_dirs(self):
        return self.trusted() + [self.work_root, self.scratch, self.intake_dir, os.path.join(self.data, "jobs")]

    def reload(self):
        self.cfg = load_cfg(self.data)
        self.reg.cfg = self.cfg

    # ------------------------------------------------------------ the record
    def _log(self, rec):
        with self.lock:
            with open(self.runs_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def history(self, limit=50, tool=None, job=None):
        out = []
        try:
            with open(self.runs_path, "rb") as f:
                f.seek(max(0, os.path.getsize(self.runs_path) - 400000))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return out
        for l in reversed(lines):
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if (tool and r.get("tool") != tool) or (job and r.get("job") != job):
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def usage(self):
        """runs per tool (from the record): what the brain shows as each tool's activity"""
        c = {}
        for r in self.history(2000):
            c[r.get("tool")] = c.get(r.get("tool"), 0) + 1
        return c

    # ------------------------------------------------------------ the one entry point
    def run(self, tool, args=None, code=None, file=None, cwd=None, timeout=None, caller="", job=None, stdin=None, gpu=None, project=None, strict=True, env=None):
        """-> dict(ok, rc, stdout, stderr, secs, tool, sha256, problems). `ok` means the program ran AND (for Lean) compiled with no error and no sorry. Refusals (policy) come back as ok=False with `problems`."""
        t0 = self.now()
        self.reg.scan()
        spec = self.reg.spec(tool)
        rec = dict(t=t0, tool=tool, caller=caller, job=job, args=list(args or [])[:12], cwd=cwd)

        def done(res):
            res.setdefault("tool", tool)
            res["secs"] = round(self.now() - t0, 2)
            rec.update(ok=res.get("ok"), rc=res.get("rc"), secs=res["secs"], sha256=res.get("sha256"), problems=res.get("problems"), code_sha=hashlib.sha256((code or "").encode()).hexdigest()[:16] if code else None)
            self._log(rec)
            return res

        if not spec:
            return done(dict(ok=False, problems=["no such tool: %s (see tool_list)" % tool]))
        info = self.reg.state["tools"].get(tool, {})
        if not info.get("available"):
            return done(dict(ok=False, problems=["%s is not installed on this computer. Ask for it with tool_install (the owner approves) or tool_need (an agent builds it)." % spec["name"]], missing=True))
        timeout = max(1, min(int(timeout or self.cfg["timeout_s"]), self.cfg["max_timeout_s"]))
        try:
            if spec["run"] == "calc":
                return done(self._calc(code, timeout, gpu))
            if tool == "lean":
                return done(self._lean(file, code, args, cwd, project, timeout, gpu, strict))
            if tool == "lake":
                return done(self._lake(args, cwd or project, timeout, gpu))
            if tool == "git":
                return done(self._git(args, cwd, timeout))
            if spec["run"] == "pymodule":
                return done(self._pymodule(spec, args, cwd, timeout, gpu))
            if spec["run"] == "read_file":
                path = file or (args[0] if args else "")
                if not path or not os.path.isfile(path) or not P.under(path, self.safe_dirs()):
                    return done(dict(ok=False, problems=["%r is not a file inside an allowed project or work folder" % path]))
                if os.path.getsize(path) > 8 * 1048576:
                    return done(dict(ok=False, problems=["the file is larger than 8 MB"]))
                raw = open(path, "rb").read()
                if b"\x00" in raw[:4096]:
                    return done(dict(ok=False, problems=["that is a binary file"]))
                off = int(code) if str(code or "").isdigit() else 0
                text = raw.decode("utf-8", "replace")
                return done(dict(ok=True, rc=0, stdout=text[off:off + 12000], stderr="", sha256=hashlib.sha256(raw).hexdigest(), note="%d characters in the file" % len(text)))
            if spec["run"] == "local_llm":
                if not self.local_chat or not (code or (args and args[0])):
                    return done(dict(ok=False, problems=["give the prompt in `code`"]))
                try:
                    text = self.local_chat(str(code or args[0])[:12000])
                except Exception as e:
                    return done(dict(ok=False, problems=["the local model did not answer: %s" % str(e)[:150]]))
                return done(dict(ok=True, rc=0, stdout=_cap(text), stderr="", sha256=hashlib.sha256(text.encode()).hexdigest(), note="written by a small local model: check it"))
            if spec["run"] == "script":
                return done(self._script(spec, args or ([file] if file else []), timeout, gpu, env))
            if spec["run"] == "pyargs":
                return done(self._pyargs(spec, args, code, timeout, gpu, env))
            if spec["run"] == "custom":
                return done(self._custom(spec, args, cwd, timeout, gpu, stdin))
            if spec["run"] == "exe":
                return done(self._exe(spec, info, args, cwd, timeout, gpu, stdin))
        except Exception as e:                                                       # a bug here must come back as a refusal, never as a crash of the broker
            return done(dict(ok=False, problems=["%s: %s" % (e.__class__.__name__, str(e)[:200])]))
        return done(dict(ok=False, problems=["%s is a library: import it in a calculation (tool: calc)" % spec["name"]]))

    # ------------------------------------------------------------ plumbing
    def _spawn(self, argv, cwd, timeout, env, stdin=None, label=""):
        key = "r%d" % self._seq
        self._seq += 1
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 errors="replace", creationflags=NOWIN, start_new_session=(os.name != "nt"))
        except OSError as e:
            return dict(ok=False, rc=None, stdout="", stderr="", problems=["could not start: %s" % e])
        self.active[key] = dict(tool=label, started=self.now(), pid=p.pid)
        stop = threading.Event()
        threading.Thread(target=self._watch, args=(p, stop), daemon=True).start()
        try:
            out, err = p.communicate(input=stdin, timeout=timeout)
            rc, timed = p.returncode, False
        except subprocess.TimeoutExpired:
            kill_tree(p)
            try:
                out, err = p.communicate(timeout=10)
            except Exception:
                out, err = "", ""
            rc, timed = None, True
        finally:
            stop.set()
            self.active.pop(key, None)
        full = (out or "") + "\n--stderr--\n" + (err or "")
        res = dict(rc=rc, stdout=_cap(out), stderr=_cap(err), sha256=hashlib.sha256(full.encode("utf-8", "replace")).hexdigest(), ok=(rc == 0))
        if timed:
            res.update(ok=False, problems=["timed out after %d s (and was stopped)" % timeout])
        return res

    def _watch(self, p, stop):
        """stop a run that eats more memory than allowed (needs psutil; without it only the timeout protects)"""
        try:
            import psutil
            proc = psutil.Process(p.pid)
        except Exception:
            return
        limit = int(self.cfg["max_mb"]) * 1048576
        while not stop.wait(1.0):
            try:
                rss = proc.memory_info().rss + sum(c.memory_info().rss for c in proc.children(recursive=True))
            except Exception:
                return
            if rss > limit:
                kill_tree(p)
                return

    def _cwd(self, cwd, host_exec=False):
        """-> (dir, problem). Safe tools may work in any scratch / work / trusted folder; tools that execute project code only in folders the owner trusts."""
        if not cwd:
            return (self.scratch if not host_exec else None), ("this tool runs project code: give a folder (cwd) inside a trusted project folder" if host_exec else None)
        if not os.path.isdir(cwd):
            return None, "no such folder: %s" % cwd
        if not P.under(cwd, self.trusted() if host_exec else self.safe_dirs()):
            return None, "%s is not inside %s" % (cwd, "a folder the owner trusts (nightshift allowed_roots / localtools.json trusted_roots)" if host_exec else "an allowed work folder")
        return cwd, None

    def _env(self, gpu=None, extra=None):
        env = P.clean_env(extra, gpu if gpu not in (None, "") else (self.cfg.get("gpu") or None))
        dirs = []                                                                          # the folders of the tools this computer has (ffmpeg, ffprobe, ...): a test or tool that runs `ffmpeg` finds it
        for info in (self.reg.state.get("tools") or {}).values():
            d = os.path.dirname(info.get("path") or "") if info.get("available") else ""
            if d and os.path.isdir(d) and d not in dirs and "python" not in os.path.basename(info.get("path") or "").lower():
                dirs.append(d)
        if dirs:
            key = next((k for k in env if k.upper() == "PATH"), "PATH")
            env[key] = os.pathsep.join(dirs + [env.get(key, "")])
        return env

    # ------------------------------------------------------------ python calculations
    def _calc(self, code, timeout, gpu):
        bad = P.check_calc(code)
        if bad:
            return dict(ok=False, problems=bad)
        libs = [m for m in ("sympy", "mpmath", "numpy", "scipy", "z3", "flint", "gmpy2", "networkx", "pandas", "matplotlib", "sklearn",
                            "statsmodels", "seaborn", "numba", "jax", "xarray", "h5py", "pint", "uncertainties", "astropy", "skyfield",
                            "symengine", "shapely", "pyproj", "geopandas", "jsonschema", "pydantic", "yaml", "toml", "orjson", "torch",
                            "tiktoken", "PIL", "cv2", "skimage", "trimesh", "soundfile", "librosa", "polars", "duckdb", "cvxpy", "pulp")
                if re.search(r"\b%s\b" % m, code)]
        py = self.reg.python_for(libs)
        if not py:
            return dict(ok=False, problems=["no Python interpreter found"])
        d = tempfile.mkdtemp(prefix="calc_", dir=self.scratch)
        try:
            script = os.path.join(d, "calc.py")
            with open(script, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            res = self._spawn([py, "-I", "-B", script], d, timeout, self._env(gpu, dict(TEMP=d, TMP=d)), label="calc")
            res["python"] = py
            return res
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------ Lean
    def _lean(self, file, code, args, cwd, project, timeout, gpu, strict):
        exe = self.reg.state["tools"]["lean"]["path"]
        tmpd = None
        if code:
            tmpd = tempfile.mkdtemp(prefix="lean_", dir=self.scratch)
            file = os.path.join(tmpd, "Check.lean")
            with open(file, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            cwd = cwd or tmpd
        if not file:
            if args == ["--version"]:
                return self._spawn([exe, "--version"], self.scratch, 30, self._env(), label="lean")
            return dict(ok=False, problems=["give a Lean file (file) or Lean source (code)"])
        try:
            if not os.path.isfile(file):
                return dict(ok=False, problems=["no such file: %s" % file])
            if not P.under(file, self.safe_dirs()):
                return dict(ok=False, problems=["the file must be inside an allowed work folder or a trusted project folder"])
            text = open(file, encoding="utf-8", errors="replace").read()
            trusted = P.under(file, self.trusted())
            bad = [] if trusted and not code else P.check_lean_text(text)
            if bad:
                return dict(ok=False, problems=bad)
            run_cwd = project if project and P.under(project, self.trusted()) else os.path.dirname(file)
            lake = self.reg.state["tools"].get("lake", {}).get("path")
            has_lake = bool(project and lake and (os.path.isfile(os.path.join(run_cwd, "lakefile.lean")) or os.path.isfile(os.path.join(run_cwd, "lakefile.toml"))))
            argv = [lake, "env", exe, file] if has_lake else [exe, file]
            res = self._spawn(argv, run_cwd, timeout, self._env(gpu), label="lean")
            out = (res.get("stdout") or "") + (res.get("stderr") or "")
            res["sorry"] = bool(re.search(r"declaration uses 'sorry'|\bsorry\b", out))
            res["errors"] = len(re.findall(r"\berror\b", out))
            if res["ok"] and strict and res["sorry"]:
                res.update(ok=False, problems=["the file compiles only with `sorry`: that is not a proof"])
            return res
        finally:
            if tmpd:
                shutil.rmtree(tmpd, ignore_errors=True)

    def _lake(self, args, cwd, timeout, gpu):
        args = list(args or [])
        if not args or args[0] not in ("build", "env", "--version", "exe"):
            return dict(ok=False, problems=["lake may only run: build, env, exe, --version"])
        run_cwd, why = self._cwd(cwd, host_exec=True)
        if why:
            return dict(ok=False, problems=[why])
        return self._spawn([self.reg.state["tools"]["lake"]["path"]] + args, run_cwd, timeout, self._env(gpu), label="lake")

    # ------------------------------------------------------------ git (read-only), python modules, other programs, engineered tools
    def _git(self, args, cwd, timeout):
        args = [str(a) for a in (args or [])]
        ok, why = P.git_args_ok(args)
        if not ok:
            return dict(ok=False, problems=[why])
        run_cwd, why = self._cwd(cwd or self.trusted()[0] if (cwd or self.trusted()) else None)
        if why or not run_cwd:
            return dict(ok=False, problems=[why or "give a repository folder (cwd)"])
        exe = self.reg.state["tools"]["git"]["path"]
        res = self._spawn([exe, "-c", "safe.directory=*", "-c", "core.fsmonitor=false", "--no-pager"] + args, run_cwd, timeout, self._env(), label="git")
        if res.get("rc") == 128 and "not a git repository" in (res.get("stderr") or ""):
            res["problems"] = ["%s is not a git repository (no history to read there)" % run_cwd]
        return res

    def _pymodule(self, spec, args, cwd, timeout, gpu):
        run_cwd, why = self._cwd(cwd, host_exec=True)
        if why:
            return dict(ok=False, problems=[why])
        args = [str(a) for a in (args or [])][:30]
        if any(a in ("-c", "--import-mode=importlib") or a.startswith("-p") and a not in ("-p", "no:cacheprovider") for a in args):
            return dict(ok=False, problems=["that flag is not allowed"])
        return self._spawn([self.reg.python_for([spec["lib"]]), "-m", spec["lib"]] + args, run_cwd, timeout, self._env(gpu), label=spec["id"])

    def _exe(self, spec, info, args, cwd, timeout, gpu, stdin):
        args = [str(a) for a in (args or [])][:30]
        tid = spec["id"]
        if tid == "docker":
            if not args or args[0] not in ("info", "version", "images", "ps"):
                return dict(ok=False, problems=["docker may only be asked: info, version, images, ps"])
        if tid == "nvidia-smi" and any(not a.startswith("-") or a in ("-r", "--gpu-reset", "-pm", "-pl") for a in args):
            return dict(ok=False, problems=["nvidia-smi may only be used to read (flags like --query-gpu)"])
        if tid == "kubectl":
            if not args or args[0] not in ("get", "describe", "logs", "version", "api-resources", "api-versions", "top", "explain", "cluster-info"):
                return dict(ok=False, problems=["kubectl is read-only here: get/describe/logs/version/top/explain/…"])
        if tid == "ollama":
            if not args or args[0] not in ("list", "show", "ps", "--version", "version"):
                return dict(ok=False, problems=["ollama CLI here may only list/show/ps (use tool local_llm to prompt)"])
        if tid == "rustc":
            if args and args[0] not in ("--version", "-V", "--print", "--help", "-h") and not any(a.startswith("--print") for a in args):
                return dict(ok=False, problems=["rustc here may only --version / --print (use cargo in a trusted folder to build)"])
        if tid == "trivy":
            if not args or args[0] not in ("image", "fs", "repo", "config", "rootfs", "sbom", "version", "--version"):
                return dict(ok=False, problems=["trivy may run: image, fs, repo, config, rootfs, sbom, version"])
        if tid == "hf":
            if not args or args[0] not in ("download", "scan-cache", "env", "version", "jobs", "repo", "view", "filter-repo"):
                return dict(ok=False, problems=["hf CLI here: download/scan-cache/env/version/jobs/repo/view"])
        if tid == "uv":
            if not args or args[0] not in ("pip", "python", "version", "--version", "tool", "cache"):
                return dict(ok=False, problems=["uv here may run: pip / python / tool / cache / version"])
        if tid in ("ffmpeg", "ffprobe", "aria2c", "yt-dlp", "whisper", "pandoc", "tesseract"):
            for a in args:
                if re.match(r"^[A-Za-z]:[\\/]|^/", a) and os.path.exists(os.path.dirname(a) or ".") and not P.under(a, self.safe_dirs()):
                    return dict(ok=False, problems=["file %s is outside the allowed folders" % a])
        # version-only queries may run without a trusted project cwd
        versionish = (not args) or (len(args) == 1 and args[0] in ("--version", "-V", "-h", "--help", "version")) or (tid == "nvidia-smi" and not args)
        host = bool(spec.get("host_exec")) and not versionish
        run_cwd, why = self._cwd(cwd, host_exec=host)
        if why:
            return dict(ok=False, problems=[why])
        if tid in ("ffmpeg", "ffprobe", "aria2c", "yt-dlp", "whisper", "pandoc", "tesseract"):          # a relative path is resolved against the folder the tool runs in: ../../ must not climb out of the allowed folders
            for a in args:
                if not a.startswith("-") and ".." in a.replace("\\", "/").split("/"):
                    full = os.path.normpath(os.path.join(run_cwd or ".", a))
                    if not P.under(full, self.safe_dirs()):
                        return dict(ok=False, problems=["file %s is outside the allowed folders" % a])
        if tid == "ffmpeg" and args:                                                       # an output file in a trusted project folder whose folder does not exist yet: make the folder (ffmpeg will not)
            out = args[-1]
            if not out.startswith("-") and re.match(r"^[A-Za-z]:[\/]|^/", out) and P.under(out, self.trusted()):
                parent = os.path.dirname(out)
                if parent and not os.path.isdir(parent):
                    try:
                        os.makedirs(parent, exist_ok=True)
                    except OSError:
                        pass
        if host:
            for a in args:
                if a.startswith("-") and a not in ("--version", "-V", "-h", "--help", "-N"):
                    # cargo/node/cmake: allow common non-destructive flags; still require trusted cwd
                    if tid in ("cargo", "cmake", "node") and a in ("check", "metadata", "tree", "fetch", "--quiet", "-q", "--offline", "run", "test", "build", "-S", "-B"):
                        continue
                    if tid == "cargo" and args and args[0] in ("check", "metadata", "tree", "fetch", "test", "build", "clippy", "fmt"):
                        continue
                    if tid == "node" and a in ("-e", "--eval"):
                        return dict(ok=False, problems=["node -e is not allowed; put a script file in a trusted folder"])
                if not a.startswith("-") and os.path.isfile(a) and not P.under(a, self.trusted()):
                    return dict(ok=False, problems=["%s must be a file inside a trusted project folder" % a])
        if tid == "freecad" and host:
            for a in args:
                if a.startswith("-") and a not in ("--version", "-h", "--help"):
                    return dict(ok=False, problems=["flag %s is not allowed for %s" % (a, spec["name"])])
                if not a.startswith("-") and not (os.path.isfile(a) and P.under(a, self.trusted())):
                    return dict(ok=False, problems=["%s must be a file inside a trusted project folder" % a])
        return self._spawn([info["path"]] + args, run_cwd, timeout, self._env(gpu), stdin=stdin, label=spec["id"])

    def _script(self, spec, files, timeout, gpu, env):
        """one of the program's own scripts (speech to text, faces, pdf, csv, …) on files inside the allowed folders; `env` is for the app's own calls (language, device, gallery), never from a model"""
        files = [str(f) for f in (files or [])][:12]
        if not files:
            return dict(ok=False, problems=["give the file(s) to work on"])
        for f in files:
            if not os.path.isfile(f) or not P.under(f, self.safe_dirs()):
                return dict(ok=False, problems=["%s is not a file inside an allowed folder" % f])
        lib = spec.get("lib")
        py = self.reg.python_for([] if lib in (None, "hashlib") else [lib])
        if not py or not self.reg.state["tools"].get(spec["id"], {}).get("available"):
            return dict(ok=False, problems=["%s is not installed" % spec["name"]], missing=True)
        extra = dict(env or {})
        if spec.get("model_function"):
            md = self.resolve_model(spec["model_function"])
            if md:
                extra["AD_MODEL_DIR"] = md
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", spec["script"])
        res = self._spawn([py, "-I", "-B", script] + files, self.scratch, timeout, self._env(gpu, extra), label=spec["id"])
        return self._json_out(res)

    def _pyargs(self, spec, args, code, timeout, gpu, env):
        """script that takes free-form args / a short code string (URL fetch, unit convert). URLs only when allow_url is set."""
        argv = [str(a) for a in (args or [])][:12]
        if code and str(code).strip():
            # unit_convert and similar accept the whole phrase in code
            if not argv:
                argv = [str(code).strip()]
            elif spec.get("allow_url") and not argv:
                argv = [str(code).strip()]
        if not argv:
            return dict(ok=False, problems=["give args (or code) for %s" % spec["name"]])
        if spec.get("allow_url"):
            for a in argv:
                if re.match(r"^https?://", a, re.I):
                    continue
                if "://" in a or a.startswith("file:"):
                    return dict(ok=False, problems=["only http(s) URLs are allowed"])
        else:
            for a in argv:
                if re.match(r"^https?://", a, re.I) or a.startswith("file:"):
                    return dict(ok=False, problems=["URLs are not allowed for %s" % spec["name"]])
        lib = spec.get("lib")
        py = self.reg.python_for([] if lib in (None, "hashlib") else [lib])
        if not py or not self.reg.state["tools"].get(spec["id"], {}).get("available"):
            return dict(ok=False, problems=["%s is not installed" % spec["name"]], missing=True)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", spec["script"])
        res = self._spawn([py, "-I", "-B", script] + argv, self.scratch, timeout, self._env(gpu, dict(env or {})), label=spec["id"])
        return self._json_out(res)

    def _json_out(self, res):
        try:
            data = json.loads((res.get("stdout") or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        res["data"] = data
        if data is not None and data.get("ok") is False:
            res.update(ok=False, problems=[str(data.get("error", "failed"))[:300]])
        elif res.get("ok") and data is None:
            res.update(ok=False, problems=["the tool printed no result"])
        return res

    def _custom(self, spec, args, cwd, timeout, gpu, stdin):
        argv = [a.replace("{python}", self.reg.python_for() or sys.executable) for a in spec["argv"]]
        run_cwd = spec["folder"]
        if cwd and P.under(cwd, self.safe_dirs()):
            run_cwd = cwd
        script = os.path.join(spec["folder"], argv[1]) if len(argv) > 1 and not os.path.isabs(argv[1]) else None
        if script and os.path.isfile(script):
            argv[1] = script
        return self._spawn(argv + [str(a) for a in (args or [])][:30], run_cwd, timeout, self._env(gpu), stdin=stdin, label=spec["id"])

    # ------------------------------------------------------------ verification: controls
    def controls(self, controls, cwd=None, caller="", job=None):
        """run positive and adverse controls. Each: {label, tool, args | code | file, expect: 'pass' | 'fail'}. The campaign is satisfied only when every positive control passes AND every
        adverse control fails as intended. -> (all_ok, [per-control dict])"""
        out, all_ok = [], True
        for c in controls or []:
            expect = "fail" if str(c.get("expect", "pass")).lower() == "fail" else "pass"
            r = self.run(c.get("tool", ""), args=c.get("args"), code=c.get("code"), file=c.get("file"), cwd=c.get("cwd") or cwd, timeout=c.get("timeout"), caller=caller, job=job, project=c.get("project"))
            got = "pass" if r.get("ok") else "fail"
            refused = bool(r.get("problems")) and r.get("rc") is None and not r.get("sorry")
            met = (got == expect) and not (refused and expect == "fail")             # a refusal is not a failure of the control's subject: it cannot satisfy an adverse control
            all_ok &= met
            out.append(dict(label=c.get("label") or c.get("tool"), tool=c.get("tool"), expect=expect, got=got, met=met, sha256=r.get("sha256"), note=("; ".join(r.get("problems") or []))[:200], secs=r.get("secs")))
        return all_ok, out
