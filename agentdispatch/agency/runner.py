"""run a job's acceptance check on the workspace copy, in the night shift's Docker container (no network, no host files except the copy, memory / CPU / process limits). Returns
True / False, or None when no isolation is available (the dispatcher then blocks the job for the owner instead of running model-written code unconfined)."""
import os, shlex, subprocess, sys, tempfile, time
from ..textfit import tail as _tail

from ..nightshift import DEFAULTS, DOCKER_WHY, command_ok, docker_ready, kill_tree, load_config


class CheckRunner:
    def __init__(self, data_dir, cfg=None):
        self.cfg = dict(DEFAULTS, **(cfg if cfg is not None else load_config(data_dir)))

    def isolation(self):
        mode = self.cfg.get("isolation", "auto")
        if mode == "auto":
            return "docker" if docker_ready(self.cfg["docker_image"]) else None
        if mode == "docker":
            return "docker" if docker_ready(self.cfg["docker_image"]) else None
        return "none"                                                              # the owner opted in to running unconfined

    def run_job(self, project_dir, acceptance, allow_host=False, host_timeout_s=900):
        """the sandbox check, then (only if it passed and the owner allowed it) the job's host check -> (ok | None, output). A host check that is not allowed blocks the job for the owner, never passes it."""
        acc = acceptance or {}
        ok, out = self.run(project_dir, acc.get("check"))
        hc = acc.get("host_check")
        if not hc or ok is not True:
            return ok, out
        if not allow_host:
            return None, "this job has a host check (%s) and host checks are not enabled: set allow_host_checks in agency.json to let the real tool run on this computer" % hc[:120]
        saved = self.cfg.get("check_timeout_s")
        self.cfg["check_timeout_s"] = host_timeout_s
        try:
            hok, hout = self.run(project_dir, hc, host=True)
        finally:
            self.cfg["check_timeout_s"] = saved
        return (bool(hok) if hok is not None else None), _tail(out or "", 6000, "check output") + "\n--- HOST CHECK (%s): %s ---\n" % (hc[:200], "passed" if hok else "FAILED") + _tail(hout or "", 8000, "host check output")

    def argv(self, check):
        argv = shlex.split(check) if isinstance(check, str) else list(check)
        if not command_ok(argv):
            raise ValueError("that check command is not allowed")
        return argv

    def run(self, project_dir, check, host=False):
        """-> (ok | None, output tail). host=True runs it on this computer (not in Docker): for a job's owner-enabled host_check, the proof that the real tool works"""
        argv = self.argv(check)
        mode = "none" if host else self.isolation()
        if mode is None:
            return None, "no isolation available (Docker image %s: %s): the check was not run" % (self.cfg["docker_image"], DOCKER_WHY.get(self.cfg["docker_image"]) or "not ready")
        tmp = tempfile.mkdtemp(prefix="agency_check_")
        name = None
        if mode == "docker":
            name = "agency_%d_%d" % (os.getpid(), int(time.time() * 1000) % 10 ** 9)
            full = ["docker", "run", "--rm", "--name", name, "--network", "none", "--memory", "1g", "--cpus", "2", "--pids-limit", "256", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "-v", "%s:/work" % os.path.abspath(project_dir), "-w", "/work", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/work", "-e", "PYTHONIOENCODING=utf-8",
                    self.cfg["docker_image"], "python"] + argv[1:]
            env = dict(os.environ, MSYS_NO_PATHCONV="1")
            cwd = None
        else:
            env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramData", "MEW_FREECADCMD") if k in os.environ}
            env.update(TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONPATH=project_dir)
            full = [sys.executable if os.path.basename(argv[0]).lower().startswith("python") else argv[0]] + argv[1:]
            cwd = project_dir
        flags = (0x00004000 | 0x08000000 | 0x00000200) if os.name == "nt" else 0
        p = subprocess.Popen(full, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags,
                             start_new_session=(os.name != "nt"))
        try:
            out, _ = p.communicate(timeout=self.cfg["check_timeout_s"])
            return p.returncode == 0, _tail(out or "", 12000, "check output")      # the end of the output (where the failure is), marked when the start was left out; the reviewer and the rework agent read it
        except subprocess.TimeoutExpired:
            if name:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0)
            kill_tree(p)
            return False, "the check timed out after %d s" % self.cfg["check_timeout_s"]
