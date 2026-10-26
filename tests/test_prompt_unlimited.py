"""nothing that is sent to a model is cut silently: a long objective, a long review reason, a long tool output and a long prompt reach the model whole
(or, past a real limit, head + tail with a visible marker); a local model's window is sized to its prompt; a reply cut off by the output limit is continued;
each agent is invoked the way its CLI really reads a prompt (stdin, a prompt file, the router, pull)."""
import json, os, shutil, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch import textfit as TF
from agentdispatch.store import Board
from agentdispatch.agency import prompts as PR
from agentdispatch.agency import llmcall as LC
from agentdispatch.agency import profiles as PROF
from agentdispatch.agency.jobs import Jobs, CAPS
from agentdispatch.agency.registry import Registry, DEFAULTS as REG_DEFAULTS
from agentdispatch.agency.realadapters import RealAdapter, cli_argv, prompt_limit, json_unclosed

RECORD = r'''
import sys, json
args = sys.argv[1:]
out = args[0]
if "--prompt-file" in args:
    prompt = open(args[args.index("--prompt-file") + 1], encoding="utf-8").read()
    via = "file"
else:
    prompt = sys.stdin.read()
    via = "stdin"
open(out, "w", encoding="utf-8").write(json.dumps(dict(via=via, prompt=prompt, argv=args)))
mode = args[1] if len(args) > 1 and not args[1].startswith("-") else "answer"
if mode == "cut":
    if "WAS CUT OFF" in prompt:
        print('ole answer", "notes": "continued"}')
    else:
        print('{"answer": "the wh')
else:
    print(json.dumps(dict(answer="done", notes="ok")))
'''


def _script():
    d = tempfile.mkdtemp(prefix="rec_")
    p = os.path.join(d, "rec.py")
    open(p, "w").write(RECORD)
    return d, p


def _text_job(objective, goal="g"):
    return dict(id="J-000001", title="t", goal=goal, kind="general", target="", acceptance={}, meta={}, steps=[dict(n=1, state="todo", objective=objective, needs=["write"], agent=None, evidence="", attempts=0)])


# ------------------------------------------------------------ textfit
def test_fit_keeps_short_text_and_marks_a_real_cut():
    assert TF.fit("abc", 10) == "abc" and TF.fit("x" * 50, None) == "x" * 50
    long = "HEAD" + "x" * 10000 + "TAIL"
    f = TF.fit(long, 2000, "diff")
    assert f.startswith("HEAD") and f.endswith("TAIL") and "omitted here" in f and "of this diff" in f and len(f) <= 2000
    t = TF.tail("a" * 5000 + "END", 100, "check output")
    assert t.endswith("END") and t.startswith("[... first ") and "check output" in t


def test_ollama_window_is_sized_to_the_prompt():
    assert TF.ollama_ctx(1000, 1024) == 8192
    n = TF.ollama_ctx(60000, 4096)                                    # ~20k tokens of prompt
    assert n >= TF.est_tokens(60000) + 4096 and n in (32768, 65536)
    assert TF.ollama_ctx(10 ** 7, 4096) == TF.CTX_MAX


# ------------------------------------------------------------ the 200-character culprit and its neighbours
def test_review_reasons_are_kept_whole_and_all_reach_the_rework_step():
    reasons = ["R%d " % i + "calc.py line %d: the loop must stop at n-1, not n; change range(n+1) to range(n) and add a test for n=0. " % i * 12 for i in range(8)]
    assert all(len(r) > 1000 for r in reasons)
    v = PR.parse_verdict(json.dumps(dict(approve=False, reasons=reasons)))
    assert v["approve"] is False and v["reasons"] == [r.strip() for r in reasons]          # was: 5 reasons of 200 characters
    d = tempfile.mkdtemp(prefix="unl_")
    b = Board(d)
    try:
        jobs = Jobs(b)
        j = jobs.create("dev", "fix", "fix the loop", "general")
        jobs.add_steps(j["id"], [dict(objective="do it", needs=["write"])])
        jobs.rework_step(j["id"], v["reasons"])
        obj = jobs.get(j["id"])["steps"][-1]["objective"]
        assert all(r.strip() in obj for r in reasons), len(obj)                             # was: the first 400 characters
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)


