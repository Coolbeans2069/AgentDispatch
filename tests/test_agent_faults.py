"""an agent whose own set-up is broken (a model name it does not know, a CLI that is missing or logged out, Docker down) is cooled down and the step goes back WITHOUT using an attempt;
pinned-window model names keep the tag a tag; a long run is only kept alive past its time while it still produces something."""
import os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_agency import rig, done
from agentdispatch.agency.adapters import Result
from agentdispatch.agency.realadapters import agent_fault
from agentdispatch.agency.agentic import variant_name, AgenticRunner
from agentdispatch.agency.vram import base_name


def test_config_errors_are_marked_as_agent_faults_and_step_errors_are_not():
    for e in ('exit 1: [claude-code:unrecognized_model] {"model":"nemotron-cascade-2:latest-ctx32k"}', "claude is not installed", "could not start codex: [WinError 2]",
              "exit 1: Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "exit 1: Not logged in. Please run /login", "no local model is usable for ollama-agent right now"):
        assert agent_fault(e).startswith("agent_fault: "), e
    for e in ("exit 1: AssertionError in test_calc.py", "the agent finished without changing any file", "declined: I cannot run commands", "usage limit: try again at 3pm",
              "timed out after 1800 s"):
        assert not agent_fault(e).startswith("agent_fault:"), e


def test_an_agent_fault_cools_the_agent_and_does_not_use_an_attempt():
    r = rig()
    g = r["jobs"].create("dev", "write a haiku", "three lines about rain", "general")
    r["jobs"].add_steps(g["id"], [dict(objective="draft the opening lines about rain on a tin roof", needs=["write"])])
    disp = r["disp"]
    job = r["jobs"].get(g["id"])
    step = job["steps"][0]
    agent = next(iter(r["reg"].agents.values()))
    run_id = r["jobs"].run_started(agent["id"], g["id"], 1)
    att = r["board"].attempt(agent["id"], step.get("post_id") or job["post_id"], step["objective"], expected="step done")
    out = disp._finish_step(job, step, agent, att["attempt"], run_id, Result(ok=False, error='agent_fault: exit 1: [x:unrecognized_model] {"model":"m"}'))
    assert out["action"] == "agent_fault" and out["agent"] == agent["id"], out
    st = r["jobs"].get(g["id"])["steps"][0]
    assert st["attempts"] == 0 and st["state"] == "todo", st
    assert r["jobs"].cooldown_until(agent["id"]) > r["clock"](), "the agent is cooling down"
    row = r["jobs"].db.execute("SELECT outcome FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["outcome"] == "fault"
    done(r)


def test_variant_names_keep_the_tag_and_are_valid():
    assert variant_name("qwen3-coder:30b", 65536) == "qwen3-coder-ctx64k:30b"
    assert variant_name("nemotron-cascade-2:latest", 32768) == "nemotron-cascade-2-ctx32k:latest"
    assert variant_name("nemotron-cascade-2", 32768) == "nemotron-cascade-2-ctx32k:latest"
    assert variant_name("nemotron-cascade-2:latest-ctx32k", 65536) == "nemotron-cascade-2-ctx64k:latest", "an old-style name is re-based, never double-pinned"
    assert variant_name("qwen3-coder-ctx64k:30b", 32768) == "qwen3-coder-ctx32k:30b"
    assert variant_name("Bad Name!:x", 32768) is None
    assert base_name("qwen3-coder-ctx64k:30b") == "qwen3-coder:30b" and base_name("qwen3-coder:30b-ctx64k") == "qwen3-coder:30b"


def test_activity_fingerprint_changes_only_when_the_run_produces_something():
    d = tempfile.mkdtemp()
    a = AgenticRunner._activity(d)
    assert AgenticRunner._activity(d) == a
    open(os.path.join(d, "out.txt"), "w").write("x")
    assert AgenticRunner._activity(d) != a


def test_a_busy_gpu_does_not_keep_a_silent_run_alive():
    import subprocess
    from agentdispatch.agency import vram
    real = vram.gpu_busy
    vram.gpu_busy = lambda min_pct=10: True

    class P:
        returncode = None

        def __init__(self, *a, **k):
            self.n = 0

        def communicate(self, input=None, timeout=None):
            self.n += 1
            raise subprocess.TimeoutExpired("x", timeout)

    class Env:
        caps = None
        gateway = None

        def variant(self, m):
            return m, 32768

        def pack(self, *a):
            return None
    killed = []
    import agentdispatch.agency.agentic as AG
    real_kill, real_run = AG.kill_tree, AG.subprocess.run
    AG.kill_tree = lambda p: killed.append(p)
    AG.subprocess.run = lambda *a, **k: None
    try:
        runner = AgenticRunner(popen=P, which=lambda b: "/bin/" + b)
        runner.env = Env()
        proj = tempfile.mkdtemp()
        out, err = runner.run(proj, "do it", "m:latest", timeout=1)
        assert killed and "timed out" in err and "produced nothing new" in err, err
    finally:
        vram.gpu_busy, AG.kill_tree, AG.subprocess.run = real, real_kill, real_run


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
