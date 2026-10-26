"""agent_profiles.json is valid and covers every built-in agent, a data/ override wins, and the planner prompt gets the live AGENTS block (availability, success)"""
import json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from agentdispatch.agency import profiles as PROF
from agentdispatch.agency.registry import DEFAULTS
from test_agency import rig, done


def test_profiles_cover_every_builtin_agent():
    prof = PROF.load()["agents"]
    assert set(DEFAULTS) <= set(prof), set(DEFAULTS) - set(prof)
    assert prof["claude-code"]["can_run_code"] is False and prof["ollama-agent"]["can_run_code"] is True
    assert "planning" in prof["openrouter"]["never"] and "planning" in prof["ollama-local"]["never"]


def test_data_override_wins():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "agent_profiles.json"), "w") as f:
        json.dump({"agents": {"gemini-cli": {"can_read_web": True}}}, f)
    assert PROF.load(d)["agents"]["gemini-cli"]["can_read_web"] is True
    assert PROF.load(d)["agents"]["gemini-cli"]["good_for"]


def test_plan_prompt_gets_the_live_agents_block():
    r = rig()
    r["jobs"].db.execute("CREATE TABLE IF NOT EXISTS route_stats (agent TEXT, kind TEXT, ok INTEGER, n INTEGER, PRIMARY KEY (agent, kind))")
    r["jobs"].db.execute("INSERT INTO route_stats VALUES ('codex-cli','code',7,9)")
    t = PROF.text(r["reg"], r["av"], r["jobs"])
    assert "codex-cli" in t and "finished: code 7/9" in t and ("available" in t)
    fn = getattr(r["disp"].adapter, "profiles_fn", None)
    assert fn and "AGENTS" in fn()
    from agentdispatch.agency.prompts import plan_prompt
    p = plan_prompt(dict(kind="general", goal="g", acceptance={}), [], fn())
    assert "AGENTS" in p and "tool" in p.split("needs is a list from:")[1][:200]
    done(r)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