def test_planner_objectives_are_not_cut():
    long = "Edit src/app.py: " + "replace the retry loop with exponential backoff, keep the public API, and make tests/test_app.py pass; " * 80 + "OBJ_END"
    assert 5000 < len(long) < CAPS["objective"]
    steps = PR.parse_steps(json.dumps(dict(steps=[dict(objective=long, needs=["code"], why="w" * 3000)])))
    assert steps[0]["objective"] == long and steps[0].get("why", "w" * 3000) == "w" * 3000
    d = tempfile.mkdtemp(prefix="unl_")
    b = Board(d)
    try:
        jobs = Jobs(b)
        j = jobs.create("dev", "t", "g", "general")
        jobs.add_steps(j["id"], [dict(objective=long, needs=["write"])])
        assert jobs.get(j["id"])["steps"][0]["objective"] == long
        jobs.result_set(j["id"], check_out="o" * 5000 + "CHECK_END")
        assert jobs.get(j["id"])["result"]["check_out"].endswith("CHECK_END")                 # a pull reviewer reads this: it was cut at 1200
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)
    p = PR.plan_prompt(dict(goal="g", kind="general", acceptance={}, title="t", target=""), [], "")
    assert "300 char" not in p and "complete and self-contained" in p


# ------------------------------------------------------------ a 10k-character objective reaches the agent process whole
def test_a_10k_objective_reaches_the_cli_on_stdin_and_in_a_prompt_file():
    obj = "OBJ_START " + "write the section on thermal limits with every number sourced; " * 160 + " OBJ_END"
    assert len(obj) > 10000
    d, rec = _script()
    try:
        ad = RealAdapter(tempfile.mkdtemp(prefix="w_"))
        job = _text_job(obj, goal="GOAL " + "x" * 30000 + " GOAL_END")
        out = os.path.join(d, "stdin.json")
        a = dict(id="claude-code", cost="plan", strengths=["write"], run=dict(argv=[sys.executable, rec, out], stdin_prompt=True, timeout_s=60))
        r = ad.run(a, job["steps"][0], job)
        got = json.load(open(out, encoding="utf-8"))
        assert r.ok, r
        assert got["via"] == "stdin" and obj in got["prompt"] and "GOAL_END" in got["prompt"]
        assert "HOW YOU ARE RUN (claude-code)" in got["prompt"]                               # its own prompting notes, in front
        assert got["prompt"].index("HOW YOU ARE RUN") < got["prompt"].index("OBJ_START") < got["prompt"].index("OBJ_END")      # the notes first, the task after them
        out2 = os.path.join(d, "file.json")
        g = dict(id="grok-cli", cost="cheap", strengths=["write"], run=dict(argv=[sys.executable, rec, out2, "--prompt-file", "{prompt_file}"], stdin_prompt=False, timeout_s=60))
        r = ad.run(g, job["steps"][0], job)
        got = json.load(open(out2, encoding="utf-8"))
        assert r.ok and got["via"] == "file" and obj in got["prompt"] and "GOAL_END" in got["prompt"]
        pf = got["argv"][got["argv"].index("--prompt-file") + 1]
        assert not os.path.exists(pf)                                                          # the temp file is removed after the run
        assert "HOW YOU ARE RUN (grok-cli)" in got["prompt"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------ how each agent is invoked
def test_each_agent_is_invoked_the_way_its_cli_reads_a_prompt():
    A = REG_DEFAULTS
    cl, cx, gm, gk = A["claude-code"]["run"], A["codex-cli"]["run"], A["gemini-cli"]["run"], A["grok-cli"]["run"]
    assert cl["argv"][:2] == ["claude", "-p"] and cl["stdin_prompt"] and "{prompt}" not in cl["argv"]
    assert cx["argv"][:2] == ["codex", "exec"] and cx["argv"][-1] == "-" and cx["stdin_prompt"]
    assert gm["argv"][0] == "gemini" and gm["stdin_prompt"] and "-o" in gm["argv"]
    assert gk["argv"][0] == "grok" and gk["argv"][-2:] == ["--prompt-file", "{prompt_file}"] and not gk["stdin_prompt"] and not gk.get("max_prompt_chars")
    assert A["openrouter"]["run"].get("via") == "router" and A["ollama-local"]["run"].get("via") == "router" and A["pull-agent"]["run"].get("via") == "pull"
    old = dict(argv=["grok", "--tools", "", "--disable-web-search", "-p", "{prompt}"], stdin_prompt=False, max_prompt_chars=24000)   # an old data/agents.json
    assert cli_argv(old) == ["grok", "--tools", "", "--disable-web-search", "--prompt-file", "{prompt_file}"]
    assert prompt_limit(dict(id="grok-cli", run=old)) is None                                # no 24000-character budget any more
    assert cli_argv(dict(old, prompt_file=False))[-1] == "{prompt}" and prompt_limit(dict(id="x", run=dict(old, prompt_file=False))) == 24000
    assert cli_argv(dict(argv=["other", "-p", "{prompt}"])) == ["other", "-p", "{prompt}"]   # only grok is migrated (verified); others are left alone
    seen = []

    class P:
        returncode = 0

        def __init__(self, argv, **kw):
            seen.append(argv)

        def communicate(self, input=None, timeout=None):
            seen.append(input)
            return '{"answer": "a", "notes": "n"}', ""
    ad = RealAdapter(tempfile.mkdtemp(prefix="w_"), popen=P, which=lambda b: "/usr/bin/" + b)
    ad._ask(dict(id="grok-cli", run=dict(old, timeout_s=10)), "PROMPT " * 9000, None)
    assert "--prompt-file" in seen[0] and seen[1] is None and "PROMPT" not in " ".join(seen[0])   # never on the command line, never on stdin
    seen.clear()
    ad._ask(dict(id="codex-cli", run=dict(cx, timeout_s=10)), "P2", "/tmp")
    assert seen[0][-1] == "-" and seen[1] == "P2" and "/tmp" in seen[0]


def test_agent_prompting_notes_come_first_and_the_reply_format_stays_last():
    for aid in ("claude-code", "codex-cli", "gemini-cli", "grok-cli", "openrouter", "ollama-local", "ollama-agent", "pull-agent"):
        for kind in ("step", "review"):
            g = PROF.guide(dict(id=aid), kind)
            assert g.startswith("HOW YOU ARE RUN (%s)" % aid) and "LENGTH:" in g, (aid, kind)
        assert PROF.load()["agents"][aid]["prompt"]["passes"]
    assert PROF.guide(dict(id="nobody"), "step") == "" and PROF.guide(None, "step") == ""
    ad = RealAdapter(tempfile.mkdtemp(prefix="w_"))
    p = ad.plan_request(dict(id="J-1", goal="g", kind="general", acceptance={}, title="t", target=""), dict(id="codex-cli"))
    assert p.startswith("HOW YOU ARE RUN (codex-cli)") and "steps" in p[-3000:]
    assert ad.plan_request(dict(id="J-1", goal="g", kind="general", acceptance={}, title="t", target="")).startswith("HOW YOU ARE RUN") is False


# ------------------------------------------------------------ tool rounds and repairs: whole, valid JSON
def test_tool_round_keeps_the_reply_and_valid_json_results():
    ad = RealAdapter(tempfile.mkdtemp(prefix="w_"))
    reply = '{"tool_calls": [' + ", ".join('{"tool": "calc", "code": "print(%d)"}' % i for i in range(60)) + "]}"
    from agentdispatch.agency.realadapters import _tool_out
    outs = [dict(tool="calc", ok=True, problems=None, secs=0.1, **_tool_out(dict(stdout="line\n" * 5000 + "OUT_END", ok=True))) for _ in range(3)]
    p = ad.tool_round_prompt("BASE", reply, outs)
    shown = p.split("TOOL RESULTS (computed on this computer):\n")[1].split("\n\nNow give your final answer")[0]
    back = json.loads(shown)                                                                    # was: json.dumps(...)[:6000] - invalid JSON, the last results gone
    assert len(back) == 3 and all(o["output"].endswith("OUT_END") for o in back) and "omitted" in back[0]["output"]
    assert reply in p                                                                           # was: its last 1500 characters
    fix = ad.repair_prompt("BASE", "x" * 3000 + "REPLY_END", "no usable {...}")
    assert "REPLY_END" in fix and "as short as" not in fix


# ------------------------------------------------------------ a reply cut off by the output limit is continued
def test_a_reply_cut_off_by_the_output_limit_is_continued():
    full = json.dumps(dict(files={"a.py": "x = 1\n" * 50}, notes="n"))
    calls = []

    def send(p):
        calls.append(p)
        return (full[:100], "length") if len(calls) == 1 else (full[100:], "stop")
    text, n = LC.complete(send, "PROMPT")
    assert n == 1 and json.loads(text) == json.loads(full) and "WAS CUT OFF" in calls[1] and full[:100] in calls[1]

    def restart(p):
        calls.append(p)
        return (full[:100], "length") if "CUT OFF" not in p else ("```json\n" + full + "\n```", "stop")
    calls.clear()
    text, n = LC.complete(restart, "PROMPT")
    assert json.loads(text) == json.loads(full)                                                 # it started over and finished: the complete answer wins
    calls.clear()
    text, n = LC.complete(lambda p: (calls.append(p) or ("{\"a\": \"b", "length")), "PROMPT")
    assert n == LC.ROUNDS and len(calls) == LC.ROUNDS + 1                                       # bounded
    assert json_unclosed('{"a": "}"') and not json_unclosed('{"a": "}"}') and not json_unclosed("no json") and json_unclosed('text {"a": {"b": 1}')
    d, rec = _script()
    try:
        ad = RealAdapter(tempfile.mkdtemp(prefix="w_"))
        out = os.path.join(d, "o.json")
        text, err = ad._ask(dict(id="x", run=dict(argv=[sys.executable, rec, out, "cut"], stdin_prompt=True, timeout_s=60)), "PROMPT", None)
        assert not err and json.loads(text) == dict(answer="the whole answer", notes="continued"), text
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------ local model windows
def test_watchdog_and_orchestrator_size_the_ollama_window_and_send_the_whole_prompt():
    import urllib.request as U
    from agentdispatch.agency import watchdog as WD
    from agentdispatch import orchestrator as ORCH
    sent = []

    class R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    real = U.urlopen

    def fake(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return R(json.dumps(dict(message=dict(content='think first {"action": "wait", "why": "x"}'), done_reason="stop")).encode())
    U.urlopen = fake
    try:
        prompt = "P_START " + "job J-1 is stuck because ... " * 1200 + " P_END"              # ~34k characters: past the old 12000 cut and the 8192-token window
        obj = WD._ask_local({}, prompt)
        b = sent[-1]
        assert obj == dict(action="wait", why="x")
        assert b["messages"][1]["content"] == prompt and b["think"] is False and b["format"] == "json"
        assert b["options"]["num_ctx"] >= TF.est_tokens(len(prompt)) + b["options"]["num_predict"]
        ORCH.ollama_llm("m")("SYS", "U" * 40000)
        b = sent[-1]
        assert b["options"]["num_ctx"] >= TF.est_tokens(40000) and b["options"]["num_ctx"] > 8192
    finally:
        U.urlopen = real


# ------------------------------------------------------------ round 4b: the other model-bound cuts the E2E and the audit found
def test_the_gate_keeps_every_review_reason_whole_for_the_rework_step():
    from agentdispatch.agency import gate as G
    reasons = ["reason %d: " % i + "the handler swallows the error and the test only checks the happy path; " * 20 + "END%d" % i for i in range(7)]
    dec = G.decide(dict(acceptance={}), "a", "b", None, dict(approve=False, reasons=reasons))
    assert not dec["ok"]
    joined = " ".join(dec["reasons"])
    assert all(r in joined for r in reasons)                                            # it was "; ".join(reasons)[:300]
    d = tempfile.mkdtemp(prefix="unl_")
    b = Board(d)
    try:
        jobs = Jobs(b)
        j = jobs.create("dev", "t", "g", "general")
        jobs.add_steps(j["id"], [dict(objective="do x", needs=["write"])])
        n = jobs.rework_step(j["id"], dec["reasons"])
        obj = [s for s in jobs.get(j["id"])["steps"] if s["n"] == n][0]["objective"]
        assert all(r in obj for r in reasons) and len(obj) > 7 * 1000
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)


def test_a_planner_hint_and_why_are_not_cut():
    d = tempfile.mkdtemp(prefix="unl_")
    b = Board(d)
    try:
        jobs = Jobs(b)
        j = jobs.create("dev", "t", "g", "general")
        jobs.add_steps(j["id"], [dict(objective="do x", needs=["write"], hint="h" * 1000 + "HINT_END", why="w" * 1000 + "WHY_END")])
        spec = jobs.get(j["id"])["steps"][0]["spec"]
        assert spec["hint"].endswith("HINT_END") and spec["why"].endswith("WHY_END")    # the hint was cut to 200 characters
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)


