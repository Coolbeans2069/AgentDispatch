"""who plans (strong CLIs in a fixed order, never the free or local models), the planner's per-step agent choice is kept and honoured when that agent is healthy, and a retry skips agents
whose set-up already failed on the step and leans on the agents that actually finish this kind of work."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_agency import rig, done
from agentdispatch.agency.prompts import parse_steps


def test_planners_are_strong_and_ordered():
    r = rig()
    r["reg"].agents["openrouter"] = dict(r["reg"].agents["codex-cli"], id="openrouter", cost="free", strengths=["plan", "code"])
    ids = [a["id"] for a in r["disp"].planners()]
    assert "openrouter" not in ids and not any(a["cost"] in ("free", "local") for a in r["disp"].planners()), ids
    known = [i for i in ids if i in ("claude-code", "codex-cli", "gemini-cli")]
    assert known == [i for i in ("claude-code", "codex-cli", "gemini-cli") if i in known] and ids[:len(known)] == known, ids
    done(r)


def test_parse_steps_keeps_the_agent_tool_and_why():
    st = parse_steps('{"steps": [{"objective": "run the probe tool and record what it prints", "needs": "tool", "agent": "codex-cli", "why": "it can run commands",'
                     ' "tool": {"tool": "calc", "code": "print(1)"}}, {"objective": "write it up", "needs": ["write"], "prefer": "NOT VALID!"}]}')
    assert st[0]["prefer"] == "codex-cli" and st[0]["why"] == "it can run commands" and st[0]["tool"]["tool"] == "calc" and st[0]["needs"] == ["tool"], st
    assert "prefer" not in st[1]


def test_choose_honours_a_healthy_step_prefer_and_skips_infra_failures():
    r = rig()
    j = r["jobs"].create("dev", "write a haiku", "three lines about rain", "general")
    r["jobs"].add_steps(j["id"], [dict(objective="draft the opening lines about rain on a tin roof", needs=["write"], prefer="claude-code", why="best writer")])
    assert r["jobs"].step_spec(j["id"], 1)["prefer"] == "claude-code"
    job = r["jobs"].get(j["id"])
    cands = [a["id"] for a in r["reg"].capable(["write"])]
    agent, _ = r["disp"].choose(job, job["steps"][0])
    if "claude-code" in cands and r["av"].check(r["reg"].get("claude-code"))[0]:
        assert agent["id"] == "claude-code", agent["id"]
    # its set-up failed on this step: the prefer is dropped and it goes last
    run = r["jobs"].run_started("claude-code", j["id"], 1)
    r["jobs"].run_done(run, "fault")
    agent2, _ = r["disp"].choose(job, job["steps"][0])
    assert agent2 is None or agent2["id"] != "claude-code" or len(cands) == 1, agent2
    assert r["disp"]._infra_failed(j["id"], 1) == {"claude-code"}
    done(r)


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
