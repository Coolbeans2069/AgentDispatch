import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import devtrack as D


def mk(name, version, files):
    r = tempfile.mkdtemp(prefix="dt_" + name + "_")
    for rel, txt in files.items():
        p = os.path.join(r, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8", newline="\n").write(txt)
    D.main(["--root", r, "init", name, version])
    return r


def run(*a):
    return D.main(list(a))


def test_semver():
    assert D.satisfies("1.2.3", "^1.0.0") and not D.satisfies("2.0.0", "^1.0.0")
    assert D.satisfies("1.2.9", "~1.2.0") and not D.satisfies("1.3.0", "~1.2.0")
    assert D.satisfies("0.3.4", "^0.3.0") and not D.satisfies("0.4.0", "^0.3.0")
    assert D.satisfies("1.5.0", ">=1.0 <2.0") and not D.satisfies("2.0.0", ">=1.0 <2.0")
    assert D.satisfies("1.9.1", "1.x") and not D.satisfies("2.0.0", "1.x")
    assert D.satisfies("3.1.4", "3.1.4") and not D.satisfies("3.1.5", "3.1.4")


def test_snapshot_verify_drift_and_chain():
    r = mk("alpha", "0.1.0", {"a.py": "print(1)\n", "sub/b.txt": "hello\n", "data/ignored.txt": "x", "x.log": "log"})
    assert run("--root", r, "snapshot", "--note", "first") == 0
    m1 = json.load(open(os.path.join(r, "dev", "manifest.json")))
    assert "a.py" in m1["files"] and "sub/b.txt" in m1["files"]
    assert "data/ignored.txt" not in m1["files"] and "x.log" not in m1["files"] and "dev/manifest.json" not in m1["files"]
    assert m1["prev_rollup"] == "0" * 64
    assert run("--root", r, "verify") == 0
    open(os.path.join(r, "a.py"), "w").write("print(2)\n")
    assert run("--root", r, "verify") == 1                               # changed file is detected
    open(os.path.join(r, "new.py"), "w").write("x=1\n")
    ok, probs = D.verify_root(r)
    assert not ok and any(p.startswith("changed: a.py") for p in probs) and any("new.py" in p for p in probs)
    assert run("--root", r, "snapshot") == 0
    m2 = json.load(open(os.path.join(r, "dev", "manifest.json")))
    assert m2["prev_rollup"] == m1["rollup"] and m2["rollup"] != m1["rollup"]       # chained to the previous snapshot
    assert run("--root", r, "verify") == 0
    man = json.load(open(os.path.join(r, "dev", "manifest.json")))
    man["files"]["a.py"] = "0" * 64                                    # tampering with the manifest itself is caught
    json.dump(man, open(os.path.join(r, "dev", "manifest.json"), "w"))
    ok, probs = D.verify_root(r)
    assert not ok and any("roll-up" in p for p in probs)
    shutil.rmtree(r)


def test_compat_and_release():
    a = mk("alpha", "1.0.0", {"a.py": "a\n"})
    b = mk("beta", "2.1.0", {"b.py": "b\n"})
    json.dump(dict(component="alpha", version="1.0.0", provides={}, requires={"beta": "^2.0.0"}), open(os.path.join(a, "dev", "contract.json"), "w"))
    json.dump(dict(component="beta", version="2.1.0", provides={}, requires={"alpha": "^1.0.0"}), open(os.path.join(b, "dev", "contract.json"), "w"))
    run("--root", a, "snapshot"); run("--root", b, "snapshot")
    assert run("--root", a, "check-compat", b) == 0                    # no tested record yet is only a note
    assert run("--root", a, "record-compat", b) == 0
    assert run("--root", a, "check-compat", b) == 0
    open(os.path.join(b, "b.py"), "w").write("changed\n")              # the peer drifts from its own manifest
    assert run("--root", a, "check-compat", b) == 1
    run("--root", b, "snapshot")                                       # new snapshot, but not the tested set any more
    assert run("--root", a, "check-compat", b) == 1
    run("--root", a, "record-compat", b)
    assert run("--root", a, "check-compat", b) == 0
    open(os.path.join(b, "dev", "VERSION"), "w").write("3.0.0\n")      # a major bump breaks the requirement
    run("--root", b, "snapshot")
    assert run("--root", a, "check-compat", b) == 1
    # release needs identical devtrack hashes: both snapshots were made by this same file
    open(os.path.join(b, "dev", "VERSION"), "w").write("2.1.0\n"); run("--root", b, "snapshot")
    out = tempfile.mkdtemp()
    assert run("--root", a, "release", out, b) == 0
    rel = json.load(open(os.path.join(out, "release.json")))
    assert {c["component"] for c in rel["components"]} == {"alpha", "beta"} and len(rel["release_hash"]) == 64
    open(os.path.join(a, "a.py"), "w").write("drift\n")
    assert run("--root", a, "release", out, b) == 1                    # refuses to release a drifting component
    for d in (a, b, out):
        shutil.rmtree(d)


def test_nested_data_dirs_are_tracked_but_runtime_root_dirs_are_not():
    r = mk("gamma", "0.1.0", {"a.py": "a\n", "tests/data/fixture.txt": "f\n", "src/dist/vendored.js": "v\n", "data/runtime.db": "x", "dist/build.zip": "z"})
    files = D.tracked_files(r)
    assert "tests/data/fixture.txt" in files and "src/dist/vendored.js" in files                  # source folders that happen to be called data/dist are covered
    assert "data/runtime.db" not in files and "dist/build.zip" not in files                        # root-level runtime/build folders are not
    shutil.rmtree(r)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL DEVTRACK TESTS PASSED")