def test_night_shift_prompt_keeps_the_issue_body_and_the_dead_ends():
    from agentdispatch import nightshift as NS
    trail = dict(issue=dict(id="P-1", title="t", body="B" * 5000 + "BODY_END"),
                 dead_ends=[dict(attempt="A-1", approach="a" * 1000 + "AP_END", evidence="e" * 1000 + "EV_END")],
                 partial=[dict(attempt="A-2", approach="p" * 1000 + "PART_END")])
    p = NS.propose_prompt(trail, "check: none")
    assert "BODY_END" in p and "AP_END" in p and "EV_END" in p and "PART_END" in p      # they were 900 / 160 characters
    trail["issue"]["body"] = "B" * 50000 + "BODY_END"
    p = NS.propose_prompt(trail, "check: none")
    assert "BODY_END" in p and "characters of this issue omitted" in p                      # past the real limit: head + tail with a marker


def test_prior_attempts_an_agent_reads_are_not_cut():
    d = tempfile.mkdtemp(prefix="unl_")
    b = Board(d)
    try:
        pid = b.post("dev", "issue", "an issue", "body")["id"]
        appr = "switch the cache to an LRU keyed by the request path and invalidate it on write " * 6 + "APPR_END"
        a = b.attempt("a1", pid, appr)
        b.result("a1", a["attempt"], "failed", "x" * 1000 + "EVID_END")
        r = b.attempt("a2", pid, appr + " and also log misses", challenge="the earlier try did not invalidate on delete")
        prior = r.get("similar_earlier") or []
        assert prior and prior[0]["approach"].endswith("APPR_END") and prior[0]["evidence"].endswith("EVID_END")   # they were 200 / 240 characters
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)


