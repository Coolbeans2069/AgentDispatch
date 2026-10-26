import json, os, shutil, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentdispatch.toolindex.compact import compact_schema, compact_catalog, first_sentence, risk_of
from agentdispatch.toolindex.build import build_index, save_index, parse_hints, domain_of
from agentdispatch.jsonx import extract_json
from agentdispatch.toolindex.search import Index
from agentdispatch.app import App

FAKE = os.path.join(ROOT, "tests", "fake_backend.py")


def fake_tools():
    import subprocess
    p = subprocess.run([sys.executable, FAKE], input=json.dumps(dict(jsonrpc="2.0", id=1, method="tools/list")) + "\n", capture_output=True, text=True)
    return json.loads(p.stdout.splitlines()[0])["result"]["tools"]


def test_compaction_inlines_refs_and_keeps_property_names():
    schema = {"type": "object", "$schema": "x", "additionalProperties": False,
              "properties": {"description": {"type": "string", "description": "A property literally named description. Second sentence."}, "title": {"type": "string"},
                             "mode": {"$ref": "#/$defs/mode"}}, "required": ["description"],
              "$defs": {"mode": {"type": "string", "enum": ["a", "b"], "description": "Which mode."}}}
    s, level = compact_schema(schema)
    assert level == 0 and "$defs" not in s and "$schema" not in s and "additionalProperties" not in s
    assert set(s["properties"]) == {"description", "title", "mode"}                   # names survive even when they collide with schema keywords
    assert s["properties"]["mode"]["enum"] == ["a", "b"] and s["properties"]["description"]["description"] == "A property literally named description."


def test_budget_cuts_big_schemas_and_flags_them():
    deep = {"type": "object", "properties": {"a%d" % i: {"type": "object", "properties": {"b%d" % j: {"type": "object", "properties": {"c": {"type": "string", "description": "x" * 80}}} for j in range(8)}} for i in range(12)}}
    s, level = compact_schema(deep, budget=1500)
    assert level >= 1 and len(json.dumps(s)) < 1700


def test_catalog_compaction_and_risk():
    tools = fake_tools()
    c, st = compact_catalog(tools)
    assert "outputSchema" not in json.dumps(c) and st["tools"] == 4
    assert c["inspect_thing"]["schema"]["properties"]["mode"]["enum"] == ["fast", "deep"]
    assert c["delete_everything"]["risk"] == "high" and c["inspect_thing"]["risk"] == "low"
    assert first_sentence("One. Two.") == "One."


def test_extract_json_finds_the_answer_after_the_reasoning():
    assert extract_json('{"a": 1}') == {"a": 1}
    reasoning = 'Thinking: the user wants {"ok": true, "n": 3} exactly. Constraint: braces {like this} are fine.\nFinal:\n{"ok": true, "n": 3}'
    assert extract_json(reasoning, "ok") == {"ok": True, "n": 3}
    txt = '*   Plan: return {"tools": []}?\n```json\n{"domain_summary": "x {y}", "tools": [{"name": "a_one", "keywords": ["k\\"q"]}]}\n```'
    assert extract_json(txt, "tools")["tools"][0]["name"] == "a_one" and extract_json(txt, "domain_summary")["domain_summary"] == "x {y}"
    assert extract_json("no json here") is None and extract_json('{"a": 1}', "zzz") is None and extract_json("{unbalanced") is None


def test_parse_hints_validates_against_the_catalog():
    names = ["a_one", "a_two", "b_one"]
    good = json.dumps(dict(domain_summary="tools about a and b", tools=[
        dict(name="a_one", topic="Topic A!", keywords=["Read", "x" * 100, ""], examples=["do x", "do y", "do z", "four"], use_when="when you need a"),
        dict(name="a_two", topic="topic-a", keywords=["write"]), dict(name="invented", keywords=["y"]), dict(name="a_one", topic="dup"), "junk"]))
    h, dsum, probs = parse_hints("reasoning first... " + good, names)
    assert set(h) == {"a_one", "a_two"} and h["a_one"]["topic"] == "topic-a" and h["a_one"]["keywords"] == ["read", "x" * 40] and len(h["a_one"]["examples"]) == 3
    assert dsum == "tools about a and b" and any("unknown tool" in p for p in probs) and any(p.startswith("missing tools: b_one") for p in probs)
    assert parse_hints("not json at all", names)[2] == ["not a JSON object with a tools list"]


class ScriptedHinter:
    def __init__(self, name, fn):
        self.name, self.label, self.fn, self.calls = name, name, fn, 0

    def ask(self, system, user):
        self.calls += 1
        return self.fn(user)


def hints_for(user, who):
    names = [l.split(":")[0] for l in user.splitlines() if ": " in l and not l.startswith(("Tools", "For EVERY", "Also", "Return"))]
    return json.dumps(dict(domain_summary="summary by " + who, tools=[dict(name=n, topic="topic-" + who, keywords=["kw" + n[:3], who], examples=["example for " + n], use_when="when " + n) for n in names]))


