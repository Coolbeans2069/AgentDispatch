#!/usr/bin/env python3
"""devtrack.py - change tracking and integration checks for a component's own dev/ folder (standard library only).

The SAME file is kept in every component (agentdispatch, engineroom and any other component). Each component has a dev/ folder:

    dev/VERSION          the component version this snapshot describes
    dev/CHANGELOG.md     human changelog (kept by hand)
    dev/BUILD_LOG.md     what was built / why / how tested (by hand, hashed)
    dev/SNAPSHOTS.log    one machine line per snapshot (not hashed: it describes the hashes)
    dev/manifest.json    every tracked file with its SHA-256, a roll-up hash, and the PREVIOUS roll-up (a hash chain)
    dev/contract.json    the public surface: what this component provides and requires (versions as ranges)
    dev/compat.json      which versions (and roll-up hashes) of the peers it was last tested against

Commands (run from anywhere; --root defaults to the folder above tools/ , i.e. the component root):

    devtrack.py init NAME VERSION          create dev/ scaffolding
    devtrack.py snapshot [--note TEXT]     rewrite dev/manifest.json (chain to the previous one) and append to SNAPSHOTS.log
    devtrack.py verify                     files on disk vs dev/manifest.json; exit 1 on any drift; also checks the chain file
    devtrack.py check-compat PEER_ROOT...  verify each peer's own manifest, its version against our `requires`, and our compat.json record
    devtrack.py record-compat PEER_ROOT... remember the peers' versions + roll-ups as the tested set (after a green integration test)
    devtrack.py release OUT_DIR PEER_ROOT...   write OUT_DIR/release.json: every component, version, roll-up and an overall hash
    devtrack.py self-hash                  print this file's SHA-256 (compare it between components: they must be identical)
"""
import argparse, fnmatch, hashlib, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache"}                       # pruned at any depth
IGNORE_TOP = {"data", "dist", ".venv", "venv", "backups"}                                       # runtime / build folders: only at the component root, so e.g. tests/data IS tracked
IGNORE_FILES = ["*.pyc", "*.pyo", "*.log", "*.tmp", "*.sqlite", "*.sqlite-wal", "*.sqlite-shm", "*.db", "dev/manifest.json", "dev/manifest.json.tmp", "dev/SNAPSHOTS.log"]
CHAIN_ZERO = "0" * 64


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tracked_files(root):
    out = {}
    for d, dirs, files in os.walk(root):
        at_root = os.path.abspath(d) == os.path.abspath(root)
        dirs[:] = sorted(x for x in dirs if x not in IGNORE_DIRS and not (at_root and x in IGNORE_TOP))
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f, pat) for pat in IGNORE_FILES):
                continue
            out[rel] = sha256_file(os.path.join(d, f))
    return out


def rollup(files):
    return hashlib.sha256("".join("%s %s\n" % (k, files[k]) for k in sorted(files)).encode()).hexdigest()


def dev(root, name):
    return os.path.join(root, "dev", name)


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)


def component_version(root):
    for p in (dev(root, "VERSION"), os.path.join(root, "VERSION")):
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read().strip()
    return "0.0.0"


# ---------------------------------------------------------------- semver ranges (^1.2, ~1.2.3, >=1.0 <2, 1.x, exact)
def ver(s):
    m = re.match(r"^v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?", str(s).strip())
    if not m:
        raise ValueError("bad version %r" % s)
    return tuple(int(g) if g and g not in ("x", "*") else 0 for g in m.groups())


def satisfies(version, rng):
    v = ver(version)
    for part in str(rng).split():
        if part in ("*", ""):
            continue
        m = re.match(r"^(\^|~|>=|<=|>|<|=)?(.*)$", part)
        op, base = m.group(1) or "=", m.group(2)
        b = ver(base)
        wild = base.lower().endswith(("x", "*"))
        if op == "^":
            ok = v >= b and (v[0] == b[0] if b[0] else (v[1] == b[1] if b[1] else v[2] == b[2]))
        elif op == "~":
            ok = v >= b and v[:2] == b[:2]
        elif op == ">=":
            ok = v >= b
        elif op == "<=":
            ok = v <= b
        elif op == ">":
            ok = v > b
        elif op == "<":
            ok = v < b
        else:
            k = len([x for x in re.split(r"[.]", base.lower().lstrip("v=")) if x not in ("x", "*")]) if wild else 3      # only the parts before the first x / *
            ok = v[:k] == b[:k]
        if not ok:
            return False
    return True