def test_a_template_goal_and_a_shadow_goal_are_not_cut():
    from agentdispatch.campaign import templates as T
    long = "the user's own goal text " * 400 + "GOAL_END"
    kw = T.apply(list(T.all_templates())[0], dict(goal=long, gate="g1"))
    assert "GOAL_END" in kw["goal"]                                                    # it was cut at 4000 characters
    src = open(os.path.join(ROOT, "agentdispatch", "campaign", "govern.py"), encoding="utf-8").read()
    assert 'p["goal"][:1500]' not in src


def test_the_planner_refusal_and_the_watchdog_brief_are_not_cut():
    src = open(os.path.join(ROOT, "agentdispatch", "agency", "dispatcher.py"), encoding="utf-8").read()
    assert "have[:600]" not in src                                                     # the planner is told every agent
    assert "res.error[9:300]" not in src and '(res.error or res.text or "failed")[:600]' not in src   # a failed / declined attempt's reason, read by later agents
    w = open(os.path.join(ROOT, "agentdispatch", "agency", "watchdog.py"), encoding="utf-8").read()
    assert '[:80]} for s in (j.get("steps") or [])[:6]' not in w and '_wfit(s.get("objective")' in w


def test_the_container_agent_gets_its_own_notes_and_the_whole_task():
    seen = {}

    class Runner:
        env = None

        def run(self, project, prompt, model, **kw):
            seen["prompt"] = prompt
            return "the whole answer", ""

    class Book:
        class router:
            @staticmethod
            def order(agent, difficulty, kind, attempts):
                return [dict(model="qwen3-coder:30b")]

    ad = RealAdapter(tempfile.mkdtemp(prefix="w_"), None, book=Book())
    ad.agentic = Runner()
    ad.digest = None
    goal = "a long text goal " * 600 + "GOAL_END"
    job = dict(id="J-T", kind="general", target="", goal=goal, acceptance={}, meta={}, steps=[])
    res = ad.run(dict(id="ollama-agent", run=dict(via="agentic", timeout_s=60)), dict(n=1, objective="obj " * 900 + "OBJ_END", attempts=0), job)
    p = seen["prompt"]
    assert res.ok and p.startswith("HOW YOU ARE RUN (ollama-agent)") and "FOR " in p and "LENGTH:" in p
    assert "GOAL_END" in p and "OBJ_END" in p                                          # the goal and the objective reach the container whole


