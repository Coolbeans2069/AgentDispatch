"""a tool that does not exist yet: first look for one that already does the job (installed, installable or already engineered), and only then have an agent build it. The build is an ordinary agency
job on a seed folder (a spec, a manifest to fill in, a stub and a test it must pass); when the owner accepts it, the finished tool joins the toolbox by itself (see Governor.register_tool)."""
import json, os, re

from ..similarity import similarity
from ..store import BoardError

TEST_PY = '''"""generic acceptance test for an engineered tool: the manifest is valid and every example in examples.json produces its expected output"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
man = json.load(open(os.path.join(HERE, "manifest.json"), encoding="utf-8"))
assert isinstance(man.get("argv"), list) and man["argv"], "manifest.argv must be a command list, e.g. [\\"{python}\\", \\"tool.py\\"]"
assert man.get("description"), "manifest.description is empty"
examples = json.load(open(os.path.join(HERE, "examples.json"), encoding="utf-8"))
assert examples, "examples.json has no examples"
for i, ex in enumerate(examples, 1):
    argv = [a.replace("{python}", sys.executable) for a in man["argv"]]
    if len(argv) > 1 and not os.path.isabs(argv[1]):
        argv[1] = os.path.join(HERE, argv[1])
    p = subprocess.run(argv + [str(a) for a in ex.get("args", [])], input=ex.get("stdin"), capture_output=True, text=True, timeout=60, cwd=HERE)
    assert p.returncode == 0, "example %d exited %s: %s" % (i, p.returncode, p.stderr[-300:])
    want = ex["expect"]
    assert want in p.stdout, "example %d: expected %r in the output, got %r" % (i, want, p.stdout[:300])
print("ok: %d example(s) passed" % len(examples))
'''


def _stems(text):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (text or "").lower())} - {"that", "with", "from", "this", "text", "prin", "resul", "retur", "outpu", "input", "give", "make", "tool", "progr"}


def match_score(description, text):
    """how much of what is asked for the tool's own description covers: shared word stems against the (stemmed) request"""
    d = _stems(description)
    return len(d & _stems(text)) / max(3.0, len(d) * 0.6) if d else 0.0


def find_existing(box, description, threshold=0.6):
    """-> the best matching tool (installed, installable or custom) for a description, or None"""
    best, score = None, 0.0
    for t in box.reg.listing():
        s = max(match_score(description, t["name"] + " " + t["desc"]), similarity(description, t["name"] + " " + t["desc"]))
        if s > score:
            best, score = t, s
    return dict(best, score=round(score, 2)) if best and score >= threshold else None


def need_tool(box, jobs, gov, name, description, examples=None, actor="agent", build_dir=None):
    """-> dict(ok, action: 'exists' | 'install' | 'build', ...). Installing needs the owner's approval; building makes a job."""
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())[:40]
    if len(name) < 2:
        return dict(ok=False, error="bad_name", message="name the tool with 2-40 letters, digits, - or _")
    if not description or len(description) < 12:
        return dict(ok=False, error="bad_description", message="describe what the tool must do (inputs, output) in a sentence or two")
    hit = find_existing(box, description)
    if hit:
        if hit["available"]:
            return dict(ok=True, action="exists", tool=hit, message="%s already does this: use tool_run with tool=%s" % (hit["name"], hit["id"]))
        if hit["installable"]:
            r = box.installer.request(hit["id"], actor, "needed for: " + description[:120])
            return dict(ok=r.get("ok", False), action="install", tool=hit, request=r, message="%s does this and can be installed; %s" % (hit["name"], r.get("message", "")))
    if os.path.isdir(os.path.join(box.reg.custom_dir, name)):
        return dict(ok=False, error="exists", message="a custom tool called %s already exists" % name)
    base = build_dir or os.path.join(box.data, "tools", "build")
    folder = os.path.join(base, name)
    if jobs.target_ok and not jobs.target_ok(folder.replace("\\", "/")):
        return dict(ok=False, error="bad_target", message="the build folder %s is not inside an allowed project folder: add its parent to allowed_roots in nightshift.json (python -m agentdispatch nightshift setup --root ...)" % base)
    os.makedirs(folder, exist_ok=True)
    exs = [e for e in (examples or []) if isinstance(e, dict) and "expect" in e][:10] or [dict(args=[], expect="TODO: replace with a real example the tool must print")]
    files = {"README.md": "# %s\n\n%s\n\nRules: small, no network, no writes outside this folder, deterministic. Print the answer to stdout.\n" % (name, description),
             "manifest.json": json.dumps(dict(id=name, name=name, description=description[:300], argv=["{python}", "tool.py"], lobe="parietal"), indent=1),
             "tool.py": '"""%s"""\nimport sys\n\n\ndef main(argv):\n    raise SystemExit("not written yet")\n\n\nif __name__ == "__main__":\n    main(sys.argv[1:])\n' % description[:200].replace('"', "'"),
             "examples.json": json.dumps(exs, indent=1), "test_tool.py": TEST_PY}
    for fn, text in files.items():
        p = os.path.join(folder, fn)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    try:
        j = jobs.create(actor, "Build tool: %s" % name, "Build the local tool %r. %s\nRead README.md; write tool.py; keep manifest.json valid; make `python test_tool.py` pass using the examples in examples.json." % (name, description),
                        "code", folder.replace("\\", "/"), dict(check="python test_tool.py", rubric="Small, deterministic, no network, no writes outside its folder; the examples pass."), None, 1,
                        dict(template="build-a-tool", role="primary", tool_build=dict(name=name), human_approval="before_promotion", seal=False, stop_on=["check_failed"]))
    except BoardError as e:
        return dict(ok=False, error=e.code if hasattr(e, "code") else "refused", message=str(e))
    return dict(ok=True, action="build", job=j["id"], folder=folder, message="no existing tool does this: job %s will build it; accept it on the Agency page and it joins the toolbox" % j["id"])
