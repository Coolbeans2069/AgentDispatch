"""the owner's switches for the agency: <data>/agency.json (defaults are the cautious ones), and the kill-switch file <data>/agency_off. Agents cannot change this file's effect: it is
protected and hash-locked (see gate.py)."""
import json, os, time
from .. import paths

DEFAULTS = dict(
    auto_merge=False,                        # OFF until the owner turns it on
    safe_repos=paths.SAFE_REPOS,
    safe_trees=[],                           # folders whose every sub-folder is a safe project (e.g. the autonomous projects folder): auto-merge applies inside them
    max_merges_per_day=5,
    max_parallel=1,
    step_timeout_s=1800,
    max_diff_lines=800,
    quiet_hours=None,                        # e.g. [8, 22] = no agent runs between 08:00 and 22:00; None = always allowed
    tick_s=20,
    allow_host_checks=False,                 # a job's host_check runs its protected script ON THIS COMPUTER after the sandbox check (real FreeCAD / ffmpeg); off until the owner turns it on
    work_dir=paths.WORK_DIR,           # throw-away copies of targets (never inside a repo)
)


def load(data_dir):
    cfg = dict(DEFAULTS)
    try:
        user = json.load(open(os.path.join(data_dir, "agency.json"), encoding="utf-8"))
        for k, v in user.items():
            if k in cfg:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def killed(data_dir):
    return os.path.exists(os.path.join(data_dir, "agency_off"))


def in_quiet_hours(cfg, now=None):
    q = cfg.get("quiet_hours")
    if not q:
        return False
    h = time.localtime(now or time.time()).tm_hour
    a, b = q
    return (a <= h < b) if a <= b else (h >= a or h < b)


def is_safe_repo(cfg, target):
    t = (target or "").replace("\\", "/").rstrip("/").lower()
    if ".." in t.split("/"):
        return False
    if any(t == r.replace("\\", "/").rstrip("/").lower() for r in cfg.get("safe_repos", [])):
        return True
    return any(t.startswith(r.replace("\\", "/").rstrip("/").lower() + "/") for r in cfg.get("safe_trees", []))
