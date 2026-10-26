"""Stage 2: putting an accepted job's changes into the REAL project folder. The job's work lives in a throw-away copy; this copies exactly what changed (added, modified, removed) into the target, keeps a
backup of everything it overwrites or deletes, runs the acceptance check against the real folder in the same sandbox as before, and puts everything back if that check fails. Used by the agency for
folders the owner marked safe (auto-merge) and by the owner's Accept button. Nothing outside the target folder is ever touched."""
import json, os, shutil, time

from .workspace import Workspace


def _inside(root, path):
    r = os.path.normcase(os.path.realpath(root))
    p = os.path.normcase(os.path.realpath(path))
    return p == r or p.startswith(r + os.sep)


def make_apply(data_dir, work_root, run_check=None, now=time.time):
    """-> apply(job) -> dict(ok, summary | why, files). `run_check(project_dir, check) -> (ok | None, output)` verifies the real folder afterwards (None = could not run: treated as not verified)."""
    def apply(job):
        target = job.get("target") or ""
        if not target or not os.path.isdir(target):
            return dict(ok=False, why="the target folder %r does not exist" % target)
        ws = Workspace(work_root, job["id"], job.get("created"))
        if not os.path.isdir(ws.project):
            return dict(ok=True, summary="nothing to apply: the job made no copy", files=[])
        changes = ws.changes()
        if not changes:
            return dict(ok=True, summary="no changes to apply", files=[])
        bdir = os.path.join(data_dir, "backups", "%s_%d" % (job["id"], int(now())))
        os.makedirs(bdir, exist_ok=True)
        undo = []
        try:
            for rel, kind in sorted(changes.items()):
                dst = os.path.join(target, rel)
                if not _inside(target, dst):
                    raise ValueError("%s would leave the project folder" % rel)
                existed = os.path.isfile(dst)
                if existed:
                    keep = os.path.join(bdir, rel)
                    os.makedirs(os.path.dirname(keep), exist_ok=True)
                    shutil.copy2(dst, keep)
                undo.append((rel, existed))
                if kind == "removed":
                    if existed:
                        os.remove(dst)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(os.path.join(ws.project, rel), dst)
            chk = (job.get("acceptance") or {}).get("check")
            if chk and run_check:
                ok, out = run_check(target, chk)
                if ok is not True:
                    raise RuntimeError("the acceptance check on the real folder %s: %s" % ("failed" if ok is False else "could not run", (out or "")[-300:]))
        except Exception as e:
            for rel, existed in reversed(undo):                                         # put everything back exactly as it was
                dst = os.path.join(target, rel)
                try:
                    if existed:
                        shutil.copy2(os.path.join(bdir, rel), dst)
                    elif os.path.isfile(dst):
                        os.remove(dst)
                except OSError:
                    pass
            return dict(ok=False, why="%s: %s (everything was put back)" % (e.__class__.__name__, str(e)[:300]))
        with open(os.path.join(bdir, "applied.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(dict(job=job["id"], target=target, files=changes, t=now()), f, indent=1)
        return dict(ok=True, summary="applied %d file(s) to %s (backup in %s)" % (len(changes), target, bdir), files=sorted(changes))
    return apply