# ---------------------------------------------------------------- commands
def cmd_init(a):
    root = a.root
    os.makedirs(dev(root, ""), exist_ok=True)
    if not os.path.isfile(dev(root, "VERSION")):
        open(dev(root, "VERSION"), "w", encoding="utf-8", newline="\n").write(a.version + "\n")
    for name, text in (("CHANGELOG.md", "# Changelog (%s)\n\n## %s\n- initial dev/ folder\n" % (a.name, a.version)),
                       ("BUILD_LOG.md", "# Build log (%s)\n\nWhat was built, why, and how it was tested. `devtrack snapshot` appends a machine line at each snapshot.\n" % a.name)):
        if not os.path.isfile(dev(root, name)):
            open(dev(root, name), "w", encoding="utf-8", newline="\n").write(text)
    if not os.path.isfile(dev(root, "contract.json")):
        write_json(dev(root, "contract.json"), dict(component=a.name, version=a.version, provides={}, requires={}))
    if not os.path.isfile(dev(root, "compat.json")):
        write_json(dev(root, "compat.json"), dict(component=a.name, tested_with={}))
    print("dev/ ready in", root)
    return 0


def diff_manifest(old, new):
    ch = sorted(k for k in new if k in old and old[k] != new[k])
    add = sorted(k for k in new if k not in old)
    rem = sorted(k for k in old if k not in new)
    return add, ch, rem


def cmd_snapshot(a):
    root = a.root
    files = tracked_files(root)
    prev = read_json(dev(root, "manifest.json"), {})
    prev_files = prev.get("files", {})
    comp = (read_json(dev(root, "contract.json"), {}) or {}).get("component") or os.path.basename(os.path.abspath(root))
    man = dict(component=comp, version=component_version(root), taken=time.strftime("%Y-%m-%d %H:%M:%S"), files=files, rollup=rollup(files),
               prev_rollup=prev.get("rollup", CHAIN_ZERO), devtrack_sha256=sha256_file(os.path.abspath(__file__)),
               note=a.note or "")
    man["chain"] = hashlib.sha256((man["prev_rollup"] + man["rollup"]).encode()).hexdigest()
    add, ch, rem = diff_manifest(prev_files, files)
    write_json(dev(root, "manifest.json"), man)
    line = "- %s snapshot v%s rollup=%s prev=%s  +%d ~%d -%d%s\n" % (man["taken"], man["version"], man["rollup"][:16], man["prev_rollup"][:16], len(add), len(ch), len(rem),
                                                                      ("  note: " + a.note) if a.note else "")
    with open(dev(root, "SNAPSHOTS.log"), "a", encoding="utf-8", newline="\n") as f:      # machine lines live outside the hashed set (they describe the hash)
        f.write(line)
    print("snapshot: %d files, rollup %s (+%d ~%d -%d)" % (len(files), man["rollup"], len(add), len(ch), len(rem)))
    return 0


def verify_root(root, quiet=False):
    man = read_json(dev(root, "manifest.json"))
    if not man:
        return False, ["no dev/manifest.json (run snapshot)"]
    probs = []
    now = tracked_files(root)
    add, ch, rem = diff_manifest(man["files"], now)
    probs += ["changed: " + k for k in ch] + ["added (not in manifest): " + k for k in add] + ["missing: " + k for k in rem]
    if rollup(man["files"]) != man["rollup"]:
        probs.append("manifest roll-up does not match its own file list (manifest edited by hand?)")
    if hashlib.sha256((man["prev_rollup"] + man["rollup"]).encode()).hexdigest() != man.get("chain"):
        probs.append("hash-chain link is wrong")
    return not probs, probs


def cmd_verify(a):
    ok, probs = verify_root(a.root)
    if ok:
        print("verify: OK (%s)" % read_json(dev(a.root, "manifest.json"))["rollup"][:16])
        return 0
    print("verify: DRIFT")
    for p in probs[:60]:
        print("  " + p)
    return 1


