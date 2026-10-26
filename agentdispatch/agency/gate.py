"""the review gate: a result is accepted only when the objective check passed, a DIFFERENT agent approved it, and no guard tripped. Pure decisions (no I/O) plus a tamper check
on the gate's own files. Changes to this file, its tests and the policy files always need the owner."""
import fnmatch, hashlib, json, os

PROTECTED = ("agentdispatch/agency/gate.py", "agentdispatch/agency/policy.py", "agentdispatch/agency/dispatcher.py", "tests/test_agency*.py", "dev/*.json", "dev/*.log", "*agents.json", "*agency.json")
MAX_DIFF_LINES = 800
LOCK_FILE = "agency_lock.json"


def _norm(p):
    return (p or "").replace("\\", "/").lstrip("./")


def protected_hits(paths, patterns=PROTECTED):
    out = []
    for p in paths or []:
        n = _norm(p)
        if any(fnmatch.fnmatch(n, pat) or fnmatch.fnmatch(os.path.basename(n), pat) for pat in patterns):
            out.append(p)
    return out


def decide(job, author, reviewer, check_ok, review, touched=(), diff_lines=0, patterns=PROTECTED, max_diff=MAX_DIFF_LINES):
    """-> dict(ok, reasons, needs_owner). `check_ok` is True / False / None (no check was configured)."""
    reasons, needs_owner = [], False
    acc = job.get("acceptance") or {}
    if acc.get("check") and check_ok is not True:
        reasons.append("the acceptance check did not pass" if check_ok is False else "the acceptance check was not run")
    if not reviewer:
        reasons.append("no independent reviewer was available")
    elif reviewer == author:
        reasons.append("the reviewer must be a different agent than the author (%s)" % author)
    elif not (review or {}).get("approve"):
        reasons.append("the reviewer did not approve: " + "; ".join((review or {}).get("reasons") or ["no reason given"]))      # whole: this text becomes the rework step's objective (it was cut to 300 characters)
    hits = protected_hits(touched, patterns)
    if hits:
        reasons.append("touches protected files (owner approval needed): " + ", ".join(hits[:5]))
        needs_owner = True
    if diff_lines > max_diff:
        reasons.append("the change is %d lines (limit %d)" % (diff_lines, max_diff))
        needs_owner = True
    return dict(ok=not reasons, reasons=reasons, needs_owner=needs_owner)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def lock(root, data_dir, files=("agentdispatch/agency/gate.py", "agentdispatch/agency/policy.py", "agentdispatch/agency/dispatcher.py")):
    """owner command: remember the hashes of the gate's own files"""
    d = {f: sha256_file(os.path.join(root, f)) for f in files if os.path.isfile(os.path.join(root, f))}
    json.dump(d, open(os.path.join(data_dir, LOCK_FILE), "w", encoding="utf-8"), indent=1)
    return d


def verify_lock(root, data_dir):
    """-> list of problems; empty when the gate's files are what the owner locked (a missing lock file is itself reported)"""
    p = os.path.join(data_dir, LOCK_FILE)
    if not os.path.isfile(p):
        return ["no lock file: run `agentdispatch agency lock` once after reviewing the gate"]
    want = json.load(open(p, encoding="utf-8"))
    bad = []
    for f, h in want.items():
        fp = os.path.join(root, f)
        if not os.path.isfile(fp):
            bad.append("%s is missing" % f)
        elif sha256_file(fp) != h:
            bad.append("%s changed since it was locked" % f)
    return bad
