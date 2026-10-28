import json, os, shutil, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.store import Board
from agentdispatch.orchestrator import Orchestrator, LLMUnavailable, parse_triage, tier_for, in_night_window, TIERS
import time


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def mk():
    d = tempfile.mkdtemp(prefix="orch_")
    c = Clock()
    return Board(d, now=c), c, d


def fake(ids_to_records):
    calls = []

    def llm(system, user):
        calls.append(user)
        return json.dumps(dict(issues=[dict(id=i, **r) for i, r in ids_to_records.items()]))
    llm.calls = calls
    return llm


def test_parse_triage_corrects_and_ignores():
    ids = {"P-000001", "P-000002"}
    txt = json.dumps(dict(issues=[dict(id="P-000001", category="made-up", difficulty=99, tier="gpu", duplicate_of="P-000001", next_step="x" * 500, needs_tool="a tool"),
                                  dict(id="P-999999", category="bug"), dict(id="P-000002", category="bug", difficulty=2, tier="ollama", duplicate_of="P-000001")]))
    r = parse_triage(txt, ids)
    assert set(r) == {"P-000001", "P-000002"}
    assert r["P-000001"]["category"] == "other" and r["P-000001"]["difficulty"] == 5 and r["P-000001"]["tier"] == "mid" and r["P-000001"]["duplicate_of"] is None
    assert len(r["P-000001"]["next_step"]) == 200 and r["P-000002"]["duplicate_of"] == "P-000001"
    assert parse_triage("garbage", ids) == {}


def test_sweep_writes_notes_not_actions_and_does_not_repeat():
    b, c, d = mk()
    a = b.post("grok", "issue", "ComfyUI 8189 hangs", "idle GPU with a prompt running")["id"]
    z = b.post("claude", "issue", "ComfyUI window 8189 hang", "same symptom")["id"]
    o = Orchestrator(b, fake({a: dict(category="environment", difficulty=3, tier="ollama", duplicate_of=None, next_step="check the queue", needs_tool=None),
                              z: dict(category="environment", difficulty=2, tier="strong", duplicate_of=a, next_step="merge", needs_tool="a queue inspector")}), now=c)
    assert set(o.pending()) == {a, z}
    r = o.sweep()
    assert r["triaged"] == 2
    t = b.trail(a)
    assert any(n["kind"] == "status" and "triage" in n["title"] for n in t["threads"])
    assert t["suggestions"][0]["model"] == "local-ollama-night" and t["suggestions"][0]["by"] == "orchestrator"
    tz = b.trail(z)
    assert "Duplicate of %s" % a in tz["threads"][0]["body"] and "Missing tool: a queue inspector" in tz["threads"][0]["body"]
    assert b.stats()["issues"] == {"open": 2}                                      # nothing was closed, claimed or executed
    assert o.pending() == []                                                       # not triaged twice
    c.t += 25 * 3600
    assert set(o.pending()) == {a, z}                                              # the daily interval brings them round again
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_fallback_when_the_first_model_is_unavailable():
    b, c, d = mk()
    a = b.post("x", "issue", "needs triage")["id"]

    def down(system, user):
        raise LLMUnavailable("budget")
    ok = fake({a: dict(category="bug", difficulty=1, tier="ollama", duplicate_of=None, next_step="try it", needs_tool=None)})
    assert Orchestrator(b, down, ok, now=c).sweep()["triaged"] == 1
    fresh = b.post("x", "issue", "a second one needing triage")["id"]
    try:
        Orchestrator(b, down, None, now=c).sweep(only_ids={fresh})
        raise AssertionError("no model at all should raise")
    except LLMUnavailable:
        pass
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_ladder_follows_the_attempt_ledger():
    b, c, d = mk()
    i = b.post("x", "issue", "hard one")["id"]
    assert tier_for(b.trail(i)) == "ollama"
    for appr in ("reinstall the graphics driver from scratch", "downgrade the checkpoint to an older release"):
        a = b.attempt("ollama-night", i, appr)
        assert a["ok"]
        b.result("ollama-night", a["attempt"], "failed", "no")
    assert tier_for(b.trail(i)) == "mid"
    for appr in ("patch the poll loop to ignore socket errors", "move the render queue to another machine"):
        a = b.attempt("gemini-flash", i, appr)
        assert a["ok"]
        b.result("gemini-flash", a["attempt"], "failed", "no")
    assert tier_for(b.trail(i)) == "strong"
    assert TIERS.index(tier_for({"dead_ends": [dict(actor="ollama-night")] * 2 + [dict(actor="gpt-mid")] * 2})) == 2
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_on_job_triggers_an_early_sweep_for_similar_work():
    b, c, d = mk()
    a = b.post("grok", "issue", "mixer exits with code 1 when the flash gate fails", "ad_gyro sys.exit string")["id"]
    other = b.post("grok", "issue", "voice library download is slow")["id"]
    o = Orchestrator(b, fake({a: dict(category="bug", difficulty=2, tier="mid", duplicate_of=None, next_step="exit 2", needs_tool=None)}), now=c)
    r = o.on_job("the mixer exits with code 1 when the flash gate fails again")
    assert r["similar"] == [a] and r["sweep"]["triaged"] == 1
    assert o.on_job("render a pirate music video")["similar"] == []
    assert o.pending() == [other]                                               # only the matching issue was swept early
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_obvious_duplicates_are_linked_even_if_the_model_misses_them():
    b, c, d = mk()
    a = b.post("grok", "issue", "ComfyUI window 8189 hangs with a prompt running", "GPU idle 0% for 15 minutes while the queue shows a running prompt")["id"]
    z = b.post("claude", "issue", "ComfyUI 8189 window hangs: prompt running", "GPU idle 0% for fifteen minutes; the queue shows a running prompt")["id"]
    other = b.post("codex", "issue", "Need a tool to diff two spreadsheet exports", "nothing compares the two price lists")["id"]
    llm = fake({x: dict(category="bug", difficulty=3, tier="mid", duplicate_of=None, next_step="look", needs_tool=None) for x in (a, z, other)})
    r = Orchestrator(b, llm, now=c).sweep()
    assert r["results"][z]["duplicate_of"] == a and r["results"][a]["duplicate_of"] is None and r["results"][other]["duplicate_of"] is None
    assert "text-similar to %s" % a in llm.calls[0]                                # the model was told, too
    assert "Duplicate of %s" % a in b.trail(z)["threads"][0]["body"]
    b.close(); shutil.rmtree(d, ignore_errors=True)


def test_night_window():
    mid = time.mktime((2026, 10, 14, 3, 0, 0, 0, 0, -1))
    noon = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, -1))
    assert in_night_window(mid) and not in_night_window(noon)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL ORCHESTRATOR TESTS PASSED")