def test_a_worked_attempt_and_a_cli_error_are_not_cut_silently():
    src = open(os.path.join(ROOT, "agentdispatch", "agency", "dispatcher.py"), encoding="utf-8").read()
    assert '"worked", (res.text or "done")[:600]' not in src and '"worked", _fit(res.text or "done", 4000, "result")' in src
    ra = open(os.path.join(ROOT, "agentdispatch", "agency", "realadapters.py"), encoding="utf-8").read()
    assert "_tail(err or out, 3000)" not in ra and '_mtail(ANSI.sub("", err or out or "").strip(), 8000, "error output")' in ra
    assert TF.tail("e" * 9000 + "END", 8000, "error output").startswith("[... first 1003 characters of this error output omitted")


def test_night_shift_keeps_the_model_reply_and_the_check_output_and_a_re_plan_reads_whole_reasons():
    from agentdispatch import nightshift as NS
    import json as _j
    o = NS.NightShift.parse(_j.dumps(dict(approach="a" * 1500 + "APPR_END", analysis="b" * 9000 + "AN_END", expected="c" * 900 + "EXP_END", patch=None)))
    assert o["approach"].endswith("APPR_END") and o["analysis"].endswith("AN_END") and o["expected"].endswith("EXP_END")   # were 600 / 1200 / 300
    src = open(os.path.join(ROOT, "agentdispatch", "nightshift.py"), encoding="utf-8").read()
    assert '(out or "")[-800:]' not in src and "out[-500:]" not in src and '_ns_tail(out or "", 3800, "check output")' in src
    w = open(os.path.join(ROOT, "agentdispatch", "agency", "watchdog.py"), encoding="utf-8").read()
    assert 'why=(r["why"] or "")[:300])' not in w and 'why=_wfit(r["why"] or "", 2000, "reason")' in w
    d = open(os.path.join(ROOT, "agentdispatch", "agency", "dispatcher.py"), encoding="utf-8").read()
    assert '("agent set-up problem, not the step: " + why)[:600]' not in d
    st = open(os.path.join(ROOT, "agentdispatch", "store.py"), encoding="utf-8").read()
    assert '(expected or "")[:300]' not in st


def test_the_triage_list_shows_each_issue_text_whole_or_marked():
    from agentdispatch.orchestrator import triage_prompt
    it = dict(id="P-1", title="t", tags=[], attempts=0, fails=0, body="w " * 400 + "BODY_END")
    assert "BODY_END" in triage_prompt([it])                                               # was cut at 300 characters
    big = triage_prompt([dict(it, body="x" * 5000 + "BIG_END")])
    assert "BIG_END" in big and "characters of this issue text omitted" in big


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
