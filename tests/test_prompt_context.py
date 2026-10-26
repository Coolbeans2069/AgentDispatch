"""later steps and reviewers see a tool's real output (not its first 600 characters), a reviewer of a code change sees what the steps found, and the honest can't-do example matches the reply shape"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.agency import prompts as PR
from agentdispatch.agency.realadapters import RealAdapter


def test_cant_do_example_matches_the_shape():
    assert '"approve": false' in PR.strict_format('{"approve": true, "reasons": ["r"]}', "{}") and '{"files": {}' not in PR.strict_format('{"approve": true, "reasons": ["r"]}', "{}")
    assert '{"answer": "", "notes": "I cannot do this' in PR.strict_format('{"answer": "<x>", "notes": "n"}', "{}")
    assert '{"files": {}, "notes": "I cannot do this' in PR.strict_format('{"files": {"<p>": "<t>"}, "notes": "n"}', "{}")
    assert '"steps": [' in PR.cant_example('{"steps": []}')


def test_earlier_results_keep_long_tool_output():
    out = "x" * 5000 + "TAIL"
    job = dict(steps=[dict(n=1, state="done", objective="run the tool", evidence=out), dict(n=2, state="todo", objective="write up")])
    txt = PR.earlier_results(job, dict(n=2))
    assert "TAIL" in txt
    assert len(PR.earlier_results(job, dict(n=2), total=3000)) <= 3000 or PR.earlier_results(job, dict(n=2), total=3000) == ""


def test_review_prompt_shows_what_the_steps_found():
    p = PR.review_prompt(dict(goal="g", acceptance={}), "diff --git a b", True, "ok", earlier="STEP 1 (probe) RESULT:\nprinted 42")
    assert "WHAT THE STEPS FOUND" in p and "printed 42" in p
    assert "WHAT THE STEPS FOUND" not in PR.review_prompt(dict(goal="g", acceptance={}), "d", True, "ok")


def test_earlier_budget_respects_a_hard_prompt_limit():
    ad = RealAdapter.__new__(RealAdapter)
    assert ad.earlier_budget(dict(run=dict(max_prompt_chars=32000))) == 8000
    assert ad.earlier_budget(dict(run=dict())) == PR.EARLIER_TOTAL


def test_text_answer_cant_do_is_a_hand_off():
    ad = RealAdapter.__new__(RealAdapter)
    ad.governor = None
    r = ad.step_apply(dict(id="x", cost="cheap", run={}), dict(n=1, objective="o"), dict(id="J-1", kind="general", goal="g", meta={}), '{"answer": "", "notes": "I cannot do this: I would have to run the probe"}')
    assert not r.ok and r.error.startswith("declined:"), r


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
