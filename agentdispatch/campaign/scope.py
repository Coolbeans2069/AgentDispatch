"""read / write / forbidden path scopes for a job: glob patterns relative to the project, `**` crosses folders. Enforced where files move (the workspace builder and the patch writer), so a job
cannot see what it must not see and cannot change what it must not change, whatever its prompt says."""
import re

_cache = {}


def _rx(pat):
    if pat in _cache:
        return _cache[pat]
    p = pat.replace("\\", "/").lstrip("./")
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if p.startswith("**", i):
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    _cache[pat] = re.compile("^" + out.rstrip("/") + "(?:/.*)?$", re.I)                    # a pattern also covers everything below a folder it names
    return _cache[pat]


def norm(rel):
    return str(rel).replace("\\", "/").lstrip("./")


def match(rel, patterns):
    n = norm(rel)
    return any(_rx(p).match(n) for p in patterns or [])


def read_ok(meta, rel):
    sc = (meta or {}).get("scope") or {}
    if match(rel, sc.get("forbidden")):
        return False
    return not sc.get("read") or match(rel, sc["read"])


def write_ok(meta, rel):
    """-> (ok, why)"""
    sc = (meta or {}).get("scope") or {}
    if match(rel, sc.get("forbidden")):
        return False, "%s is forbidden for this job" % rel
    if sc.get("write") and not match(rel, sc["write"]):
        return False, "%s is outside what this job may write (%s)" % (rel, ", ".join(sc["write"][:4]))
    return True, ""