def cmd_check_compat(a):
    me = read_json(dev(a.root, "contract.json"), {}) or {}
    compat = read_json(dev(a.root, "compat.json"), {}) or {}
    tested = compat.get("tested_with", {})
    bad = 0
    for pr in a.peers:
        pr = os.path.abspath(pr)
        pc = read_json(dev(pr, "contract.json"), {}) or {}
        pname = pc.get("component") or os.path.basename(pr)
        pman = read_json(dev(pr, "manifest.json"), {}) or {}
        pver = component_version(pr)
        ok, probs = verify_root(pr)
        msgs = []
        if not ok:
            msgs.append("peer files do not match the peer's own manifest: " + "; ".join(probs[:3]))
        want = (me.get("requires") or {}).get(pname)
        if want and not satisfies(pver, want):
            msgs.append("peer version %s does not satisfy our requirement %s" % (pver, want))
        mine = (pc.get("requires") or {}).get(me.get("component"))
        if mine and not satisfies(component_version(a.root), mine):
            msgs.append("our version %s does not satisfy the peer's requirement %s" % (component_version(a.root), mine))
        rec = tested.get(pname)
        if rec and rec.get("rollup") != pman.get("rollup"):
            msgs.append("peer changed since the last tested set (tested %s, now %s): re-run the integration test, then record-compat" % ((rec.get("rollup") or "?")[:12], (pman.get("rollup") or "?")[:12]))
        if not rec:
            msgs.append("note: no tested record for this peer yet")
        hard = [m for m in msgs if not m.startswith("note:")]
        print("%-16s v%-8s %s" % (pname, pver, "OK" if not hard else "PROBLEM"))
        for m in msgs:
            print("    " + m)
        bad += bool(hard)
    return 1 if bad else 0


def cmd_record_compat(a):
    compat = read_json(dev(a.root, "compat.json"), {}) or {}
    compat.setdefault("tested_with", {})
    for pr in a.peers:
        pr = os.path.abspath(pr)
        pc = read_json(dev(pr, "contract.json"), {}) or {}
        pname = pc.get("component") or os.path.basename(pr)
        pman = read_json(dev(pr, "manifest.json"), {}) or {}
        compat["tested_with"][pname] = dict(version=component_version(pr), rollup=pman.get("rollup"), at=time.strftime("%Y-%m-%d %H:%M:%S"))
    write_json(dev(a.root, "compat.json"), compat)
    print("recorded:", ", ".join("%s %s" % (k, v["version"]) for k, v in compat["tested_with"].items()))
    a.note = "compat recorded"
    return cmd_snapshot(a)                                    # compat.json is a tracked file: re-snapshot so the manifest matches


def cmd_release(a):
    comps = []
    for r in [a.root] + list(a.peers):
        r = os.path.abspath(r)
        ok, probs = verify_root(r)
        if not ok:
            print("refusing to release: %s has drift: %s" % (r, "; ".join(probs[:3])))
            return 1
        m = read_json(dev(r, "manifest.json"))
        comps.append(dict(component=m["component"], version=m["version"], rollup=m["rollup"], chain=m["chain"], devtrack_sha256=m.get("devtrack_sha256")))
    if len({c["devtrack_sha256"] for c in comps}) > 1:
        print("refusing to release: devtrack.py differs between components:", {c["component"]: c["devtrack_sha256"][:12] for c in comps})
        return 1
    rel = dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), components=comps)
    rel["release_hash"] = hashlib.sha256(json.dumps(comps, sort_keys=True).encode()).hexdigest()
    write_json(os.path.join(a.out, "release.json"), rel)
    print("release.json written; release hash", rel["release_hash"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(HERE))
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("init"); s.add_argument("name"); s.add_argument("version"); s.set_defaults(f=cmd_init)
    s = sp.add_parser("snapshot"); s.add_argument("--note", default=""); s.set_defaults(f=cmd_snapshot)
    s = sp.add_parser("verify"); s.set_defaults(f=cmd_verify)
    s = sp.add_parser("check-compat"); s.add_argument("peers", nargs="+"); s.set_defaults(f=cmd_check_compat)
    s = sp.add_parser("record-compat"); s.add_argument("peers", nargs="+"); s.set_defaults(f=cmd_record_compat)
    s = sp.add_parser("release"); s.add_argument("out"); s.add_argument("peers", nargs="*"); s.set_defaults(f=cmd_release)
    s = sp.add_parser("self-hash"); s.set_defaults(f=lambda a: print(sha256_file(os.path.abspath(__file__))) or 0)
    a = ap.parse_args(argv)
    a.root = os.path.abspath(a.root)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