def test_build_uses_hinters_in_order_falls_through_and_reuses_hints():
    tools = fake_tools()
    good = ScriptedHinter("strong", lambda u: hints_for(u, "strong"))
    broken = ScriptedHinter("broken", lambda u: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    idx, _ = build_index(tools, "fake", hinters=[broken, good], log=lambda *a: None)
    assert idx["llm"]["model"] == "strong" and idx["llm"]["failed_chunks"] == 0 and any("broken: HTTP 500" in p for p in idx["llm"]["problems"])
    t = idx["tools"]["inspect_thing"]
    assert t["hints"]["keywords"][-1] == "strong" and t["hints"]["by"] == "strong" and t["hints"]["topic"] == "topic-strong" and t["hints"]["examples"] == ["example for inspect_thing"]
    assert any("summary by strong" == g["summary"] for g in idx["groups"].values())
    # a second build reuses the hints of unchanged tools: the (new) model is not asked at all
    again = ScriptedHinter("second", lambda u: hints_for(u, "second"))
    idx2, _ = build_index(tools, "fake", hinters=[again], previous=idx, log=lambda *a: None)
    assert again.calls == 0 and idx2["llm"]["reused"] == len(tools) and idx2["tools"]["inspect_thing"]["hints"]["by"] == "strong"
    # no model reachable: deterministic hints, flagged
    idx3, _ = build_index(tools, "fake", hinters=[broken], log=lambda *a: None)
    assert idx3["llm"]["model"] is None and idx3["llm"]["failed_chunks"] >= 1 and idx3["tools"]["inspect_thing"]["hints"]["by"] == "deterministic"


def test_chunks_run_in_parallel_with_a_pool_and_keep_their_order():
    import threading, time as _t
    tools = fake_tools()
    state = dict(now=0, peak=0)
    lock = threading.Lock()

    def slow(user):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        _t.sleep(0.4)
        with lock:
            state["now"] -= 1
        return hints_for(user, "pool")
    h = ScriptedHinter("pool", slow)
    h.workers = 3
    t0 = _t.time()
    idx, _ = build_index(tools, "fake", hinters=[h], chunk=1, log=lambda *a: None)      # one tool per chunk: 4 chunks
    assert state["peak"] >= 2 and _t.time() - t0 < 4 * 0.4 + 0.5 - 0.2                  # genuinely overlapped (serial would take >= 1.6 s)
    assert idx["llm"]["failed_chunks"] == 0 and all(t["hints"]["by"] == "pool" for t in idx["tools"].values())


def test_save_index_keeps_the_replaced_one_in_history():
    d = tempfile.mkdtemp(prefix="hist_")
    try:
        tools = fake_tools()
        a, sa = build_index(tools, "fake", hinters=[ScriptedHinter("first", lambda u: hints_for(u, "first"))], log=lambda *a: None)
        save_index(d, "fake", a, sa, tools)
        b, sb = build_index(tools, "fake", hinters=[ScriptedHinter("second", lambda u: hints_for(u, "second"))], log=lambda *a: None)
        save_index(d, "fake", b, sb, tools)
        hist = os.listdir(os.path.join(d, "index", "history"))
        assert len(hist) == 1 and hist[0].endswith("first") and os.path.isfile(os.path.join(d, "index", "history", hist[0], "index.json"))
        assert json.load(open(os.path.join(d, "index", "fake", "index.json"), encoding="utf-8"))["llm"]["model"] == "second"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_domains():
    assert domain_of("analyze_javascript_application", "Reconstruct an Electron app") == "javascript-electron"
    assert domain_of("decompile", "Read one part of an app as code") in ("native-binary", "other")
    assert domain_of("inspect_android_package", "apk") == "android"


def test_search_and_describe():
    tools = [dict(name="analyze_javascript_application", description="Reconstruct an Electron or JavaScript app and its IPC channels.", inputSchema={"type": "object", "properties": {"input_path": {"type": "string"}}}),
             dict(name="decompile_function", description="Decompile one native function to pseudocode.", inputSchema={"type": "object", "properties": {"address": {"type": "string"}}}),
             dict(name="capture_browser_scenario", description="Run one browser scenario and capture network requests.", inputSchema={"type": "object", "properties": {}}),
             dict(name="list_android_classes", description="List the classes of an Android APK.", inputSchema={"type": "object", "properties": {}})]
    idx, schemas = build_index(tools, "t", use_llm=False)
    ix = Index(idx, schemas, {t["name"]: t["inputSchema"] for t in tools}, "t")
    assert [r["tool"] for r in ix.search("find the IPC channels of an electron app")][0] == "analyze_javascript_application"
    assert ix.search("pseudocode for a native function")[0]["tool"] == "decompile_function"
    assert ix.search("apk classes")[0]["tool"] == "list_android_classes"
    assert ix.search("zzzz nothing") == []
    assert len(ix.groups()) >= 3 and ix.search("", group=ix.groups()[0]["group"])
    d = ix.describe("decompile_function")
    assert d["schema"]["properties"]["address"]["type"] == "string" and ix.describe("nope") is None


def _app_with_fake_backend(d):
    json.dump({"fake": dict(command=[sys.executable, FAKE], env={"FAKE_COUNT_FILE": os.path.join(d, "crash_count.txt")}, startup_timeout=20, idle_stop_s=0)}, open(os.path.join(d, "backends.json"), "w"))
    tools = fake_tools()
    idx, schemas = build_index(tools, "fake", "9.9", use_llm=False)
    save_index(d, "fake", idx, schemas, tools)
    return App(d)


def test_meta_tools_end_to_end_with_a_backend():
    d = tempfile.mkdtemp(prefix="ti_")
    try:
        app = _app_with_fake_backend(d)
        names = {t.name for t in app.mcp.visible("lite")}
        assert {"tool_search", "tool_describe", "tool_call", "tool_result"} <= names
        sess = dict(profile="full", actor="pull-agent")
        call = lambda n, **a: app.mcp.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name=n, arguments=a)), sess)["result"]["structuredContent"]
        r = call("tool_search", query="inspect a thing")
        assert r["results"][0]["tool"] == "inspect_thing"
        assert call("tool_search")["groups"]
        dsc = call("tool_describe", names=["inspect_thing", "nope"])
        assert dsc["tools"][0]["schema"]["properties"]["mode"]["enum"] == ["fast", "deep"] and dsc["unknown"] == ["nope"]
        ok = call("tool_call", name="inspect_thing", arguments=dict(path="x"))
        assert ok["ok"] and ok["result"]["echo"] == {"path": "x"}
        big = call("tool_call", name="make_big_report", arguments=dict(n=900))
        assert big["ok"] and "result_ref" in big and big["pages"] > 3 and len(big["head"]) <= 1500
        pg = call("tool_result", ref=big["result_ref"], page=2)
        assert pg["ok"] and pg["page"] == 2 and "row" in pg["text"]
        assert call("tool_result", ref="R-../../etc")["error"] == "bad_ref"
        # risky tool: refused, a request lands on the board, approving lets it through
        d1 = call("tool_call", name="delete_everything")
        assert d1["error"] == "approval_required" and d1["request"].startswith("P-")
        d1b = call("tool_call", name="delete_everything")
        assert d1b["request"] == d1["request"]                                          # no duplicate requests
        assert app.board.read(state="open")["posts"][0]["tags"] == ["approval", "high"]
        app.index.approve("delete_everything", "pull-agent", 5)
        assert call("tool_call", name="delete_everything")["ok"]
        assert not call("tool_call", name="delete_everything") is None
        # a backend that dies mid-call is reported, and the next call restarts it
        died = call("tool_call", name="crash_now")
        assert died["ok"] is False and died["error"] == "backend_died"
        assert open(os.path.join(d, "crash_count.txt")).read() == "x"                      # the crashing call ran ONCE: tools/call is never retried automatically
        assert call("tool_call", name="inspect_thing", arguments=dict(path="again"))["ok"]
        assert call("tool_call", name="nope_tool")["error"] == "unknown_tool"
        audit = [json.loads(l) for l in open(os.path.join(d, "audit.jsonl"), encoding="utf-8")]
        assert any(a.get("kind") == "backend-call" and a["tool"] == "fake:inspect_thing" for a in audit)
        # stored results are pruned by age and by total size
        import time as _t
        old = os.path.join(app.index.results_dir, "R-old00000.json")
        open(old, "w").write("x" * 10)
        os.utime(old, (_t.time() - 8 * 86400, _t.time() - 8 * 86400))
        assert app.index.prune_results() >= 1 and not os.path.exists(old)
        assert app.index.prune_results(max_bytes=1) >= 1 and not [f for f in os.listdir(app.index.results_dir) if f.endswith(".json")]
        app.index.backends["fake"].stop()
        app.board.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_extract_json_survives_a_stray_brace_and_local_fallback_is_one_at_a_time():
    txt = 'I should use a { brace in my notes. Final: {"tools": [], "domain_summary": "x"}'
    assert extract_json(txt, "tools") == {"tools": [], "domain_summary": "x"}
    assert extract_json("a { b } c { d", "k") is None
    import threading, time as _t
    import agentdispatch.toolindex.build as B
    from agentdispatch.toolindex.llm import OllamaHinter
    state = dict(now=0, peak=0)
    lock = threading.Lock()
    real = B.ask_ollama

    def fake_ask(model, system, user, **k):
        with lock:
            state["now"] += 1; state["peak"] = max(state["peak"], state["now"])
        _t.sleep(0.15)
        with lock:
            state["now"] -= 1
        return "{}", {}
    B.ask_ollama = fake_ask
    try:
        h = OllamaHinter(["m"])
        ts = [threading.Thread(target=h.ask, args=("s", "u")) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
    finally:
        B.ask_ollama = real
    assert state["peak"] == 1                                                            # three parallel index workers can no longer start three local generations together


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("ALL TOOLINDEX TESTS PASSED")
